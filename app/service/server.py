import logging
from pathlib import Path
from typing import Any

from app.core import config, registry
from mcp.server.mcpserver import Icon
from fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl
from app.oauth import JWKSJWTTokenVerifier

_logger = logging.getLogger(__name__)


def setup_logging():
    logging.basicConfig(
        level=getattr(logging, config['log_level'], logging.INFO),
        format=f"%(asctime)s %(levelname)s [{config['client_id']}] %(name)s: %(message)s",
    )


def _peltier_hooks():
    if not config['peltier_enabled']:
        return None
    from app.addons.peltierpro_odoo import configure_server, register_integration
    return configure_server, register_integration


def server_args():
    icons = []
    if config['icon_url']:
        icons.append(Icon(src=config['icon_url'], mimeType="image/png", sizes=["128x128"]))
    elif config['peltier_enabled']:
        public = (config['mcp_public_url'] or config['website_url'] or '').rstrip('/')
        if public:
            icons.append(Icon(src=f"{public}/assets/zenbiz-icon.png", mimeType="image/png", sizes=["128x128"]))

    instructions = f"""
    You are the Odoo MCP integration for {config['client_name']},
    provided by {config['provider_name']}.

    Only operate against the Odoo environment configured for this MCP instance.
    Never assume data from another client/container.
    Respect tool-level safeguards and confirmation requirements for write actions.
    """.strip()

    mcp_kwargs: dict[str, Any] = {}

    # Preserve ZenMCP's external-JWKS OAuth mode when the Peltier addon is not
    # providing the authorization server itself.
    if config['auth_enabled'] and not config['peltier_enabled']:
        mcp_kwargs["token_verifier"] = JWKSJWTTokenVerifier(
            issuer=config['auth_issuer_url'],
            audience=config['auth_audience'],
            jwks_url=config['auth_jwks_url'],
            required_scopes=config['auth_required_scopes'],
            algorithms=config['auth_algorithms'],
        )
        mcp_kwargs["auth"] = AuthSettings(
            issuer_url=AnyHttpUrl(config['auth_issuer_url']),
            resource_server_url=AnyHttpUrl(config['auth_resource_server_url']),
            required_scopes=config['auth_required_scopes'],
        )

    hooks = _peltier_hooks()
    if hooks:
        configure_server, _ = hooks
        mcp_kwargs.update(configure_server(config))

    fastmcp_args: dict[str, Any] = {"instructions": instructions, **mcp_kwargs}
    if config['website_url']:
        fastmcp_args["website_url"] = config['website_url']
    elif config['mcp_public_url']:
        fastmcp_args["website_url"] = f"{config['mcp_public_url'].rstrip('/')}/mcp"
    if icons:
        fastmcp_args["icons"] = icons
    return fastmcp_args


mcp_server = None


def start():
    global mcp_server
    mcp_server = FastMCP(config['service_name'], **server_args())

    # Original ZenMCP addon discovery/registry remains intact.
    registry.load_registry(mcp_server)

    # Peltier is layered on after the normal Zen registry has loaded.
    hooks = _peltier_hooks()
    if hooks:
        _, register_integration = hooks
        register_integration(mcp_server, config)

    @mcp_server.custom_route("/", methods=["GET"])
    async def home(request):
        from starlette.responses import JSONResponse
        return JSONResponse({
            "status": "online",
            "service": config['service_name'],
            "provider": config['provider_name'],
            "client_id": config['client_id'],
            "client": config['client_name'],
            "access": config['access_mode'],
            "mcp_endpoint": "/mcp",
            "health_endpoint": "/health",
            "oauth_enabled": config['auth_enabled'],
            "peltier_enabled": config['peltier_enabled'],
        })

    @mcp_server.custom_route("/health", methods=["GET"])
    async def health(request):
        from starlette.responses import JSONResponse
        return JSONResponse({
            "status": "ok",
            "service": config['service_name'],
            "client_id": config['client_id'],
            "client": config['client_name'],
            "provider": config['provider_name'],
            "transport": config['mcp_transport'],
            "oauth_enabled": config['auth_enabled'],
            "peltier_enabled": config['peltier_enabled'],
            "permissions_enabled": config['permissions_enabled'],
        })

    return mcp_server


def run():
    rc = 0
    try:
        _logger.info(
            "Starting %s for client=%s on %s:%s",
            config['service_name'], config['client_id'], config['mcp_host'], config['mcp_port'],
        )
        mcp_params = {'transport': config['mcp_transport']}
        if config['mcp_transport'] == "stdio":
            mcp_server.run(**mcp_params)
        else:
            mcp_params.update({'host': config['mcp_host'], 'port': config['mcp_port']})
            mcp_server.run(**mcp_params)
    except Exception as e:
        _logger.exception(e)
        rc = -1
    return rc
