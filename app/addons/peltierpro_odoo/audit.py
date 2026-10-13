import json
import logging

from mcp.server.auth.middleware.auth_context import get_access_token

logger = logging.getLogger("claude_odoo_mcp.audit")


def _identity() -> dict:
    token = get_access_token()
    if token is None:
        return {
            "client_id": None,
            "subject": None,
            "email": None,
            "username": None,
        }

    claims = getattr(token, "claims", None)
    if not isinstance(claims, dict):
        claims = {}

    return {
        "client_id": getattr(token, "client_id", None),
        "subject": getattr(token, "subject", None) or claims.get("sub"),
        "email": claims.get("email"),
        "username": claims.get("preferred_username") or claims.get("username"),
    }


def log_tool(
    tool: str,
    parameters=None,
    count=None,
    success=True,
    error=None,
):
    logger.info(
        json.dumps(
            {
                "event": "mcp_tool_call",
                **_identity(),
                "tool": tool,
                "parameters": parameters or {},
                "record_count": count,
                "success": success,
                "error": error,
            },
            default=str,
        )
    )


def log_access_denied(*, module: str, tool: str, error: str):
    logger.warning(
        json.dumps(
            {
                "event": "mcp_access_denied",
                **_identity(),
                "module": module,
                "tool": tool,
                "success": False,
                "error": error,
            },
            default=str,
        )
    )
