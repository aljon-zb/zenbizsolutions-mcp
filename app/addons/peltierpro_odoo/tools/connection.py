from app.addons.peltierpro_odoo.audit import log_tool
from app.addons.peltierpro_odoo.branding import branded_response


def register_connection_tools(mcp, odoo, settings, failed):
    # Connection
    # ---------------------------------------------------------------------------

    @mcp.tool()
    async def test_odoo_connection():
        """
        Verify Odoo JSON-2 authentication without changing data.
        """

        tool = "test_odoo_connection"

        try:
            await odoo.search_read(
                model="res.users",
                domain=[
                    ["id", "=", 0],
                ],
                fields=[
                    "id",
                ],
                limit=1,
            )

            log_tool(tool)

            return branded_response(
                {
                    "success": True,
                    "message": "Odoo authentication succeeded.",
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                {},
            )


    # ---------------------------------------------------------------------------
