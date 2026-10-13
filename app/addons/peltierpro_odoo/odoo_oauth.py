from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


class OdooAuthorizationCode(AuthorizationCode):
    """Authorization code carrying the authenticated Odoo identity."""

    identity: dict[str, Any]


class OdooRefreshToken(RefreshToken):
    """Refresh token carrying the authenticated Odoo identity."""

    identity: dict[str, Any]


@dataclass
class PendingAuthorization:
    client_id: str
    params: AuthorizationParams
    expires_at: float


class OdooOAuthProvider(
    OAuthAuthorizationServerProvider[
        OdooAuthorizationCode,
        OdooRefreshToken,
        AccessToken,
    ]
):
    """OAuth 2.1 authorization server backed by Odoo user authentication.

    The MCP SDK owns the standards-facing /authorize, /token, /register and
    metadata endpoints. This provider redirects /authorize requests to the MCP's
    Odoo login page, then mints opaque MCP access/refresh tokens after Odoo
    verifies the user's credentials.

    Storage is intentionally in-memory. A server restart invalidates existing
    login sessions/tokens and causes the MCP client to authorize again. This is
    a safe default for a single-instance deployment and can later be replaced by
    Redis/database storage without changing the OAuth surface.
    """

    def __init__(
        self,
        *,
        issuer_url: str,
        resource_server_url: str,
        default_scopes: list[str],
        authorization_code_ttl_seconds: int = 300,
        access_token_ttl_seconds: int = 3600,
        refresh_token_ttl_seconds: int = 2_592_000,
    ) -> None:
        self.issuer_url = issuer_url.rstrip("/")
        self.resource_server_url = resource_server_url.rstrip("/")
        self.default_scopes = list(default_scopes)
        self.authorization_code_ttl_seconds = authorization_code_ttl_seconds
        self.access_token_ttl_seconds = access_token_ttl_seconds
        self.refresh_token_ttl_seconds = refresh_token_ttl_seconds

        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.pending: dict[str, PendingAuthorization] = {}
        self.codes: dict[str, OdooAuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, OdooRefreshToken] = {}

    def _cleanup(self) -> None:
        now = time.time()
        self.pending = {
            key: value
            for key, value in self.pending.items()
            if value.expires_at > now
        }
        self.codes = {
            key: value
            for key, value in self.codes.items()
            if value.expires_at > now
        }
        self.access_tokens = {
            key: value
            for key, value in self.access_tokens.items()
            if value.expires_at is None or value.expires_at > now
        }
        self.refresh_tokens = {
            key: value
            for key, value in self.refresh_tokens.items()
            if value.expires_at is None or value.expires_at > now
        }

    # ------------------------------------------------------------------
    # Dynamic client registration
    # ------------------------------------------------------------------

    async def get_client(
        self,
        client_id: str,
    ) -> OAuthClientInformationFull | None:
        self._cleanup()
        return self.clients.get(client_id)

    async def register_client(
        self,
        client_info: OAuthClientInformationFull,
    ) -> None:
        if not client_info.client_id:
            raise ValueError("OAuth client registration is missing client_id.")
        self.clients[str(client_info.client_id)] = client_info

    # ------------------------------------------------------------------
    # Authorization code flow
    # ------------------------------------------------------------------

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Redirect the browser to the MCP-hosted Odoo sign-in page."""
        self._cleanup()

        if not client.client_id:
            return construct_redirect_uri(
                str(params.redirect_uri),
                error="unauthorized_client",
                state=params.state,
            )

        request_id = secrets.token_urlsafe(32)
        self.pending[request_id] = PendingAuthorization(
            client_id=str(client.client_id),
            params=params,
            expires_at=time.time() + self.authorization_code_ttl_seconds,
        )

        return (
            f"{self.issuer_url}/odoo/login?"
            + urlencode({"request_id": request_id})
        )

    def get_pending(
        self,
        request_id: str,
    ) -> PendingAuthorization | None:
        self._cleanup()
        return self.pending.get(request_id)

    def complete_odoo_login(
        self,
        *,
        request_id: str,
        identity: dict[str, Any],
    ) -> str | None:
        """Create the OAuth code after Odoo has verified the user."""
        self._cleanup()
        pending = self.pending.pop(request_id, None)
        if pending is None:
            return None

        params = pending.params
        scopes = params.scopes or self.default_scopes
        resource = params.resource or self.resource_server_url

        odoo_user_id = identity.get("id") or identity.get("uid")
        subject = f"odoo:{odoo_user_id}" if odoo_user_id is not None else None

        code = OdooAuthorizationCode(
            code=f"code_{secrets.token_urlsafe(32)}",
            client_id=pending.client_id,
            scopes=list(scopes),
            expires_at=time.time() + self.authorization_code_ttl_seconds,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=resource,
            subject=subject,
            identity=dict(identity),
        )
        self.codes[code.code] = code

        return construct_redirect_uri(
            str(params.redirect_uri),
            code=code.code,
            state=params.state,
        )

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> OdooAuthorizationCode | None:
        self._cleanup()
        code = self.codes.get(authorization_code)
        if code is None:
            return None
        if not client.client_id or code.client_id != str(client.client_id):
            return None
        return code

    def _mint_access_token(
        self,
        *,
        client_id: str,
        scopes: list[str],
        resource: str | None,
        subject: str | None,
        identity: dict[str, Any],
    ) -> AccessToken:
        token_value = f"mcp_{secrets.token_urlsafe(48)}"
        now = int(time.time())

        login = identity.get("login") or identity.get("username")
        email = identity.get("email") or login
        claims = {
            "iss": self.issuer_url,
            "sub": subject,
            "odoo_user_id": identity.get("id") or identity.get("uid"),
            "email": email,
            "preferred_username": login,
            "username": login,
            "name": identity.get("name"),
        }

        access = AccessToken(
            token=token_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + self.access_token_ttl_seconds,
            resource=resource or self.resource_server_url,
            subject=subject,
            claims=claims,
        )
        self.access_tokens[token_value] = access
        return access

    def _mint_refresh_token(
        self,
        *,
        client_id: str,
        scopes: list[str],
        resource: str | None,
        subject: str | None,
        identity: dict[str, Any],
    ) -> OdooRefreshToken:
        token_value = f"refresh_{secrets.token_urlsafe(48)}"
        refresh = OdooRefreshToken(
            token=token_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time()) + self.refresh_token_ttl_seconds,
            resource=resource or self.resource_server_url,
            subject=subject,
            identity=dict(identity),
        )
        self.refresh_tokens[token_value] = refresh
        return refresh

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: OdooAuthorizationCode,
    ) -> OAuthToken:
        self._cleanup()

        stored = self.codes.pop(authorization_code.code, None)
        if stored is None:
            raise ValueError("Authorization code is invalid or has already been used.")

        access = self._mint_access_token(
            client_id=stored.client_id,
            scopes=stored.scopes,
            resource=stored.resource,
            subject=stored.subject,
            identity=stored.identity,
        )
        refresh = self._mint_refresh_token(
            client_id=stored.client_id,
            scopes=stored.scopes,
            resource=stored.resource,
            subject=stored.subject,
            identity=stored.identity,
        )

        return OAuthToken(
            access_token=access.token,
            token_type="Bearer",
            expires_in=self.access_token_ttl_seconds,
            refresh_token=refresh.token,
            scope=" ".join(stored.scopes),
        )

    # ------------------------------------------------------------------
    # Refresh / bearer verification / revocation
    # ------------------------------------------------------------------

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> OdooRefreshToken | None:
        self._cleanup()
        token = self.refresh_tokens.get(refresh_token)
        if token is None:
            return None
        if not client.client_id or token.client_id != str(client.client_id):
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: OdooRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        self._cleanup()

        stored = self.refresh_tokens.pop(refresh_token.token, None)
        if stored is None:
            raise ValueError("Refresh token is invalid or has already been used.")

        requested_scopes = scopes or stored.scopes
        if not set(requested_scopes).issubset(set(stored.scopes)):
            raise ValueError("Refresh request contains scopes not originally granted.")

        access = self._mint_access_token(
            client_id=stored.client_id,
            scopes=list(requested_scopes),
            resource=stored.resource,
            subject=stored.subject,
            identity=stored.identity,
        )
        rotated = self._mint_refresh_token(
            client_id=stored.client_id,
            scopes=list(requested_scopes),
            resource=stored.resource,
            subject=stored.subject,
            identity=stored.identity,
        )

        return OAuthToken(
            access_token=access.token,
            token_type="Bearer",
            expires_in=self.access_token_ttl_seconds,
            refresh_token=rotated.token,
            scope=" ".join(requested_scopes),
        )

    async def load_access_token(
        self,
        token: str,
    ) -> AccessToken | None:
        self._cleanup()
        access = self.access_tokens.get(token)
        if access is None:
            return None

        # Resource binding is also enforced by AuthSettings.validate_token_resource.
        if access.resource != self.resource_server_url:
            return None

        return access

    async def revoke_token(
        self,
        token: AccessToken | OdooRefreshToken,
    ) -> None:
        self.access_tokens.pop(token.token, None)
        self.refresh_tokens.pop(token.token, None)

        # Revoke sibling tokens for the same principal/client as well.
        self.access_tokens = {
            value: item
            for value, item in self.access_tokens.items()
            if not (
                item.client_id == token.client_id
                and item.subject == token.subject
            )
        }
        self.refresh_tokens = {
            value: item
            for value, item in self.refresh_tokens.items()
            if not (
                item.client_id == token.client_id
                and item.subject == token.subject
            )
        }
