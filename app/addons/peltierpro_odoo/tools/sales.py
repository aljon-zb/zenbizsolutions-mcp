from datetime import date, timedelta
from typing import Any

from app.addons.peltierpro_odoo.audit import log_tool
from app.addons.peltierpro_odoo.branding import branded_response
from app.addons.peltierpro_odoo.security import ALLOWED_SALES_STATES, clamp_limit, clean_search, positive_id, choice


def register_sales_tools(mcp, odoo, settings, failed):
    # Sales
    # ---------------------------------------------------------------------------

    @mcp.tool()
    async def search_sales_orders(
        search: str = "",
        state: str = "",
        limit: int = 20,
    ):
        """
        Search quotations and sales orders.

        Read-only.
        
        Provider: Zen Business Solutions
        """

        tool = "search_sales_orders"

        params = {
            "search": clean_search(search),
            "state": state,
            "limit": limit,
        }

        try:
            state = choice(
                state,
                ALLOWED_SALES_STATES,
                "sales state",
            )

            domain = []

            if state:
                domain.append(
                    [
                        "state",
                        "=",
                        state,
                    ]
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
                            "partner_id",
                            "ilike",
                            params["search"],
                        ],
                    ]
                )

            rows = await odoo.search_read(
                model="sale.order",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "user_id",
                    "team_id",
                    "date_order",
                    "commitment_date",
                    "state",
                    "currency_id",
                    "amount_untaxed",
                    "amount_tax",
                    "amount_total",
                    "invoice_status",
                ],
                limit=clamp_limit(
                    limit,
                    settings.max_results,
                ),
                order="date_order desc, id desc",
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
                    "sales_orders": rows,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def get_sales_order(
        order_id: int,
    ):
        """
        Get one sales order and its lines.

        Read-only.
        
        Provider: Zen Business Solutions
        """

        tool = "get_sales_order"

        params = {
            "order_id": order_id,
        }

        try:
            positive_id(
                order_id,
                "order_id",
            )

            orders = await odoo.read(
                model="sale.order",
                record_ids=[
                    order_id,
                ],
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "user_id",
                    "date_order",
                    "commitment_date",
                    "state",
                    "currency_id",
                    "amount_untaxed",
                    "amount_tax",
                    "amount_total",
                    "invoice_status",
                    "order_line",
                ],
            )

            if not orders:
                raise ValueError(
                    "Sales order not found or access denied."
                )

            order = orders[0]

            ids = (
                order.get("order_line") or []
            )[: settings.max_results]

            lines = []

            if ids:
                lines = await odoo.read(
                    model="sale.order.line",
                    record_ids=ids,
                    fields=[
                        "id",
                        "product_id",
                        "name",
                        "product_uom_qty",
                        "qty_delivered",
                        "qty_invoiced",
                        "product_uom_id",
                        "price_unit",
                        "discount",
                        "price_subtotal",
                        "price_total",
                    ],
                )

            log_tool(
                tool,
                params,
                1 + len(lines),
            )

            return branded_response(
                {
                    "success": True,
                    "order": order,
                    "lines": lines,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )



    # ---------------------------------------------------------------------------
    # Sales
    # ---------------------------------------------------------------------------
    # Existing tools:
    # - search_sales_orders
    # - get_sales_order
    #
    # Reporting tools:
    # - get_sales_summary
    # - get_top_customers
    # - get_salesperson_performance
    # - get_uninvoiced_sales_orders
    # - get_expiring_quotations
    # ---------------------------------------------------------------------------

    @mcp.tool()
    async def get_sales_summary(
        date_from: str = "",
        date_to: str = "",
        limit: int = 100,
    ):
        """
        Summarize confirmed sales orders for an optional date range.

        Read-only.
        
        Provider: Zen Business Solutions

        Parameters:
        - date_from: optional start date in YYYY-MM-DD format
        - date_to: optional end date in YYYY-MM-DD format
        - limit: maximum number of sales orders included in the summary
        """

        tool = "get_sales_summary"

        params = {
            "date_from": clean_search(date_from),
            "date_to": clean_search(date_to),
            "limit": limit,
        }

        try:
            safe_limit = clamp_limit(
                limit,
                settings.max_results,
            )

            domain: list[Any] = [
                ["state", "in", ["sale", "done"]],
            ]

            if params["date_from"]:
                date.fromisoformat(params["date_from"])
                domain.append(
                    ["date_order", ">=", f'{params["date_from"]} 00:00:00']
                )

            if params["date_to"]:
                date.fromisoformat(params["date_to"])
                domain.append(
                    ["date_order", "<=", f'{params["date_to"]} 23:59:59']
                )

            rows = await odoo.search_read(
                model="sale.order",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "user_id",
                    "team_id",
                    "date_order",
                    "currency_id",
                    "amount_untaxed",
                    "amount_tax",
                    "amount_total",
                    "invoice_status",
                ],
                limit=safe_limit,
                order="date_order desc, id desc",
            )

            by_currency: dict[int, dict[str, Any]] = {}

            for row in rows:
                currency = row.get("currency_id") or [0, ""]
                currency_id = currency[0] if currency else 0
                currency_name = (
                    currency[1]
                    if currency and len(currency) > 1
                    else ""
                )

                if currency_id not in by_currency:
                    by_currency[currency_id] = {
                        "currency_id": currency_id,
                        "currency_name": currency_name,
                        "order_count": 0,
                        "amount_untaxed": 0.0,
                        "amount_tax": 0.0,
                        "amount_total": 0.0,
                    }

                summary = by_currency[currency_id]
                summary["order_count"] += 1
                summary["amount_untaxed"] += float(
                    row.get("amount_untaxed") or 0
                )
                summary["amount_tax"] += float(
                    row.get("amount_tax") or 0
                )
                summary["amount_total"] += float(
                    row.get("amount_total") or 0
                )

            currency_summaries = list(
                by_currency.values()
            )

            log_tool(
                tool,
                params,
                len(rows),
            )

            return branded_response(
                {
                    "success": True,
                    "date_from": params["date_from"] or None,
                    "date_to": params["date_to"] or None,
                    "count": len(rows),
                    "currency_summaries": currency_summaries,
                    "sales_orders": rows,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def get_top_customers(
        date_from: str = "",
        date_to: str = "",
        limit: int = 20,
    ):
        """
        Rank customers by confirmed sales order value.

        Read-only.
        
        Provider: Zen Business Solutions

        Results are separated by currency to avoid mixing totals from
        different currencies.

        Parameters:
        - date_from: optional start date in YYYY-MM-DD format
        - date_to: optional end date in YYYY-MM-DD format
        - limit: maximum number of sales orders used for aggregation
        """

        tool = "get_top_customers"

        params = {
            "date_from": clean_search(date_from),
            "date_to": clean_search(date_to),
            "limit": limit,
        }

        try:
            safe_limit = clamp_limit(
                limit,
                settings.max_results,
            )

            domain: list[Any] = [
                ["state", "in", ["sale", "done"]],
            ]

            if params["date_from"]:
                date.fromisoformat(params["date_from"])
                domain.append(
                    ["date_order", ">=", f'{params["date_from"]} 00:00:00']
                )

            if params["date_to"]:
                date.fromisoformat(params["date_to"])
                domain.append(
                    ["date_order", "<=", f'{params["date_to"]} 23:59:59']
                )

            rows = await odoo.search_read(
                model="sale.order",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "date_order",
                    "currency_id",
                    "amount_total",
                ],
                limit=safe_limit,
                order="date_order desc, id desc",
            )

            customers: dict[tuple[int, int], dict[str, Any]] = {}

            for row in rows:
                partner = row.get("partner_id")
                currency = row.get("currency_id")

                if not partner or not currency:
                    continue

                partner_id = partner[0]
                partner_name = (
                    partner[1]
                    if len(partner) > 1
                    else ""
                )
                currency_id = currency[0]
                currency_name = (
                    currency[1]
                    if len(currency) > 1
                    else ""
                )

                key = (
                    partner_id,
                    currency_id,
                )

                if key not in customers:
                    customers[key] = {
                        "partner_id": partner_id,
                        "partner_name": partner_name,
                        "currency_id": currency_id,
                        "currency_name": currency_name,
                        "order_count": 0,
                        "sales_total": 0.0,
                    }

                customer = customers[key]
                customer["order_count"] += 1
                customer["sales_total"] += float(
                    row.get("amount_total") or 0
                )

            ranked = sorted(
                customers.values(),
                key=lambda item: item["sales_total"],
                reverse=True,
            )

            log_tool(
                tool,
                params,
                len(rows),
            )

            return branded_response(
                {
                    "success": True,
                    "date_from": params["date_from"] or None,
                    "date_to": params["date_to"] or None,
                    "count": len(ranked),
                    "customers": ranked,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def get_salesperson_performance(
        date_from: str = "",
        date_to: str = "",
        limit: int = 100,
    ):
        """
        Summarize confirmed sales by salesperson.

        Read-only.
        
        Provider: Zen Business Solutions

        Results are separated by salesperson and currency.

        Parameters:
        - date_from: optional start date in YYYY-MM-DD format
        - date_to: optional end date in YYYY-MM-DD format
        - limit: maximum number of sales orders used for aggregation
        """

        tool = "get_salesperson_performance"

        params = {
            "date_from": clean_search(date_from),
            "date_to": clean_search(date_to),
            "limit": limit,
        }

        try:
            safe_limit = clamp_limit(
                limit,
                settings.max_results,
            )

            domain: list[Any] = [
                ["state", "in", ["sale", "done"]],
            ]

            if params["date_from"]:
                date.fromisoformat(params["date_from"])
                domain.append(
                    ["date_order", ">=", f'{params["date_from"]} 00:00:00']
                )

            if params["date_to"]:
                date.fromisoformat(params["date_to"])
                domain.append(
                    ["date_order", "<=", f'{params["date_to"]} 23:59:59']
                )

            rows = await odoo.search_read(
                model="sale.order",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "user_id",
                    "team_id",
                    "date_order",
                    "currency_id",
                    "amount_total",
                ],
                limit=safe_limit,
                order="date_order desc, id desc",
            )

            performance: dict[tuple[int, int], dict[str, Any]] = {}

            for row in rows:
                user = row.get("user_id")
                currency = row.get("currency_id")

                if not user or not currency:
                    continue

                user_id = user[0]
                user_name = (
                    user[1]
                    if len(user) > 1
                    else ""
                )
                currency_id = currency[0]
                currency_name = (
                    currency[1]
                    if len(currency) > 1
                    else ""
                )

                key = (
                    user_id,
                    currency_id,
                )

                if key not in performance:
                    performance[key] = {
                        "user_id": user_id,
                        "user_name": user_name,
                        "currency_id": currency_id,
                        "currency_name": currency_name,
                        "order_count": 0,
                        "sales_total": 0.0,
                    }

                salesperson = performance[key]
                salesperson["order_count"] += 1
                salesperson["sales_total"] += float(
                    row.get("amount_total") or 0
                )

            ranked = sorted(
                performance.values(),
                key=lambda item: item["sales_total"],
                reverse=True,
            )

            log_tool(
                tool,
                params,
                len(rows),
            )

            return branded_response(
                {
                    "success": True,
                    "date_from": params["date_from"] or None,
                    "date_to": params["date_to"] or None,
                    "count": len(ranked),
                    "salespeople": ranked,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def get_uninvoiced_sales_orders(
        search: str = "",
        limit: int = 20,
    ):
        """
        Return confirmed sales orders that still need to be invoiced.

        Read-only.
        
        Provider: Zen Business Solutions

        Parameters:
        - search: optional sales order or customer search
        - limit: maximum number of records to return
        """

        tool = "get_uninvoiced_sales_orders"

        params = {
            "search": clean_search(search),
            "limit": limit,
        }

        try:
            domain: list[Any] = [
                ["state", "in", ["sale", "done"]],
                ["invoice_status", "=", "to invoice"],
            ]

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
                            "partner_id",
                            "ilike",
                            params["search"],
                        ],
                    ]
                )

            rows = await odoo.search_read(
                model="sale.order",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "user_id",
                    "team_id",
                    "date_order",
                    "commitment_date",
                    "currency_id",
                    "amount_total",
                    "invoice_status",
                ],
                limit=clamp_limit(
                    limit,
                    settings.max_results,
                ),
                order="date_order asc, id asc",
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
                    "sales_orders": rows,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def get_expiring_quotations(
        within_days: int = 7,
        search: str = "",
        limit: int = 20,
    ):
        """
        Return active quotations whose validity date is approaching.

        Read-only.
        
        Provider: Zen Business Solutions

        Parameters:
        - within_days: include quotations expiring from today through this many days
        - search: optional quotation or customer search
        - limit: maximum number of records to return
        """

        tool = "get_expiring_quotations"

        params = {
            "within_days": within_days,
            "search": clean_search(search),
            "limit": limit,
        }

        try:
            if within_days < 0:
                raise ValueError(
                    "within_days must be 0 or greater."
                )

            today = date.today()
            until = (
                today + timedelta(days=within_days)
            ).isoformat()

            domain: list[Any] = [
                ["state", "in", ["draft", "sent"]],
                ["validity_date", "!=", False],
                ["validity_date", ">=", today.isoformat()],
                ["validity_date", "<=", until],
            ]

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
                            "partner_id",
                            "ilike",
                            params["search"],
                        ],
                    ]
                )

            rows = await odoo.search_read(
                model="sale.order",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "user_id",
                    "team_id",
                    "date_order",
                    "validity_date",
                    "currency_id",
                    "amount_total",
                    "state",
                ],
                limit=clamp_limit(
                    limit,
                    settings.max_results,
                ),
                order="validity_date asc, id asc",
            )

            quotations = []

            for row in rows:
                item = dict(row)
                validity_date = row.get("validity_date")

                if validity_date:
                    expiry_date = date.fromisoformat(
                        validity_date
                    )
                    item["days_until_expiry"] = (
                        expiry_date - today
                    ).days

                quotations.append(item)

            log_tool(
                tool,
                params,
                len(quotations),
            )

            return branded_response(
                {
                    "success": True,
                    "as_of_date": today.isoformat(),
                    "within_days": within_days,
                    "count": len(quotations),
                    "quotations": quotations,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    # ---------------------------------------------------------------------------


    # ---------------------------------------------------------------------------
    # Draft quotation tools
    # ---------------------------------------------------------------------------

    async def _prepare_quotation_items(
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not items:
            raise ValueError("At least one quotation item is required.")

        if len(items) > settings.max_results:
            raise ValueError(
                f"Too many quotation items. Maximum allowed is {settings.max_results}."
            )

        requested: dict[int, float] = {}

        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise ValueError(
                    f"Item {index} must contain product_id and quantity."
                )

            product_id = item.get("product_id")
            quantity = item.get("quantity")

            positive_id(
                product_id,
                f"items[{index}].product_id",
            )

            try:
                quantity = float(quantity)
            except (TypeError, ValueError):
                raise ValueError(
                    f"items[{index}].quantity must be a number."
                )

            if quantity <= 0:
                raise ValueError(
                    f"items[{index}].quantity must be greater than 0."
                )

            requested[product_id] = requested.get(product_id, 0.0) + quantity

        product_ids = list(requested.keys())

        products = await odoo.read(
            model="product.product",
            record_ids=product_ids,
            fields=[
                "id",
                "display_name",
                "active",
                "sale_ok",
                "type",
                "is_storable",
                "uom_id",
                "qty_available",
                "free_qty",
                "virtual_available",
            ],
        )

        products_by_id = {
            row["id"]: row
            for row in products
        }

        missing_ids = [
            product_id
            for product_id in product_ids
            if product_id not in products_by_id
        ]

        if missing_ids:
            raise ValueError(
                "One or more products were not found or access was denied: "
                + ", ".join(str(product_id) for product_id in missing_ids)
            )

        checked_items = []
        unavailable_items = []

        for product_id, requested_qty in requested.items():
            product = products_by_id[product_id]

            if not product.get("active", True):
                raise ValueError(
                    f'Product "{product.get("display_name") or product_id}" is inactive.'
                )

            if not product.get("sale_ok", False):
                raise ValueError(
                    f'Product "{product.get("display_name") or product_id}" '
                    "is not configured for Sales."
                )

            product_type = product.get("type")
            is_service = product_type == "service"
            is_storable = bool(product.get("is_storable", False))

            qty_available = float(product.get("qty_available") or 0.0)
            free_qty = float(product.get("free_qty") or 0.0)
            virtual_available = float(product.get("virtual_available") or 0.0)

            requires_stock_check = (
                not is_service
                and (
                    is_storable
                    or qty_available != 0.0
                    or free_qty != 0.0
                )
            )

            enough_stock = (
                True
                if not requires_stock_check
                else free_qty >= requested_qty
            )

            uom = product.get("uom_id") or [None, ""]

            result = {
                "product_id": product_id,
                "product_name": product.get("display_name"),
                "requested_quantity": requested_qty,
                "uom_id": (
                    uom[0]
                    if isinstance(uom, (list, tuple)) and uom
                    else None
                ),
                "uom_name": (
                    uom[1]
                    if isinstance(uom, (list, tuple)) and len(uom) > 1
                    else ""
                ),
                "product_type": product_type,
                "is_storable": is_storable,
                "stock_check_required": requires_stock_check,
                "qty_available": qty_available,
                "free_qty": free_qty,
                "forecast_quantity": virtual_available,
                "enough_stock": enough_stock,
                "shortage_quantity": (
                    0.0
                    if enough_stock
                    else requested_qty - free_qty
                ),
            }

            checked_items.append(result)

            if not enough_stock:
                unavailable_items.append(result)

        return {
            "available": len(unavailable_items) == 0,
            "items": checked_items,
            "unavailable_items": unavailable_items,
        }


    @mcp.tool()
    async def check_quotation_stock(
        items: list[dict[str, Any]],
    ):
        """
        Check whether requested products have enough free stock before
        creating a quotation.

        Read-only.

        Item format:
        [
            {"product_id": 123, "quantity": 10}
        ]

        Stock-controlled products use free_qty.
        Services/non-stock products are not blocked by inventory quantity.

        Provider: Zen Business Solutions
        """

        tool = "check_quotation_stock"
        params = {"items": items}

        try:
            result = await _prepare_quotation_items(items)

            log_tool(
                tool,
                params,
                len(result["items"]),
            )

            return branded_response(
                {
                    "success": True,
                    **result,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def create_draft_quotation(
        customer_id: int,
        items: list[dict[str, Any]],
        client_order_ref: str = "",
        note: str = "",
    ):
        """
        Create a DRAFT Sales quotation only when all stock-controlled
        products have enough free stock.

        This tool never confirms the quotation.

        If Product A has free_qty = 8 and quantity = 10,
        the quotation is rejected and nothing is created.

        Item format:
        [
            {"product_id": 123, "quantity": 10}
        ]

        Provider: Zen Business Solutions
        """

        tool = "create_draft_quotation"

        params = {
            "customer_id": customer_id,
            "items": items,
            "client_order_ref": clean_search(client_order_ref),
            "note": note,
        }

        try:
            positive_id(
                customer_id,
                "customer_id",
            )

            customers = await odoo.read(
                model="res.partner",
                record_ids=[customer_id],
                fields=[
                    "id",
                    "display_name",
                    "active",
                ],
            )

            if not customers:
                raise ValueError(
                    "Customer not found or access denied."
                )

            if not customers[0].get("active", True):
                raise ValueError(
                    "The selected customer is inactive."
                )

            stock_result = await _prepare_quotation_items(items)

            if not stock_result["available"]:
                shortages = [
                    {
                        "product_id": item["product_id"],
                        "product_name": item["product_name"],
                        "requested_quantity": item["requested_quantity"],
                        "free_qty": item["free_qty"],
                        "shortage_quantity": item["shortage_quantity"],
                        "uom_name": item["uom_name"],
                    }
                    for item in stock_result["unavailable_items"]
                ]

                log_tool(
                    tool,
                    params,
                    0,
                )

                return branded_response(
                    {
                        "success": False,
                        "created": False,
                        "reason": "insufficient_stock",
                        "message": (
                            "Quotation was not created because one or more "
                            "products do not have enough free stock."
                        ),
                        "shortages": shortages,
                        "stock_check": stock_result,
                    }
                )

            order_lines = [
                [
                    0,
                    0,
                    {
                        "product_id": item["product_id"],
                        "product_uom_qty": item["requested_quantity"],
                    },
                ]
                for item in stock_result["items"]
            ]

            values: dict[str, Any] = {
                "partner_id": customer_id,
                "order_line": order_lines,
            }

            if params["client_order_ref"]:
                values["client_order_ref"] = params["client_order_ref"]

            if note:
                values["note"] = note

            order_id = await odoo.create(
                model="sale.order",
                values=values,
            )

            if isinstance(order_id, list):
                if not order_id:
                    raise ValueError(
                        "Odoo did not return a quotation ID."
                    )
                order_id = order_id[0]

            positive_id(
                order_id,
                "created_order_id",
            )

            orders = await odoo.read(
                model="sale.order",
                record_ids=[order_id],
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "user_id",
                    "date_order",
                    "state",
                    "currency_id",
                    "amount_untaxed",
                    "amount_tax",
                    "amount_total",
                    "order_line",
                ],
            )

            if not orders:
                raise ValueError(
                    "Quotation was created but could not be read back."
                )

            order = orders[0]

            if order.get("state") != "draft":
                raise ValueError(
                    "Quotation was created, but Odoo returned an unexpected "
                    f'state: {order.get("state")}.'
                )

            line_ids = (
                order.get("order_line") or []
            )[: settings.max_results]

            lines = []

            if line_ids:
                lines = await odoo.read(
                    model="sale.order.line",
                    record_ids=line_ids,
                    fields=[
                        "id",
                        "product_id",
                        "name",
                        "product_uom_qty",
                        "product_uom_id",
                        "price_unit",
                        "discount",
                        "price_subtotal",
                        "price_total",
                    ],
                )

            log_tool(
                tool,
                params,
                1 + len(lines),
            )

            return branded_response(
                {
                    "success": True,
                    "created": True,
                    "message": (
                        "Draft quotation created successfully. "
                        "It has NOT been confirmed."
                    ),
                    "requires_confirmation": True,
                    "confirmation_action": "confirm_sales_order",
                    "confirmation_prompt": (
                        "The draft quotation has been created. "
                        "Show the quotation details to the user and ask: "
                        "'Would you like me to confirm this Sales Order?' "
                        "Do not confirm unless the user explicitly agrees."
                    ),
                    "quotation": order,
                    "lines": lines,
                    "stock_check": stock_result,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def confirm_sales_order(
        order_id: int,
        user_confirmed: bool = False,
    ):
        """
        Confirm an existing DRAFT quotation and turn it into a Sales Order.

        IMPORTANT SAFETY RULE:
        This tool may only be called after the user has explicitly confirmed
        that they want to proceed with the draft quotation shown by Claude.

        Before confirmation, this tool:
        - verifies the quotation still exists
        - verifies it is still in draft state
        - reloads the quotation lines
        - re-checks free stock for all stock-controlled products
        - refuses confirmation if stock is no longer sufficient

        The user_confirmed parameter MUST be True.
        If it is False, no confirmation occurs.

        Provider: Zen Business Solutions
        """

        tool = "confirm_sales_order"

        params = {
            "order_id": order_id,
            "user_confirmed": user_confirmed,
        }

        try:
            positive_id(
                order_id,
                "order_id",
            )

            if user_confirmed is not True:
                return branded_response(
                    {
                        "success": False,
                        "confirmed": False,
                        "requires_confirmation": True,
                        "message": (
                            "Sales Order confirmation was not performed. "
                            "Explicit user confirmation is required."
                        ),
                    }
                )

            orders = await odoo.read(
                model="sale.order",
                record_ids=[order_id],
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "state",
                    "currency_id",
                    "amount_untaxed",
                    "amount_tax",
                    "amount_total",
                    "order_line",
                ],
            )

            if not orders:
                raise ValueError(
                    "Quotation not found or access denied."
                )

            order = orders[0]

            if order.get("state") != "draft":
                return branded_response(
                    {
                        "success": False,
                        "confirmed": False,
                        "message": (
                            "Only draft quotations can be confirmed by this tool."
                        ),
                        "current_state": order.get("state"),
                        "quotation": order,
                    }
                )

            line_ids = (
                order.get("order_line") or []
            )[: settings.max_results]

            if not line_ids:
                raise ValueError(
                    "The quotation has no order lines."
                )

            lines = await odoo.read(
                model="sale.order.line",
                record_ids=line_ids,
                fields=[
                    "id",
                    "product_id",
                    "product_uom_qty",
                    "display_type",
                ],
            )

            stock_items = []

            for line in lines:
                if line.get("display_type"):
                    continue

                product = line.get("product_id")
                quantity = float(
                    line.get("product_uom_qty") or 0.0
                )

                if not product or quantity <= 0:
                    continue

                product_id = (
                    product[0]
                    if isinstance(product, (list, tuple))
                    else product
                )

                stock_items.append(
                    {
                        "product_id": product_id,
                        "quantity": quantity,
                    }
                )

            if not stock_items:
                raise ValueError(
                    "The quotation has no valid product lines to confirm."
                )

            stock_result = await _prepare_quotation_items(
                stock_items
            )

            if not stock_result["available"]:
                shortages = [
                    {
                        "product_id": item["product_id"],
                        "product_name": item["product_name"],
                        "requested_quantity": item["requested_quantity"],
                        "free_qty": item["free_qty"],
                        "shortage_quantity": item["shortage_quantity"],
                        "uom_name": item["uom_name"],
                    }
                    for item in stock_result["unavailable_items"]
                ]

                return branded_response(
                    {
                        "success": False,
                        "confirmed": False,
                        "reason": "insufficient_stock",
                        "message": (
                            "The Sales Order was not confirmed because stock "
                            "availability changed after the draft was created."
                        ),
                        "shortages": shortages,
                        "stock_check": stock_result,
                    }
                )

            # Call Odoo's real Sales confirmation workflow.
            # Do NOT write state='sale' directly because action_confirm()
            # performs Odoo's required downstream business logic.
            await odoo.execute(
                "sale.order",
                "action_confirm",
                [order_id],
            )

            confirmed_orders = await odoo.read(
                model="sale.order",
                record_ids=[order_id],
                fields=[
                    "id",
                    "name",
                    "partner_id",
                    "user_id",
                    "date_order",
                    "state",
                    "currency_id",
                    "amount_untaxed",
                    "amount_tax",
                    "amount_total",
                    "invoice_status",
                    "order_line",
                ],
            )

            if not confirmed_orders:
                raise ValueError(
                    "Sales Order was confirmed but could not be read back."
                )

            confirmed_order = confirmed_orders[0]

            if confirmed_order.get("state") not in [
                "sale",
                "done",
            ]:
                raise ValueError(
                    "Odoo did not return a confirmed Sales Order state."
                )

            log_tool(
                tool,
                params,
                1,
            )

            return branded_response(
                {
                    "success": True,
                    "confirmed": True,
                    "message": (
                        "The quotation was confirmed successfully "
                        "and is now a Sales Order."
                    ),
                    "sales_order": confirmed_order,
                    "stock_check": stock_result,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )
