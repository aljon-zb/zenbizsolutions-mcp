from datetime import date, timedelta
from typing import Any

from app.addons.peltierpro_odoo.audit import log_tool
from app.addons.peltierpro_odoo.branding import branded_response
from app.addons.peltierpro_odoo.security import clamp_limit, clean_search, positive_id


def register_crm_tools(mcp, odoo, settings, failed):
    # CRM
    # ---------------------------------------------------------------------------
    # Read-only tools:
    # - search_crm_opportunities
    # - get_crm_opportunity
    # - get_crm_pipeline_summary
    # - get_crm_opportunities_closing_soon
    # - get_stale_crm_opportunities
    # - get_crm_salesperson_pipeline
    # ---------------------------------------------------------------------------

    @mcp.tool()
    async def search_crm_opportunities(
        search: str = "",
        limit: int = 20,
    ):
        """
        Search active CRM opportunities.

        Read-only.
        """

        tool = "search_crm_opportunities"

        params = {
            "search": clean_search(search),
            "limit": limit,
        }

        try:
            domain = [
                ["type", "=", "opportunity"],
                ["active", "=", True],
            ]

            if params["search"]:
                domain.append(
                    [
                        "name",
                        "ilike",
                        params["search"],
                    ]
                )

            rows = await odoo.search_read(
                model="crm.lead",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "user_id",
                    "team_id",
                    "stage_id",
                    "expected_revenue",
                    "probability",
                    "date_deadline",
                    "priority",
                    "create_date",
                ],
                limit=clamp_limit(
                    limit,
                    settings.max_results,
                ),
                order="create_date desc, id desc",
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
                    "opportunities": rows,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def get_crm_opportunity(
        opportunity_id: int,
    ):
        """
        Get one CRM opportunity by ID.

        Read-only.
        """

        tool = "get_crm_opportunity"

        params = {
            "opportunity_id": opportunity_id,
        }

        try:
            positive_id(
                opportunity_id,
                "opportunity_id",
            )

            rows = await odoo.read(
                model="crm.lead",
                record_ids=[
                    opportunity_id,
                ],
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "contact_name",
                    "email_from",
                    "phone",
                    "user_id",
                    "team_id",
                    "stage_id",
                    "expected_revenue",
                    "probability",
                    "date_deadline",
                    "description",
                    "create_date",
                    "write_date",
                ],
            )

            if not rows:
                raise ValueError(
                    "Opportunity not found or access denied."
                )

            log_tool(
                tool,
                params,
                1,
            )

            return branded_response(
                {
                    "success": True,
                    "opportunity": rows[0],
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )



    @mcp.tool()
    async def get_crm_pipeline_summary(
        limit: int = 100,
    ):
        """
        Summarize active CRM opportunities by stage.

        Read-only.
        """

        tool = "get_crm_pipeline_summary"
        params = {"limit": limit}

        try:
            rows = await odoo.search_read(
                model="crm.lead",
                domain=[
                    ["type", "=", "opportunity"],
                    ["active", "=", True],
                ],
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "user_id",
                    "team_id",
                    "stage_id",
                    "expected_revenue",
                    "probability",
                    "date_deadline",
                    "priority",
                ],
                limit=clamp_limit(limit, settings.max_results),
                order="stage_id asc, id asc",
            )

            by_stage: dict[int, dict[str, Any]] = {}

            for row in rows:
                stage = row.get("stage_id") or [0, "Unspecified"]
                stage_id = stage[0] if stage else 0
                stage_name = stage[1] if stage and len(stage) > 1 else "Unspecified"

                if stage_id not in by_stage:
                    by_stage[stage_id] = {
                        "stage_id": stage_id,
                        "stage_name": stage_name,
                        "opportunity_count": 0,
                        "expected_revenue": 0.0,
                    }

                by_stage[stage_id]["opportunity_count"] += 1
                by_stage[stage_id]["expected_revenue"] += float(
                    row.get("expected_revenue") or 0
                )

            log_tool(tool, params, len(rows))

            return branded_response(
                {
                    "success": True,
                    "count": len(rows),
                    "stages": list(by_stage.values()),
                    "opportunities": rows,
                }
            )

        except Exception as exc:
            return failed(tool, exc, params)


    @mcp.tool()
    async def get_crm_opportunities_closing_soon(
        within_days: int = 7,
        limit: int = 20,
    ):
        """
        Return active CRM opportunities with deadlines approaching.

        Read-only.
        """

        tool = "get_crm_opportunities_closing_soon"
        params = {
            "within_days": within_days,
            "limit": limit,
        }

        try:
            if within_days < 0:
                raise ValueError("within_days must be 0 or greater.")

            today = date.today()
            until = (today + timedelta(days=within_days)).isoformat()

            rows = await odoo.search_read(
                model="crm.lead",
                domain=[
                    ["type", "=", "opportunity"],
                    ["active", "=", True],
                    ["date_deadline", "!=", False],
                    ["date_deadline", ">=", today.isoformat()],
                    ["date_deadline", "<=", until],
                ],
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "user_id",
                    "team_id",
                    "stage_id",
                    "expected_revenue",
                    "probability",
                    "date_deadline",
                    "priority",
                ],
                limit=clamp_limit(limit, settings.max_results),
                order="date_deadline asc, id asc",
            )

            opportunities = []

            for row in rows:
                item = dict(row)
                deadline = row.get("date_deadline")

                if deadline:
                    deadline_date = date.fromisoformat(deadline)
                    item["days_until_deadline"] = (deadline_date - today).days

                opportunities.append(item)

            log_tool(tool, params, len(opportunities))

            return branded_response(
                {
                    "success": True,
                    "as_of_date": today.isoformat(),
                    "within_days": within_days,
                    "count": len(opportunities),
                    "opportunities": opportunities,
                }
            )

        except Exception as exc:
            return failed(tool, exc, params)


    @mcp.tool()
    async def get_stale_crm_opportunities(
        inactive_days: int = 30,
        limit: int = 20,
    ):
        """
        Return active opportunities that have not been updated recently.

        Read-only.
        """

        tool = "get_stale_crm_opportunities"
        params = {
            "inactive_days": inactive_days,
            "limit": limit,
        }

        try:
            if inactive_days < 0:
                raise ValueError("inactive_days must be 0 or greater.")

            cutoff = (date.today() - timedelta(days=inactive_days)).isoformat()

            rows = await odoo.search_read(
                model="crm.lead",
                domain=[
                    ["type", "=", "opportunity"],
                    ["active", "=", True],
                    ["write_date", "<", f"{cutoff} 00:00:00"],
                ],
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "user_id",
                    "team_id",
                    "stage_id",
                    "expected_revenue",
                    "probability",
                    "date_deadline",
                    "priority",
                    "write_date",
                ],
                limit=clamp_limit(limit, settings.max_results),
                order="write_date asc, id asc",
            )

            log_tool(tool, params, len(rows))

            return branded_response(
                {
                    "success": True,
                    "inactive_days": inactive_days,
                    "count": len(rows),
                    "opportunities": rows,
                }
            )

        except Exception as exc:
            return failed(tool, exc, params)


    @mcp.tool()
    async def get_crm_salesperson_pipeline(
        search: str,
        limit: int = 100,
    ):
        """
        Return active opportunities assigned to matching salespeople.

        Read-only.
        """

        tool = "get_crm_salesperson_pipeline"
        params = {
            "search": clean_search(search),
            "limit": limit,
        }

        try:
            if not params["search"]:
                raise ValueError("Provide a salesperson name or login/email.")

            users = await odoo.search_read(
                model="res.users",
                domain=[
                    "|",
                    ["name", "ilike", params["search"]],
                    ["login", "ilike", params["search"]],
                ],
                fields=[
                    "id",
                    "name",
                    "login",
                    "active",
                ],
                limit=clamp_limit(limit, settings.max_results),
                order="name asc",
            )

            if not users:
                return {
                    "success": True,
                    "count": 0,
                    "matched_users": [],
                    "opportunities": [],
                    "message": "No matching salesperson found.",
                }

            user_ids = [user["id"] for user in users]

            rows = await odoo.search_read(
                model="crm.lead",
                domain=[
                    ["type", "=", "opportunity"],
                    ["active", "=", True],
                    ["user_id", "in", user_ids],
                ],
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "user_id",
                    "team_id",
                    "stage_id",
                    "expected_revenue",
                    "probability",
                    "date_deadline",
                    "priority",
                ],
                limit=clamp_limit(limit, settings.max_results),
                order="user_id asc, stage_id asc, id asc",
            )

            log_tool(tool, params, len(rows))

            return branded_response(
                {
                    "success": True,
                    "count": len(rows),
                    "matched_users": users,
                    "opportunities": rows,
                }
            )

        except Exception as exc:
            return failed(tool, exc, params)


    # ---------------------------------------------------------------------------
