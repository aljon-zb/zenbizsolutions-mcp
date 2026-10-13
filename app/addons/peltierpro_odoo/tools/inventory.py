from datetime import date
from typing import Any

from app.addons.peltierpro_odoo.audit import log_tool
from app.addons.peltierpro_odoo.branding import branded_response
from app.addons.peltierpro_odoo.security import ALLOWED_PICKING_STATES, clamp_limit, clean_search, positive_id, choice


def register_inventory_tools(mcp, odoo, settings, failed):
    # Inventory
    # ---------------------------------------------------------------------------
    # Product and stock tools:
    # - search_products
    # - get_stock_by_location
    # - search_inventory_transfers
    #
    # Reporting tools:
    # - get_low_stock_products
    # - get_out_of_stock_products
    # - get_inventory_stock_summary
    # - get_late_inventory_transfers
    # - get_pending_receipts
    # - get_pending_deliveries
    # ---------------------------------------------------------------------------

    @mcp.tool()
    async def search_products(
        search: str,
        limit: int = 20,
    ):
        """
        Search products and product-level stock figures.

        Read-only.
        """

        tool = "search_products"

        params = {
            "search": clean_search(search),
            "limit": limit,
        }

        try:
            if not params["search"]:
                raise ValueError(
                    "Provide a product name, reference, or barcode."
                )

            domain = [
                "|",
                "|",
                [
                    "name",
                    "ilike",
                    params["search"],
                ],
                [
                    "default_code",
                    "ilike",
                    params["search"],
                ],
                [
                    "barcode",
                    "=",
                    params["search"],
                ],
            ]

            rows = await odoo.search_read(
                model="product.product",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "default_code",
                    "barcode",
                    "type",
                    "uom_id",
                    "qty_available",
                    "virtual_available",
                ],
                limit=clamp_limit(
                    limit,
                    settings.max_results,
                ),
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
                    "products": rows,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def get_stock_by_location(
        product_id: int,
        limit: int = 50,
    ):
        """
        Return internal-location stock quants for one product.

        Read-only.
        """

        tool = "get_stock_by_location"

        params = {
            "product_id": product_id,
            "limit": limit,
        }

        try:
            positive_id(
                product_id,
                "product_id",
            )

            rows = await odoo.search_read(
                model="stock.quant",
                domain=[
                    [
                        "product_id",
                        "=",
                        product_id,
                    ],
                    [
                        "location_id.usage",
                        "=",
                        "internal",
                    ],
                    [
                        "quantity",
                        "!=",
                        0,
                    ],
                ],
                fields=[
                    "id",
                    "product_id",
                    "location_id",
                    "company_id",
                    "quantity",
                    "reserved_quantity",
                    "available_quantity",
                ],
                limit=clamp_limit(
                    limit,
                    settings.max_results,
                ),
                order="location_id asc, id asc",
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
                    "stock_quants": rows,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def search_inventory_transfers(
        reference: str = "",
        state: str = "",
        limit: int = 20,
    ):
        """
        Search receipts, deliveries, and internal transfers.

        Read-only.
        """

        tool = "search_inventory_transfers"

        params = {
            "reference": clean_search(reference),
            "state": state,
            "limit": limit,
        }

        try:
            state = choice(
                state,
                ALLOWED_PICKING_STATES,
                "transfer state",
            )

            domain = []

            if params["reference"]:
                domain.extend(
                    [
                        "|",
                        [
                            "name",
                            "ilike",
                            params["reference"],
                        ],
                        [
                            "origin",
                            "ilike",
                            params["reference"],
                        ],
                    ]
                )

            if state:
                domain.append(
                    [
                        "state",
                        "=",
                        state,
                    ]
                )

            rows = await odoo.search_read(
                model="stock.picking",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "picking_type_id",
                    "location_id",
                    "location_dest_id",
                    "scheduled_date",
                    "date_deadline",
                    "date_done",
                    "state",
                    "origin",
                    "company_id",
                ],
                limit=clamp_limit(
                    limit,
                    settings.max_results,
                ),
                order="scheduled_date desc, id desc",
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
                    "transfers": rows,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )



    @mcp.tool()
    async def get_low_stock_products(
        threshold: float = 10,
        limit: int = 50,
    ):
        """
        Return products whose available stock is at or below a threshold.

        Read-only.
        """

        tool = "get_low_stock_products"

        params = {
            "threshold": threshold,
            "limit": limit,
        }

        try:
            rows = await odoo.search_read(
                model="product.product",
                domain=[
                    ["active", "=", True],
                    ["type", "=", "consu"],
                ],
                fields=[
                    "id",
                    "name",
                    "default_code",
                    "barcode",
                    "uom_id",
                    "qty_available",
                    "virtual_available",
                ],
                limit=clamp_limit(
                    limit,
                    settings.max_results,
                ),
                order="name asc",
            )

            products = [
                row
                for row in rows
                if float(
                    row.get("qty_available") or 0
                ) <= threshold
            ]

            products.sort(
                key=lambda row: (
                    float(
                        row.get("qty_available") or 0
                    ),
                    row.get("name") or "",
                )
            )

            log_tool(
                tool,
                params,
                len(products),
            )

            return branded_response(
                {
                    "success": True,
                    "threshold": threshold,
                    "count": len(products),
                    "products": products,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def get_out_of_stock_products(
        limit: int = 50,
    ):
        """
        Return active products with no on-hand stock.

        Read-only.
        """

        tool = "get_out_of_stock_products"

        params = {
            "limit": limit,
        }

        try:
            rows = await odoo.search_read(
                model="product.product",
                domain=[
                    ["active", "=", True],
                    ["type", "=", "consu"],
                ],
                fields=[
                    "id",
                    "name",
                    "default_code",
                    "barcode",
                    "uom_id",
                    "qty_available",
                    "virtual_available",
                ],
                limit=clamp_limit(
                    limit,
                    settings.max_results,
                ),
                order="name asc",
            )

            products = [
                row
                for row in rows
                if float(
                    row.get("qty_available") or 0
                ) <= 0
            ]

            products.sort(
                key=lambda row: (
                    float(
                        row.get("qty_available") or 0
                    ),
                    row.get("name") or "",
                )
            )

            log_tool(
                tool,
                params,
                len(products),
            )

            return branded_response(
                {
                    "success": True,
                    "count": len(products),
                    "products": products,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def get_inventory_stock_summary(
        limit: int = 100,
    ):
        """
        Return a stock summary for active products.

        Read-only.
        """

        tool = "get_inventory_stock_summary"
        params = {"limit": limit}

        try:
            rows = await odoo.search_read(
                model="product.product",
                domain=[
                    ["active", "=", True],
                ],
                fields=[
                    "id",
                    "name",
                    "default_code",
                    "type",
                    "uom_id",
                    "qty_available",
                    "virtual_available",
                ],
                limit=clamp_limit(limit, settings.max_results),
                order="name asc",
            )

            total_on_hand = sum(
                float(row.get("qty_available") or 0)
                for row in rows
            )
            total_forecast = sum(
                float(row.get("virtual_available") or 0)
                for row in rows
            )

            log_tool(tool, params, len(rows))

            return branded_response(
                {
                    "success": True,
                    "count": len(rows),
                    "total_on_hand_quantity": total_on_hand,
                    "total_forecast_quantity": total_forecast,
                    "products": rows,
                }
            )

        except Exception as exc:
            return failed(tool, exc, params)


    @mcp.tool()
    async def get_late_inventory_transfers(
        limit: int = 50,
    ):
        """
        Return unfinished inventory transfers whose scheduled date has passed.

        Read-only.
        """

        tool = "get_late_inventory_transfers"
        params = {"limit": limit}

        try:
            today_text = date.today().isoformat()

            rows = await odoo.search_read(
                model="stock.picking",
                domain=[
                    ["state", "not in", ["done", "cancel"]],
                    ["scheduled_date", "!=", False],
                    ["scheduled_date", "<", f"{today_text} 00:00:00"],
                ],
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "picking_type_id",
                    "location_id",
                    "location_dest_id",
                    "scheduled_date",
                    "date_deadline",
                    "state",
                    "origin",
                    "company_id",
                ],
                limit=clamp_limit(limit, settings.max_results),
                order="scheduled_date asc, id asc",
            )

            log_tool(tool, params, len(rows))

            return branded_response(
                {
                    "success": True,
                    "as_of_date": today_text,
                    "count": len(rows),
                    "transfers": rows,
                }
            )

        except Exception as exc:
            return failed(tool, exc, params)


    @mcp.tool()
    async def get_pending_receipts(
        limit: int = 50,
    ):
        """
        Return unfinished incoming inventory receipts.

        Read-only.
        """

        tool = "get_pending_receipts"
        params = {"limit": limit}

        try:
            rows = await odoo.search_read(
                model="stock.picking",
                domain=[
                    ["picking_type_code", "=", "incoming"],
                    ["state", "not in", ["done", "cancel"]],
                ],
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "picking_type_id",
                    "scheduled_date",
                    "date_deadline",
                    "state",
                    "origin",
                    "company_id",
                ],
                limit=clamp_limit(limit, settings.max_results),
                order="scheduled_date asc, id asc",
            )

            log_tool(tool, params, len(rows))

            return branded_response(
                {
                    "success": True,
                    "count": len(rows),
                    "receipts": rows,
                }
            )

        except Exception as exc:
            return failed(tool, exc, params)


    @mcp.tool()
    async def get_pending_deliveries(
        limit: int = 50,
    ):
        """
        Return unfinished outgoing customer deliveries.

        Read-only.
        """

        tool = "get_pending_deliveries"
        params = {"limit": limit}

        try:
            rows = await odoo.search_read(
                model="stock.picking",
                domain=[
                    ["picking_type_code", "=", "outgoing"],
                    ["state", "not in", ["done", "cancel"]],
                ],
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "picking_type_id",
                    "scheduled_date",
                    "date_deadline",
                    "state",
                    "origin",
                    "company_id",
                ],
                limit=clamp_limit(limit, settings.max_results),
                order="scheduled_date asc, id asc",
            )

            log_tool(tool, params, len(rows))

            return branded_response(
                {
                    "success": True,
                    "count": len(rows),
                    "deliveries": rows,
                }
            )

        except Exception as exc:
            return failed(tool, exc, params)


    # ---------------------------------------------------------------------------
