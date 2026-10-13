from __future__ import annotations

import functools
import inspect
import logging
from pathlib import Path
from typing import Any

from mcp.server.auth.settings import ClientRegistrationOptions

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
    return (
        config['mcp_public_url']
        or config['website_url']
        or f"http://{config['mcp_host']}:{config['mcp_port']}"
    ).rstrip('/')


def configure_server(config) -> dict[str, Any]:
    """Return FastMCP 4 constructor kwargs for the Peltier integration.

    FastMCP 4 accepts an AuthProvider through auth=. It no longer accepts the
    old MCP SDK constructor arguments auth_server_provider or
    transport_security.
    """
    if not config['peltier_enabled']:
        return {}

    settings = PeltierSettings.from_zen_config(config)

    if not settings.auth_enabled:
        return {}

    public_url = _public_url(config)
    resource_url = (
        settings.auth_resource_server_url
        or f"{public_url}/mcp"
    ).rstrip('/')

    provider = OdooOAuthProvider(
        issuer_url=public_url,
        resource_server_url=resource_url,
        default_scopes=settings.auth_required_scopes,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=settings.auth_required_scopes,
            default_scopes=settings.auth_required_scopes,
        ),
        authorization_code_ttl_seconds=settings.auth_code_ttl_seconds,
        access_token_ttl_seconds=settings.auth_access_token_ttl_seconds,
        refresh_token_ttl_seconds=settings.auth_refresh_token_ttl_seconds,
    )

    # Keep a reference so register_integration() can attach the Odoo login route.
    config.options['_peltier_oauth_provider'] = provider

    return {
        "auth": provider,
    }


def register_integration(mcp, config) -> None:
    """Attach Peltier tools, routes and prompts to the ZenMCP server."""
    if not config['peltier_enabled']:
        return

    settings = PeltierSettings.from_zen_config(config)
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

    # Expose the manager for diagnostics and any future global middleware.
    mcp._peltier_permissions = permissions

    def failed(tool: str, exc: Exception, params: dict[str, Any]):
        log_tool(tool, params, success=False, error=str(exc))
        return branded_response({
            "success": False,
            "error": str(exc),
        })

    # Register existing Peltier tools without replacing ZenMCP's registry.
    original_tool = mcp.tool

    if permissions.enabled:

        def permissioned_tool(*tool_args, **tool_kwargs):
            base_decorator = original_tool(*tool_args, **tool_kwargs)

            def decorate(fn):
                module_name = fn.__module__.rsplit('.', 1)[-1]
                tool_name = tool_kwargs.get('name') or fn.__name__

                if inspect.iscoroutinefunction(fn):

                    @functools.wraps(fn)
                    async def guarded(*args, **kwargs):
                        permissions.require(
                            module=module_name,
                            tool_name=tool_name,
                        )
                        return await fn(*args, **kwargs)

                else:

                    @functools.wraps(fn)
                    def guarded(*args, **kwargs):
                        permissions.require(
                            module=module_name,
                            tool_name=tool_name,
                        )
                        return fn(*args, **kwargs)

                return base_decorator(guarded)

            return decorate

        mcp.tool = permissioned_tool

    try:
        for registrar in ALL_REGISTRARS:
            registrar(mcp, odoo, settings, failed)
            _logger.info(
                "Loaded Peltier tool registrar: %s",
                registrar.__name__,
            )
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
        _register_odoo_login_routes(
            mcp,
            odoo,
            oauth_provider,
            config,
        )

    _logger.info("PeltierPro integration registered on ZenMCP")


