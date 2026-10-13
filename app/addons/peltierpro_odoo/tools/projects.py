from typing import Any

from app.addons.peltierpro_odoo.audit import log_tool
from app.addons.peltierpro_odoo.branding import branded_response
from app.addons.peltierpro_odoo.security import clamp_limit, clean_search


def register_projects_tools(mcp, odoo, settings, failed):
    # Projects / Tasks
    # ---------------------------------------------------------------------------

    @mcp.tool()
    async def search_user_projects_tasks(
        search: str,
        active_only: bool = True,
        limit: int = 20,
    ):
        """
        Search for Odoo users and return projects that contain tasks
        assigned to the matched user(s).

        Read-only.

        Searches users by name or login/email, then finds project tasks
        where any matched user is included in the task assignees.

        Parameters:
        - search: partial user name or login/email
        - active_only: when true, only active users and active tasks are returned
        - limit: maximum number of task records to return
        """

        tool = "search_user_projects_tasks"

        params = {
            "search": clean_search(search),
            "active_only": active_only,
            "limit": limit,
        }

        try:
            if not params["search"]:
                raise ValueError(
                    "Provide a user name or login/email."
                )

            safe_limit = clamp_limit(
                limit,
                settings.max_results,
            )

            user_domain: list[Any] = []

            if active_only:
                user_domain.append(
                    ["active", "=", True]
                )

            user_domain.extend(
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

            users = await odoo.search_read(
                model="res.users",
                domain=user_domain,
                fields=[
                    "id",
                    "name",
                    "login",
                    "active",
                ],
                limit=safe_limit,
                order="name asc",
            )

            if not users:
                log_tool(
                    tool,
                    params,
                    0,
                )

                return {
                    "success": True,
                    "count": 0,
                    "matched_users": [],
                    "projects": [],
                    "message": "No matching user found.",
                }

            user_ids = [
                user["id"]
                for user in users
            ]

            task_domain: list[Any] = [
                [
                    "user_ids",
                    "in",
                    user_ids,
                ],
                [
                    "project_id",
                    "!=",
                    False,
                ],
            ]

            if active_only:
                task_domain.append(
                    ["active", "=", True]
                )

            tasks = await odoo.search_read(
                model="project.task",
                domain=task_domain,
                fields=[
                    "id",
                    "name",
                    "project_id",
                    "user_ids",
                    "stage_id",
                    "date_deadline",
                    "priority",
                    "create_date",
                    "write_date",
                ],
                limit=safe_limit,
                order="project_id asc, id asc",
            )

            projects_by_id: dict[int, dict[str, Any]] = {}

            for task in tasks:
                project = task.get("project_id")

                if not project:
                    continue

                project_id = project[0]
                project_name = (
                    project[1]
                    if len(project) > 1
                    else ""
                )

                if project_id not in projects_by_id:
                    projects_by_id[project_id] = {
                        "id": project_id,
                        "name": project_name,
                        "task_count": 0,
                        "tasks": [],
                    }

                projects_by_id[project_id]["tasks"].append(
                    task
                )
                projects_by_id[project_id]["task_count"] += 1

            projects = list(
                projects_by_id.values()
            )

            log_tool(
                tool,
                params,
                len(tasks),
            )

            return branded_response(
                {
                    "success": True,
                    "count": len(projects),
                    "task_count": len(tasks),
                    "matched_users": users,
                    "projects": projects,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    # ---------------------------------------------------------------------------
