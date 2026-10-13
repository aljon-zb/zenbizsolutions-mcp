from typing import Any

from app.addons.peltierpro_odoo.audit import log_tool
from app.addons.peltierpro_odoo.branding import branded_response
from app.addons.peltierpro_odoo.security import clamp_limit, clean_search


def register_users_tools(mcp, odoo, settings, failed):
    # Users
    # ---------------------------------------------------------------------------

    @mcp.tool()
    async def search_users(
        search: str = "",
        active_only: bool = True,
        limit: int = 20,
    ):
        """
        Search Odoo users.

        Read-only.

        Searches by user name or login/email.

        Parameters:
        - search: partial user name or login/email
        - active_only: when true, only active users are returned
        - limit: maximum number of records to return
        """

        tool = "search_users"

        params = {
            "search": clean_search(search),
            "active_only": active_only,
            "limit": limit,
        }

        try:
            safe_limit = clamp_limit(
                limit,
                settings.max_results,
            )

            domain: list[Any] = []

            if active_only:
                domain.append(
                    ["active", "=", True]
                )

            if params["search"]:
                domain.extend(
                    [
                        "|",
                        [
                            "name",
                            "ilike",
                            params["search"],
                        ],
                        [
                            "login",
                            "ilike",
                            params["search"],
                        ],
                    ]
                )

            rows = await odoo.search_read(
                model="res.users",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "login",
                    "active",
                    "partner_id",
                    "company_id",
                    "company_ids",
                    "create_date",
                ],
                limit=safe_limit,
                order="name asc",
            )

            log_tool(
                tool,
                params,
                len(rows),
            )

            return branded_response(
                {
                    "success": True,
                    "count": len(rows),
                    "users": rows,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    # ---------------------------------------------------------------------------
