# ZenMCP + Peltier integration notes

## Merge rule

`zenmcp-main` is the base and remains the application architecture. The Peltier project is applied as an integration addon.

The following original ZenMCP areas are retained:

- `main.py`
- `app/cli/`
- `app/service/`
- `app/core/`
- `app/base.py`
- `app/addons/`
- `app/custom_addons/`
- ZenMCP dynamic addon discovery and registry
- ZenMCP FastMCP 4 / MCP 2 dependency baseline

## Added from mcp-peltier

Peltier code is located under:

`app/addons/peltierpro_odoo/`

It contains:

- Odoo API client
- Odoo-backed OAuth provider
- Odoo login route
- per-user permissions
- audit/branding/security helpers
- MCP prompts
- connection/users/projects/CRM/sales/accounting/inventory/contacts tools
- ZenBiz icon

Also added:

- `config/permissions.yaml`
- Docker/deployment examples
- Peltier environment values

## ZenMCP files intentionally updated

### `app/core/config.py`

Extended to understand Peltier/OAuth/permission settings and the old Peltier environment aliases (`TRANSPORT`, `HOST`, `PORT`). The ZenMCP ConfigManager remains in charge.

### `app/service/server.py`

Still owns FastMCP creation and server startup. Two addon hooks were added:

1. `configure_server(config)` before FastMCP creation for Odoo OAuth/server settings.
2. `register_integration(mcp, config)` after the normal Zen registry loads for Peltier routes/tools/prompts.

No separate Peltier `app/server.py` is used.

## Adding another client later

Do not copy/replace `app/service`, `app/cli`, or `app/core`. Add a new ZenMCP addon/integration and configure it through ZenMCP's existing config system.
