import logging
from typing import Any
from urllib.parse import urlparse

from app.core import config, registry
from fastmcp import FastMCP
from mcp.server.mcpserver import Icon

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


def server_args() -> dict[str, Any]:
    icons = []

    if config['icon_url']:
        icons.append(
            Icon(
                src=config['icon_url'],
                mimeType="image/png",
                sizes=["128x128"],
            )
        )
    elif config['peltier_enabled']:
        public = (
            config['mcp_public_url']
            or config['website_url']
            or ''
        ).rstrip('/')

        if public:
            icons.append(
                Icon(
                    src=f"{public}/assets/zenbiz-icon.png",
                    mimeType="image/png",
                    sizes=["128x128"],
                )
            )

    instructions = f"""
    You are the Odoo MCP integration for {config['client_name']},
    provided by {config['provider_name']}.

    Only operate against the Odoo environment configured for this MCP instance.
    Never assume data from another client/container.
    Respect tool-level safeguards and confirmation requirements for write actions.
    """.strip()

    mcp_kwargs: dict[str, Any] = {
        "instructions": instructions,
    }

    # Peltier OAuth is implemented as a FastMCP 4 AuthProvider and is passed
    # directly through FastMCP(auth=...).
    hooks = _peltier_hooks()
    if hooks:
        configure_server, _ = hooks
        mcp_kwargs.update(configure_server(config))

    if config['website_url']:
        mcp_kwargs["website_url"] = config['website_url']
    elif config['mcp_public_url']:
        mcp_kwargs["website_url"] = f"{config['mcp_public_url'].rstrip('/')}/mcp"

    if icons:
        mcp_kwargs["icons"] = icons

    return mcp_kwargs


def _http_security_args() -> dict[str, Any]:
    """Build FastMCP 4 HTTP host/origin protection arguments.

    FastMCP 4 no longer accepts transport_security in FastMCP(...). The
    equivalent settings belong on run()/run_http_async()/http_app().
    """
    public_url = (
        config['mcp_public_url']
        or config['website_url']
        or ''
    ).rstrip('/')

    parsed = urlparse(public_url) if public_url else None
    hostname = parsed.hostname if parsed else None

    allowed_hosts = list(config['allowed_hosts'] or [])
    if not allowed_hosts:
        allowed_hosts = [
            "localhost",
            "localhost:*",
            "127.0.0.1",
            "127.0.0.1:*",
        ]

        if hostname:
            allowed_hosts.extend([
                hostname,
                f"{hostname}:*",
            ])

    allowed_origins = list(config['allowed_origins'] or [])
    if not allowed_origins:
        allowed_origins = [
            "http://localhost:*",
            "http://127.0.0.1:*",
        ]

        if parsed and parsed.scheme and parsed.netloc:
            allowed_origins.append(f"{parsed.scheme}://{parsed.netloc}")

    return {
        "host_origin_protection": True,
        "allowed_hosts": allowed_hosts,
        "allowed_origins": allowed_origins,
    }


mcp_server = None


def start():
    global mcp_server

    mcp_server = FastMCP(
        config['service_name'],
        **server_args(),
    )

    # Keep the original ZenMCP addon discovery/registry behavior.
    registry.load_registry(mcp_server)

    # Layer Peltier functionality on top of the ZenMCP server.
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
            config['service_name'],
            config['client_id'],
            config['mcp_host'],
            config['mcp_port'],
        )

        mcp_params: dict[str, Any] = {
            'transport': config['mcp_transport'],
        }

        if config['mcp_transport'] == "stdio":
            mcp_server.run(**mcp_params)
        else:
            # FastMCP 4 transport/runtime settings belong here, not in
            # FastMCP(...).
            mcp_params.update({
                'host': config['mcp_host'],
                'port': config['mcp_port'],
                'stateless_http': True,
                'json_response': True,
                **_http_security_args(),
            })

            mcp_server.run(**mcp_params)

    except Exception as exc:
        _logger.exception(exc)
        rc = -1

    return rc
