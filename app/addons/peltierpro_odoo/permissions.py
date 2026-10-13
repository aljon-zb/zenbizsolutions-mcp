from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import logging
from pathlib import Path
from typing import Any

import yaml
from mcp.server.auth.middleware.auth_context import get_access_token

logger = logging.getLogger("claude_odoo_mcp.permissions")


class PermissionConfigurationError(RuntimeError):
    """Raised when the MCP permission configuration is invalid."""


class PermissionDeniedError(PermissionError):
    """Raised when the authenticated principal cannot use an MCP tool."""


@dataclass(frozen=True)
class Principal:
    key: str
    label: str
    client_id: str | None
    subject: str | None
    email: str | None
    username: str | None


class PermissionManager:
    """
    Config-driven MCP tool authorization.

    Permission patterns support:

      "*"                         -> every MCP tool
      "contacts.*"                -> every Contacts tool
      "crm.*"                     -> every CRM tool
      "sales.get_sales_order"     -> one specific module/tool
      "search_users"              -> one exact tool name

    The current user is resolved from the authenticated MCP access token.

    Supported identity values include:

      - sub
      - user_id
      - email
      - preferred_username
      - username
      - client_id

    For the built-in Odoo OAuth provider, the access token contains the Odoo
    email/login plus a stable subject in the form ``odoo:<user_id>``. The
    nested ``claims["user"]`` fallback is retained for backwards-compatible
    token formats.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        config_file: str,
    ) -> None:
        self.enabled = enabled
        config_path = Path(config_file)

        if config_path.is_absolute():
            self.config_file = config_path
        else:
            app_dir = Path(__file__).resolve().parent
            project_root = app_dir.parent

            candidates = [
                project_root / config_path,
                app_dir / config_path,
            ]

            self.config_file = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.exists()
                ),
                candidates[0],
            )

        self._principals: dict[str, dict[str, Any]] = {}
        self._identifier_index: dict[str, str] = {}
        self._default_permissions: list[str] = []

        if self.enabled:
            self.reload()

    # ------------------------------------------------------------------
    # Configuration loading
    # ------------------------------------------------------------------

    def reload(self) -> None:
        if not self.config_file.exists():
            raise PermissionConfigurationError(
                "Permission file not found: "
                f"{self.config_file.resolve()}"
            )

        with self.config_file.open(
            "r",
            encoding="utf-8",
        ) as handle:
            raw = yaml.safe_load(handle) or {}

        if not isinstance(raw, dict):
            raise PermissionConfigurationError(
                "permissions.yaml root must be a mapping."
            )

        principals = raw.get(
            "principals",
            {},
        )

        defaults = raw.get(
            "defaults",
            {},
        )

        if not isinstance(principals, dict):
            raise PermissionConfigurationError(
                "permissions.yaml: 'principals' must be a mapping."
            )

        if not isinstance(defaults, dict):
            raise PermissionConfigurationError(
                "permissions.yaml: 'defaults' must be a mapping."
            )

        default_permissions = defaults.get(
            "permissions",
            [],
        )

        if not isinstance(default_permissions, list):
            raise PermissionConfigurationError(
                "permissions.yaml: defaults.permissions must be a list."
            )

        identifier_index: dict[str, str] = {}

        for key, config in principals.items():
            if not isinstance(config, dict):
                raise PermissionConfigurationError(
                    f"permissions.yaml: principal '{key}' must be a mapping."
                )

            identifiers = config.get(
                "identifiers",
                [],
            )

            permissions = config.get(
                "permissions",
                [],
            )

            if not isinstance(identifiers, list):
                raise PermissionConfigurationError(
                    f"permissions.yaml: "
                    f"{key}.identifiers must be a list."
                )

            if not isinstance(permissions, list):
                raise PermissionConfigurationError(
                    f"permissions.yaml: "
                    f"{key}.permissions must be a list."
                )

            # Principal key itself may also be used as an identifier.
            all_identifiers = [
                str(key),
                *[
                    str(item)
                    for item in identifiers
                ],
            ]

            for identifier in all_identifiers:
                normalized = self._normalize_identifier(
                    identifier
                )

                if not normalized:
                    continue

                existing = identifier_index.get(
                    normalized
                )

                if (
                    existing is not None
                    and existing != str(key)
                ):
                    raise PermissionConfigurationError(
                        "Duplicate permission identifier "
                        f"'{identifier}' is assigned to both "
                        f"'{existing}' and '{key}'."
                    )

                identifier_index[
                    normalized
                ] = str(key)

        self._principals = principals
        self._identifier_index = identifier_index

        self._default_permissions = [
            str(item)
            for item in default_permissions
        ]

        logger.info(
            "Loaded MCP permissions from %s (%d principals)",
            self.config_file,
            len(self._principals),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_identifier(
        value: str | None,
    ) -> str:
        return (
            value or ""
        ).strip().lower()

    @staticmethod
    def _token_claims(
        token: Any,
    ) -> dict[str, Any]:
        claims = getattr(
            token,
            "claims",
            None,
        )

        if isinstance(
            claims,
            dict,
        ):
            return claims

        return {}

    @staticmethod
    def _first_value(
        *values: Any,
    ) -> str | None:
        for value in values:
            if value is None:
                continue

            text = str(
                value
            ).strip()

            if text:
                return text

        return None

    # ------------------------------------------------------------------
    # Current authenticated user
    # ------------------------------------------------------------------

    def current_principal(
        self,
    ) -> Principal | None:
        token = get_access_token()

        if token is None:
            return None

        claims = self._token_claims(
            token
        )

        # Keep support for token formats that nest identity data under "user".
        user = claims.get(
            "user",
            {},
        )

        if not isinstance(
            user,
            dict,
        ):
            user = {}

        subject = self._first_value(
            getattr(
                token,
                "subject",
                None,
            ),
            claims.get("sub"),
            claims.get("user_id"),
            user.get("user_id"),
            user.get("id"),
        )

        client_id = self._first_value(
            getattr(
                token,
                "client_id",
                None,
            ),
            claims.get("client_id"),
        )

        email = self._first_value(
            claims.get("email"),
            user.get("email"),
        )

        username = self._first_value(
            claims.get(
                "preferred_username"
            ),
            claims.get(
                "username"
            ),
            user.get(
                "preferred_username"
            ),
            user.get(
                "username"
            ),
        )

        candidates = [
            subject,
            email,
            username,
            client_id,
        ]

        matched_key: str | None = None

        for candidate in candidates:
            normalized = self._normalize_identifier(
                candidate
            )

            if (
                normalized
                and normalized
                in self._identifier_index
            ):
                matched_key = (
                    self._identifier_index[
                        normalized
                    ]
                )
                break

        # Authenticated, but not configured
        if matched_key is None:
            fallback = (
                email
                or username
                or subject
                or client_id
                or "unknown"
            )

            return Principal(
                key=fallback,
                label=fallback,
                client_id=client_id,
                subject=subject,
                email=email,
                username=username,
            )

        config = self._principals.get(
            matched_key,
            {},
        )

        return Principal(
            key=matched_key,
            label=str(
                config.get("label")
                or matched_key
            ),
            client_id=client_id,
            subject=subject,
            email=email,
            username=username,
        )

    # ------------------------------------------------------------------
    # Permission resolution
    # ------------------------------------------------------------------

    def _permissions_for_current_principal(
        self,
    ) -> tuple[
        Principal | None,
        list[str],
        bool,
    ]:
        principal = self.current_principal()

        # No authenticated user.
        if principal is None:
            return (
                None,
                self._default_permissions,
                True,
            )

        config = self._principals.get(
            principal.key
        )

        # Authenticated but not configured.
        if config is None:
            return (
                principal,
                self._default_permissions,
                True,
            )

        enabled = bool(
            config.get(
                "enabled",
                True,
            )
        )

        permissions = [
            str(item)
            for item in config.get(
                "permissions",
                [],
            )
        ]

        return (
            principal,
            permissions,
            enabled,
        )

    @staticmethod
    def permission_key(
        module: str,
        tool_name: str,
    ) -> str:
        return (
            f"{module}.{tool_name}"
        )

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    def is_allowed(
        self,
        *,
        module: str,
        tool_name: str,
    ) -> bool:
        # Development behavior:
        # if permission filtering is disabled,
        # every registered tool remains available.
        if not self.enabled:
            return True

        (
            _principal,
            permissions,
            principal_enabled,
        ) = self._permissions_for_current_principal()

        if not principal_enabled:
            return False

        full_key = self.permission_key(
            module,
            tool_name,
        )

        for pattern in permissions:
            pattern = (
                pattern
                .strip()
            )

            if not pattern:
                continue

            # Examples:
            #
            # *
            # contacts.*
            # crm.*
            # sales.get_sales_order
            # search_users

            if fnmatch.fnmatchcase(
                full_key,
                pattern,
            ):
                return True

            if fnmatch.fnmatchcase(
                tool_name,
                pattern,
            ):
                return True

        return False

    def require(
        self,
        *,
        module: str,
        tool_name: str,
    ) -> None:
        if self.is_allowed(
            module=module,
            tool_name=tool_name,
        ):
            return

        principal = self.current_principal()

        identity = (
            principal.label
            if principal
            else "anonymous"
        )

        raise PermissionDeniedError(
            f"MCP access denied for '{identity}': "
            f"tool '{module}.{tool_name}' "
            "is not permitted."
        )

    # ------------------------------------------------------------------
    # Logging / debugging
    # ------------------------------------------------------------------

    def identity_for_log(
        self,
    ) -> dict[str, Any]:
        principal = self.current_principal()

        if principal is None:
            return {
                "principal": None,
                "principal_key": None,
                "client_id": None,
                "subject": None,
                "email": None,
                "username": None,
            }

        return {
            "principal": principal.label,
            "principal_key": principal.key,
            "client_id": principal.client_id,
            "subject": principal.subject,
            "email": principal.email,
            "username": principal.username,
        }