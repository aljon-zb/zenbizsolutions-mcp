from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from app.addons.peltierpro_odoo.audit import log_tool
from app.addons.peltierpro_odoo.branding import branded_response
from app.addons.peltierpro_odoo.odoo_client import OdooAPIError, OdooClient
from app.addons.peltierpro_odoo.odoo_oauth import OdooOAuthProvider
from app.addons.peltierpro_odoo.permissions import PermissionManager
from app.addons.peltierpro_odoo.prompts import register_prompts
from app.addons.peltierpro_odoo.runtime import PeltierSettings
from app.addons.peltierpro_odoo.tools import ALL_REGISTRARS

_logger = logging.getLogger(__name__)


def _public_url(config) -> str:
    return (config['mcp_public_url'] or config['website_url'] or f"http://{config['mcp_host']}:{config['mcp_port']}").rstrip('/')


def configure_server(config) -> dict[str, Any]:
    """Return FastMCP kwargs contributed by the Peltier integration.

    This hook runs before ZenMCP creates FastMCP, preserving app.service.server
    as the owner of server construction.
    """
    if not config['peltier_enabled']:
        return {}

    settings = PeltierSettings.from_zen_config(config)
    public_url = _public_url(config)
    parsed = urlparse(public_url)
    resource_url = (settings.auth_resource_server_url or f"{public_url}/mcp").rstrip('/')

    kwargs: dict[str, Any] = {
        "stateless_http": True,
        "json_response": True,
        "transport_security": TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=config['allowed_hosts'] or [
                parsed.hostname or "localhost",
                f"{parsed.hostname or 'localhost'}:*",
                "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*",
            ],
            allowed_origins=config['allowed_origins'] or [
                f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else public_url,
                "http://localhost:*", "http://127.0.0.1:*",
            ],
        ),
    }

    if settings.auth_enabled:
        provider = OdooOAuthProvider(
            issuer_url=public_url,
            resource_server_url=resource_url,
            default_scopes=settings.auth_required_scopes,
            authorization_code_ttl_seconds=settings.auth_code_ttl_seconds,
            access_token_ttl_seconds=settings.auth_access_token_ttl_seconds,
            refresh_token_ttl_seconds=settings.auth_refresh_token_ttl_seconds,
        )
        kwargs["auth_server_provider"] = provider
        kwargs["auth"] = AuthSettings(
            issuer_url=AnyHttpUrl(public_url),
            resource_server_url=AnyHttpUrl(resource_url),
            required_scopes=settings.auth_required_scopes,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=settings.auth_required_scopes,
                default_scopes=settings.auth_required_scopes,
            ),
            validate_token_resource=True,
        )
        # Stored on config so register_integration can attach the login route.
        config.options['_peltier_oauth_provider'] = provider

    return kwargs


def register_integration(mcp, config) -> None:
    """Attach Peltier tools/routes/prompts to the already-created ZenMCP server."""
    if not config['peltier_enabled']:
        return

    settings = PeltierSettings.from_zen_config(config)
    public_url = _public_url(config)
    oauth_provider = config.options.get('_peltier_oauth_provider')

    odoo = OdooClient(
        base_url=settings.odoo_url,
        database=settings.odoo_database,
        api_key=settings.odoo_api_key,
        timeout_seconds=settings.request_timeout_seconds,
    )

    permissions = PermissionManager(
        enabled=settings.permissions_enabled,
        config_file=settings.permissions_file,
    )
    # Expose the manager for a future/global ZenMCP permission middleware and
    # for diagnostics. Tool-level checks are also wrapped below.
    mcp._peltier_permissions = permissions

    def failed(tool: str, exc: Exception, params: dict[str, Any]):
        log_tool(tool, params, success=False, error=str(exc))
        return branded_response({"success": False, "error": str(exc)})

    # Register existing Peltier tool modules without moving ZenMCP's registry,
    # CLI, service or core modules. While registering, wrap tool execution with
    # the original mcp-peltier PermissionManager so permission rules remain
    # effective even though ZenMCP owns the FastMCP instance.
    original_tool = mcp.tool

    if permissions.enabled:
        import functools
        import inspect

        def permissioned_tool(*tool_args, **tool_kwargs):
            base_decorator = original_tool(*tool_args, **tool_kwargs)

            def decorate(fn):
                module_name = fn.__module__.rsplit('.', 1)[-1]
                tool_name = tool_kwargs.get('name') or fn.__name__

                if inspect.iscoroutinefunction(fn):
                    @functools.wraps(fn)
                    async def guarded(*args, **kwargs):
                        permissions.require(module=module_name, tool_name=tool_name)
                        return await fn(*args, **kwargs)
                else:
                    @functools.wraps(fn)
                    def guarded(*args, **kwargs):
                        permissions.require(module=module_name, tool_name=tool_name)
                        return fn(*args, **kwargs)

                return base_decorator(guarded)

            return decorate

        mcp.tool = permissioned_tool

    try:
        for registrar in ALL_REGISTRARS:
            registrar(mcp, odoo, settings, failed)
            _logger.info("Loaded Peltier tool registrar: %s", registrar.__name__)
    finally:
        mcp.tool = original_tool

    register_prompts(mcp)

    @mcp.custom_route('/assets/zenbiz-icon.png', methods=['GET'])
    async def zenbiz_icon(request):
        from starlette.responses import FileResponse
        return FileResponse(
            Path(__file__).resolve().parent / 'assets' / 'zenbiz-icon.png',
            media_type='image/png',
        )

    if settings.auth_enabled and oauth_provider is not None:
        _register_odoo_login_routes(mcp, odoo, oauth_provider, config)

    _logger.info("PeltierPro integration registered on ZenMCP")