def _odoo_login_page(
    request_id: str,
    client_name: str,
    error: str | None = None,
) -> str:
    from html import escape

    peltier_logo = (
        "https://assets.swoogo.com/uploads/medium/"
        "6859978-69dfc086ace34.png"
    )
    zenbiz_logo = (
        "https://media.licdn.com/dms/image/v2/"
        "D4D0BAQF9Belq8764Ng/company-logo_200_200/"
        "company-logo_200_200/0/1698295332670"
        "?e=2147483647&v=beta&t=ITxSOPaiCVIQri1EjX782tkZfXRJNnwu0sE4d7mSmxc"
    )

    error_html = ''
    if error:
        error_html = f'''
        <div class="alert-error" role="alert">
            <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 9v4m0 4h.01M10.3 3.9 2.6 17.2A2 2 0 0 0 4.3 20h15.4a2 2 0 0 0 1.7-2.8L13.7 3.9a2 2 0 0 0-3.4 0Z"/>
            </svg>
            <span>{escape(error)}</span>
        </div>
        '''

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authorize {escape(client_name)} MCP</title>
<style>
:root {{
    --blue: #1858d8;
    --blue-soft: #edf4ff;
    --text: #17233d;
    --muted: #667085;
    --border: #dfe7f2;
    --surface: rgba(255, 255, 255, 0.96);
}}
* {{ box-sizing: border-box; }}
html, body {{ min-height: 100%; }}
body {{
    margin: 0;
    min-height: 100vh;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: var(--text);
    background:
        radial-gradient(circle at 16% 78%, rgba(24, 88, 216, .055) 0 95px, transparent 96px),
        radial-gradient(circle at 11% 93%, rgba(24, 88, 216, .045) 0 82px, transparent 83px),
        radial-gradient(circle at 23% 102%, rgba(24, 88, 216, .04) 0 76px, transparent 77px),
        linear-gradient(180deg, #fbfdff 0%, #f5f9ff 100%);
}}
.topbar {{
    height: 64px;
    padding: 0 36px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: rgba(255, 255, 255, .92);
    border-bottom: 1px solid #e8eef6;
    backdrop-filter: blur(10px);
}}
.peltier-logo {{
    display: block;
    max-width: 150px;
    height: 40px;
    object-fit: contain;
    object-position: left center;
}}
.connector-pill {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    min-height: 34px;
    padding: 7px 15px;
    color: #1850bd;
    background: #eff5ff;
    border: 1px solid #e1ebff;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 700;
    white-space: nowrap;
}}
.connector-pill svg {{
    width: 15px;
    height: 15px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
}}
.page {{
    width: 100%;
    max-width: 1060px;
    margin: 0 auto;
    padding: 88px 24px 48px;
}}
.layout {{
    display: flex;
    justify-content: center;
    align-items: stretch;
    gap: 22px;
}}
.card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    box-shadow: 0 18px 50px rgba(32, 54, 90, .07);
}}
.login-card {{ width: 430px; padding: 34px 30px 28px; }}
.info-card {{
    width: 300px;
    padding: 34px 28px;
    display: flex;
    flex-direction: column;
    justify-content: center;
}}
h1 {{
    margin: 0 0 8px;
    font-size: 25px;
    line-height: 1.2;
    letter-spacing: -.025em;
    color: #111c35;
}}
.subtitle {{
    margin: 0 0 22px;
    font-size: 15px;
    line-height: 1.5;
    color: var(--muted);
}}
.alert-error {{
    margin: 0 0 18px;
    padding: 11px 13px;
    display: flex;
    align-items: flex-start;
    gap: 9px;
    border: 1px solid #fecaca;
    border-radius: 10px;
    background: #fff1f2;
    color: #b42318;
    font-size: 13px;
    line-height: 1.45;
}}
.alert-error svg {{
    width: 17px;
    height: 17px;
    flex: 0 0 auto;
    margin-top: 1px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
}}
.field {{ margin-bottom: 16px; }}
.field label {{
    display: block;
    margin-bottom: 7px;
    color: #15203a;
    font-size: 13px;
    font-weight: 700;
}}
.input-wrap {{ position: relative; }}
.field input {{
    width: 100%;
    height: 44px;
    padding: 0 13px;
    border: 1px solid #d6deeb;
    border-radius: 8px;
    outline: none;
    background: #fff;
    color: #182237;
    font-size: 14px;
    transition: border-color .15s ease, box-shadow .15s ease;
}}
.field input::placeholder {{ color: #98a2b3; }}
.field input:focus {{
    border-color: #6f9af0;
    box-shadow: 0 0 0 3px rgba(37, 99, 235, .10);
}}
.password-input {{ padding-right: 44px !important; }}
.eye-button {{
    position: absolute;
    top: 50%;
    right: 10px;
    width: 30px;
    height: 30px;
    padding: 0;
    display: grid;
    place-items: center;
    transform: translateY(-50%);
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: #98a2b3;
    cursor: pointer;
}}
.eye-button:hover {{ background: #f3f6fb; color: #667085; }}
.eye-button svg {{
    width: 18px;
    height: 18px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
}}
.submit-button {{
    width: 100%;
    height: 45px;
    margin-top: 2px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 9px;
    border: 0;
    border-radius: 8px;
    color: #fff;
    background: linear-gradient(90deg, #164fd0 0%, #2265e8 100%);
    box-shadow: 0 8px 18px rgba(29, 78, 216, .20);
    font-size: 14px;
    font-weight: 700;
    cursor: pointer;
    transition: transform .15s ease, box-shadow .15s ease, filter .15s ease;
}}
.submit-button:hover {{
    transform: translateY(-1px);
    filter: brightness(1.02);
    box-shadow: 0 10px 22px rgba(29, 78, 216, .25);
}}
.submit-button svg {{
    width: 18px;
    height: 18px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
}}
.security-note {{
    margin-top: 20px;
    padding-top: 18px;
    display: flex;
    gap: 10px;
    border-top: 1px solid #edf1f6;
    color: #7b879b;
    font-size: 12px;
    line-height: 1.5;
}}
.security-note svg {{
    width: 17px;
    height: 17px;
    flex: 0 0 auto;
    margin-top: 1px;
    fill: currentColor;
}}
.powered {{
    margin-top: 19px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 9px;
    color: #7b879b;
    font-size: 12px;
}}
.powered strong {{ color: #44516a; font-weight: 700; }}
.zenbiz-logo {{
    width: 28px;
    height: 28px;
    object-fit: contain;
    border-radius: 50%;
    background: #fff;
}}
.feature {{ display: flex; gap: 14px; }}
.feature + .feature {{ margin-top: 31px; }}
.feature-icon {{
    width: 45px;
    height: 45px;
    flex: 0 0 auto;
    display: grid;
    place-items: center;
    border-radius: 13px;
    background: var(--blue-soft);
    color: #1e5bdd;
}}
.feature-icon svg {{
    width: 23px;
    height: 23px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.9;
    stroke-linecap: round;
    stroke-linejoin: round;
}}
.feature h2 {{
    margin: 1px 0 6px;
    color: #17233d;
    font-size: 14px;
    line-height: 1.25;
}}
.feature p {{
    margin: 0;
    color: #6f7c91;
    font-size: 12px;
    line-height: 1.45;
}}
@media (max-width: 820px) {{
    .page {{ padding-top: 42px; }}
    .layout {{ flex-direction: column; align-items: center; }}
    .login-card, .info-card {{ width: min(100%, 480px); }}
}}
@media (max-width: 520px) {{
    .topbar {{ height: 60px; padding: 0 16px; }}
    .peltier-logo {{ max-width: 120px; height: 34px; }}
    .connector-pill {{ padding: 7px 11px; font-size: 12px; }}
    .page {{ padding: 28px 14px 32px; }}
    .login-card {{ padding: 27px 20px 23px; }}
    .info-card {{ padding: 27px 20px; }}
    h1 {{ font-size: 22px; }}
}}
</style>
</head>
<body>
<header class="topbar">
    <img class="peltier-logo" src="{peltier_logo}" alt="PeltierPro" referrerpolicy="no-referrer">
    <div class="connector-pill">
        <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M8 12h8M9 7v3m6-3v3M8 14v2a4 4 0 0 0 8 0v-2M7 7h2m6 0h2"/>
        </svg>
        MCP Connector
    </div>
</header>

<main class="page">
    <div class="layout">
        <section class="card login-card">
            <h1>Connect to Peltier MCP</h1>
            <p class="subtitle">Sign in with your Odoo account to authorize Claude access.</p>
            {error_html}
            <form method="post" action="/odoo/login" autocomplete="on">
                <input type="hidden" name="request_id" value="{escape(request_id)}">
                <div class="field">
                    <label for="odoo-login">Odoo login</label>
                    <div class="input-wrap">
                        <input id="odoo-login" name="login" type="text" autocomplete="username" placeholder="Enter your Odoo login" required autofocus>
                    </div>
                </div>
                <div class="field">
                    <label for="odoo-password">Password</label>
                    <div class="input-wrap">
                        <input id="odoo-password" class="password-input" name="password" type="password" autocomplete="current-password" placeholder="Enter your password" required>
                        <button class="eye-button" type="button" id="toggle-password" aria-label="Show password" title="Show password">
                            <svg viewBox="0 0 24 24" aria-hidden="true">
                                <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/>
                                <circle cx="12" cy="12" r="2.5"/>
                            </svg>
                        </button>
                    </div>
                </div>
                <button class="submit-button" type="submit">
                    Continue with Odoo
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
                </button>
            </form>
            <div class="security-note">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M7 10V7a5 5 0 0 1 10 0v3h1a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h1Zm2 0h6V7a3 3 0 0 0-6 0v3Z"/>
                </svg>
                <span>Your password is sent to your configured Odoo server only for authentication and is not stored by this MCP service.</span>
            </div>
            <div class="powered">
                <span>Powered by:</span>
                <img class="zenbiz-logo" src="{zenbiz_logo}" alt="Zen Business Solutions" referrerpolicy="no-referrer">
                <strong>Zen Business Solutions</strong>
            </div>
        </section>

        <aside class="card info-card">
            <div class="feature">
                <div class="feature-icon">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 5 6v5c0 4.6 2.8 8 7 10 4.2-2 7-5.4 7-10V6l-7-3Z"/></svg>
                </div>
                <div>
                    <h2>Secure authentication</h2>
                    <p>Your credentials are used securely to authenticate with your Odoo instance.</p>
                </div>
            </div>
            <div class="feature">
                <div class="feature-icon">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 7 4-7 4-7-4 7-4Z"/><path d="m5 7 7 4 7-4v9l-7 4-7-4V7Z"/></svg>
                </div>
                <div>
                    <h2>Tool permissions</h2>
                    <p>Claude can use approved tools to help you work with your Peltier data in Odoo.</p>
                </div>
            </div>
            <div class="feature">
                <div class="feature-icon">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 20v-1.5a4.5 4.5 0 0 0-4.5-4.5h-5A4.5 4.5 0 0 0 2 18.5V20"/><circle cx="9" cy="7" r="4"/><path d="M17 11a4 4 0 0 0 0-8M22 20v-1.5a4.5 4.5 0 0 0-3.3-4.3"/></svg>
                </div>
                <div>
                    <h2>Connected workspace</h2>
                    <p>Access your Peltier data and workflows directly from Claude.</p>
                </div>
            </div>
        </aside>
    </div>
</main>

<script>
(function () {{
    const password = document.getElementById('odoo-password');
    const toggle = document.getElementById('toggle-password');
    if (!password || !toggle) return;
    toggle.addEventListener('click', function () {{
        const showing = password.type === 'text';
        password.type = showing ? 'password' : 'text';
        toggle.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
        toggle.setAttribute('title', showing ? 'Show password' : 'Hide password');
    }});
}})();
</script>
</body>
</html>'''


def _register_odoo_login_routes(
    mcp,
    odoo,
    oauth_provider,
    config,
) -> None:

    @mcp.custom_route('/odoo/login', methods=['GET', 'POST'])
    async def odoo_login(request):
        from urllib.parse import parse_qs

        from starlette.responses import HTMLResponse, RedirectResponse

        if request.method == 'GET':
            request_id = request.query_params.get('request_id', '')

            if (
                not request_id
                or oauth_provider.get_pending(request_id) is None
            ):
                return HTMLResponse(
                    'This authorization request is missing or has expired. '
                    'Reconnect the MCP.',
                    status_code=400,
                )

            return HTMLResponse(
                _odoo_login_page(
                    request_id,
                    config['client_name'] or 'Peltier Pro',
                )
            )

        raw_body = (
            await request.body()
        ).decode('utf-8', errors='replace')

        form = parse_qs(
            raw_body,
            keep_blank_values=True,
        )

        request_id = (form.get('request_id') or [''])[0]
        login = (form.get('login') or [''])[0].strip()
        password = (form.get('password') or [''])[0]

        if (
            not request_id
            or oauth_provider.get_pending(request_id) is None
        ):
            return HTMLResponse(
                'This authorization request is missing or has expired. '
                'Reconnect the MCP.',
                status_code=400,
            )

        if not login or not password:
            return HTMLResponse(
                _odoo_login_page(
                    request_id,
                    config['client_name'] or 'Peltier Pro',
                    'Enter your Odoo login and password.',
                ),
                status_code=400,
            )

        try:
            identity = await odoo.authenticate_user(
                login=login,
                password=password,
            )
        except OdooAPIError:
            _logger.warning(
                'Odoo MCP authorization login failed for submitted login.'
            )

            return HTMLResponse(
                _odoo_login_page(
                    request_id,
                    config['client_name'] or 'Peltier Pro',
                    'Odoo sign-in failed. Check your login and password.',
                ),
                status_code=401,
            )

        redirect_to = oauth_provider.complete_odoo_login(
            request_id=request_id,
            identity=identity,
        )

        if not redirect_to:
            return HTMLResponse(
                'This authorization request has expired. Reconnect the MCP.',
                status_code=400,
            )

        return RedirectResponse(
            redirect_to,
            status_code=302,
        )
