"""PeltierPro runtime adapter for ZenMCP.

This module intentionally reads configuration from app.core.config so ZenMCP
remains the single source of runtime configuration and CLI/environment parsing.
"""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PeltierSettings:
    odoo_url: str
    odoo_database: str
    odoo_api_key: str
    request_timeout_seconds: float
    max_results: int
    auth_enabled: bool
    auth_resource_server_url: str | None
    auth_required_scopes: list[str]
    auth_access_token_ttl_seconds: int
    auth_refresh_token_ttl_seconds: int
    auth_code_ttl_seconds: int
    permissions_enabled: bool
    permissions_file: str

    @classmethod
    def from_zen_config(cls, config: Any) -> "PeltierSettings":
        return cls(
            odoo_url=(config['odoo_url'] or '').rstrip('/'),
            odoo_database=config['odoo_database'] or '',
            odoo_api_key=config['odoo_api_key'] or '',
            request_timeout_seconds=float(config['request_timeout_seconds']),
            max_results=int(config['max_results']),
            auth_enabled=bool(config['auth_enabled']),
            auth_resource_server_url=config['auth_resource_server_url'],
            auth_required_scopes=list(config['auth_required_scopes'] or []),
            auth_access_token_ttl_seconds=int(config['auth_access_token_ttl_seconds']),
            auth_refresh_token_ttl_seconds=int(config['auth_refresh_token_ttl_seconds']),
            auth_code_ttl_seconds=int(config['auth_code_ttl_seconds']),
            permissions_enabled=bool(config['permissions_enabled']),
            permissions_file=config['permissions_file'],
        )