def _odoo_login_page(request_id: str, client_name: str, error: str | None = None) -> str:
    from html import escape
    error_html = ''
    if error:
        error_html = (
            '<div style="margin:0 0 18px;padding:12px 14px;border-radius:8px;'
            'background:#fff1f0;color:#8a1f17;border:1px solid #ffccc7;">'
            f'{escape(error)}</div>'
        )
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authorize {escape(client_name)} MCP</title></head>
<body style="margin:0;background:#f7f7f8;font-family:Arial,sans-serif;color:#222;">
<main style="max-width:420px;margin:64px auto;padding:0 18px;"><section style="background:white;border:1px solid #e5e5e5;border-radius:12px;padding:28px;box-shadow:0 6px 24px rgba(0,0,0,.05);">
<h1 style="margin:0 0 8px;font-size:23px;">Sign in with Odoo</h1>
<p style="margin:0 0 22px;color:#666;line-height:1.45;">Use your existing Odoo account to authorize access to the {escape(client_name)} MCP.</p>
{error_html}<form method="post" action="/odoo/login" autocomplete="on"><input type="hidden" name="request_id" value="{escape(request_id)}">
<label style="display:block;margin-bottom:7px;font-weight:600;">Odoo login</label><input name="login" type="text" autocomplete="username" required style="box-sizing:border-box;width:100%;padding:11px 12px;margin-bottom:16px;border:1px solid #ccc;border-radius:7px;">
<label style="display:block;margin-bottom:7px;font-weight:600;">Password</label><input name="password" type="password" autocomplete="current-password" required style="box-sizing:border-box;width:100%;padding:11px 12px;margin-bottom:20px;border:1px solid #ccc;border-radius:7px;">
<button type="submit" style="width:100%;padding:11px 14px;border:0;border-radius:7px;background:#714b67;color:white;font-weight:700;cursor:pointer;">Continue with Odoo</button></form>
<p style="font-size:12px;color:#777;line-height:1.45;margin:18px 0 0;">Your password is sent to Odoo only for authentication and is not stored by this MCP service.</p>
</section></main></body></html>'''


def _register_odoo_login_routes(mcp, odoo, oauth_provider, config) -> None:
    @mcp.custom_route('/odoo/login', methods=['GET', 'POST'])
    async def odoo_login(request):
        from urllib.parse import parse_qs
        from starlette.responses import HTMLResponse, RedirectResponse

        if request.method == 'GET':
            request_id = request.query_params.get('request_id', '')
            if not request_id or oauth_provider.get_pending(request_id) is None:
                return HTMLResponse('This authorization request is missing or has expired. Reconnect the MCP.', status_code=400)
            return HTMLResponse(_odoo_login_page(request_id, config['client_name'] or 'Peltier Pro'))

        raw_body = (await request.body()).decode('utf-8', errors='replace')
        form = parse_qs(raw_body, keep_blank_values=True)
        request_id = (form.get('request_id') or [''])[0]
        login = (form.get('login') or [''])[0].strip()
        password = (form.get('password') or [''])[0]

        if not request_id or oauth_provider.get_pending(request_id) is None:
            return HTMLResponse('This authorization request is missing or has expired. Reconnect the MCP.', status_code=400)
        if not login or not password:
            return HTMLResponse(_odoo_login_page(request_id, config['client_name'] or 'Peltier Pro', 'Enter your Odoo login and password.'), status_code=400)
        try:
            identity = await odoo.authenticate_user(login=login, password=password)
        except OdooAPIError:
            _logger.warning('Odoo MCP authorization login failed for submitted login.')
            return HTMLResponse(_odoo_login_page(request_id, config['client_name'] or 'Peltier Pro', 'Odoo sign-in failed. Check your login and password.'), status_code=401)

        redirect_to = oauth_provider.complete_odoo_login(request_id=request_id, identity=identity)
        if not redirect_to:
            return HTMLResponse('This authorization request has expired. Reconnect the MCP.', status_code=400)
        return RedirectResponse(redirect_to, status_code=302)
