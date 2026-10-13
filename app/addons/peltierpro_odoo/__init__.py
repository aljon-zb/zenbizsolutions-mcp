"""PeltierPro Odoo integration for ZenMCP.

The ZenMCP CLI/service/core remains the application shell. This addon contributes
Odoo connectivity, tools, prompts, branding, OAuth login routes and permissions.
"""
from .integration import configure_server, register_integration

__all__ = ["configure_server", "register_integration"]
