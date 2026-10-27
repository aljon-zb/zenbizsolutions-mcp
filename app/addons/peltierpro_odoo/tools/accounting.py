from datetime import date, timedelta
from typing import Any

from app.addons.peltierpro_odoo.audit import log_tool
from app.addons.peltierpro_odoo.branding import branded_response
from app.addons.peltierpro_odoo.security import clamp_limit, clean_search


def register_accounting_tools(mcp, odoo, settings, failed):
    # Accounting
    # ---------------------------------------------------------------------------
    # Reporting tools:
    # - get_customer_invoices
    # - get_invoice_product_sales_summary
    # - search_customer_invoices_due
    # - get_aged_receivables
    # - get_aged_payables
    # - get_customer_outstanding_balance
    # - get_vendor_outstanding_balance
    # ---------------------------------------------------------------------------

    def _validate_date_range(date_from: str, date_to: str):
        """Validate optional ISO dates and ensure the range is chronological."""
        parsed_from = None
        parsed_to = None

        if date_from:
            parsed_from = date.fromisoformat(date_from)

        if date_to:
            parsed_to = date.fromisoformat(date_to)

        if parsed_from and parsed_to and parsed_from > parsed_to:
            raise ValueError("date_from cannot be later than date_to.")


    async def _read_invoice_lines(line_ids: list[int]):
        """Read invoice lines in bounded chunks to avoid oversized RPC requests."""
        if not line_ids:
            return []

        chunk_size = max(1, min(int(settings.max_results or 100), 200))
        rows = []

        for start in range(0, len(line_ids), chunk_size):
            chunk = line_ids[start:start + chunk_size]

            chunk_rows = await odoo.read(
                model="account.move.line",
                record_ids=chunk,
                fields=[
                    "id",
                    "move_id",
                    "product_id",
                    "name",
                    "quantity",
                    "product_uom_id",
                    "price_unit",
                    "discount",
                    "price_subtotal",
                    "price_total",
                    "currency_id",
                    "display_type",
                ],
            )

            rows.extend(chunk_rows or [])

        return rows


    async def _fetch_posted_customer_invoices(
        date_from: str = "",
        date_to: str = "",
        search: str = "",
        limit: int = 100,
        include_lines: bool = True,
    ):
        """Internal helper for posted customer-invoice history/reporting tools."""
        clean_date_from = clean_search(date_from)
        clean_date_to = clean_search(date_to)
        clean_query = clean_search(search)

        _validate_date_range(clean_date_from, clean_date_to)

        safe_limit = clamp_limit(
            limit,
            settings.max_results,
        )

        domain: list[Any] = [
            ["move_type", "=", "out_invoice"],
            ["state", "=", "posted"],
        ]

        if clean_date_from:
            domain.append(
                ["invoice_date", ">=", clean_date_from]
            )

        if clean_date_to:
            domain.append(
                ["invoice_date", "<=", clean_date_to]
            )

        if clean_query:
            domain.extend(
                [
                    "|",
                    "|",
                    ["name", "ilike", clean_query],
                    ["ref", "ilike", clean_query],
                    ["partner_id", "ilike", clean_query],
                ]
            )

        invoices = await odoo.search_read(
            model="account.move",
            domain=domain,
            fields=[
                "id",
                "name",
                "ref",
                "partner_id",
                "invoice_date",
                "invoice_date_due",
                "currency_id",
                "amount_untaxed",
                "amount_tax",
                "amount_total",
                "amount_residual",
                "payment_state",
                "invoice_payment_term_id",
                "company_id",
                "invoice_line_ids",
            ],
            limit=safe_limit,
            order="invoice_date asc, id asc",
        )

        if not include_lines or not invoices:
            return invoices, []

        all_line_ids: list[int] = []

        for invoice in invoices:
            all_line_ids.extend(
                invoice.get("invoice_line_ids") or []
            )

        # Preserve order while removing duplicate IDs.
        unique_line_ids = list(dict.fromkeys(all_line_ids))
        lines = await _read_invoice_lines(unique_line_ids)

        lines_by_move: dict[int, list[dict[str, Any]]] = {}

        for line in lines:
            move = line.get("move_id")
            move_id = (
                move[0]
                if isinstance(move, (list, tuple)) and move
                else move
            )

            if not move_id:
                continue

            lines_by_move.setdefault(move_id, []).append(line)

        for invoice in invoices:
            invoice["lines"] = lines_by_move.get(
                invoice.get("id"),
                [],
            )

        return invoices, lines


    @mcp.tool()
    async def get_customer_invoices(
        date_from: str = "",
        date_to: str = "",
        search: str = "",
        include_lines: bool = True,
        limit: int = 100,
    ):
        """
        Return posted customer invoices for a date range, including paid invoices.

        Use this tool for historical invoice reporting, invoice lookups, monthly or
        quarterly invoice analysis, and requests that need invoice line details.

        Unlike the outstanding/aged-receivable tools, this tool DOES NOT exclude
        invoices that are already paid. It returns posted customer invoices based
        on invoice_date.

        Read-only.

        Parameters:
        - date_from: optional invoice start date in YYYY-MM-DD format
        - date_to: optional invoice end date in YYYY-MM-DD format
        - search: optional customer name, invoice number, or reference
        - include_lines: when True, include invoice line details
        - limit: maximum number of invoice records to return

        Invoice line details include product, quantity, unit price, discount,
        untaxed subtotal, total, and currency.

        Provider: Zen Business Solutions
        """

        tool = "get_customer_invoices"

        params = {
            "date_from": clean_search(date_from),
            "date_to": clean_search(date_to),
            "search": clean_search(search),
            "include_lines": include_lines,
            "limit": limit,
        }

        try:
            invoices, lines = await _fetch_posted_customer_invoices(
                date_from=params["date_from"],
                date_to=params["date_to"],
                search=params["search"],
                limit=limit,
                include_lines=include_lines,
            )

            totals_by_currency: dict[int, dict[str, Any]] = {}

            for invoice in invoices:
                currency = invoice.get("currency_id") or [0, ""]
                currency_id = currency[0] if currency else 0
                currency_name = (
                    currency[1]
                    if currency and len(currency) > 1
                    else ""
                )

                if currency_id not in totals_by_currency:
                    totals_by_currency[currency_id] = {
                        "currency_id": currency_id,
                        "currency_name": currency_name,
                        "invoice_count": 0,
                        "amount_untaxed": 0.0,
                        "amount_tax": 0.0,
                        "amount_total": 0.0,
                        "amount_residual": 0.0,
                    }

                summary = totals_by_currency[currency_id]
                summary["invoice_count"] += 1
                summary["amount_untaxed"] += float(
                    invoice.get("amount_untaxed") or 0
                )
                summary["amount_tax"] += float(
                    invoice.get("amount_tax") or 0
                )
                summary["amount_total"] += float(
                    invoice.get("amount_total") or 0
                )
                summary["amount_residual"] += float(
                    invoice.get("amount_residual") or 0
                )

            log_tool(
                tool,
                params,
                len(invoices),
            )

            return branded_response(
                {
                    "success": True,
                    "date_from": params["date_from"] or None,
                    "date_to": params["date_to"] or None,
                    "search": params["search"] or None,
                    "includes_paid_invoices": True,
                    "invoice_count": len(invoices),
                    "invoice_line_count": len(lines) if include_lines else None,
                    "totals_by_currency": list(
                        totals_by_currency.values()
                    ),
                    "invoices": invoices,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def get_invoice_product_sales_summary(
        date_from: str = "",
        date_to: str = "",
        search: str = "",
        limit: int = 100,
    ):
        """
        Summarize products sold from POSTED CUSTOMER INVOICES for a date range.

        This is the preferred tool for requests such as:
        - "Create a summary of products sold for Q3 2026 based on invoices issued"
        - monthly/quarterly invoiced sales by product
        - gross sales, discounts, and net sales by invoiced product

        The report uses invoice_date, not Sales Order date, and includes both paid
        and unpaid posted customer invoices.

        Calculations per invoice line:
        - gross_sales = quantity * price_unit
        - discount = gross_sales - price_subtotal
        - net_sales = price_subtotal

        Tax is not included in gross_sales or net_sales. Results are separated by
        currency so values from different currencies are never mixed.

        Royalty rate and royalty amount are intentionally not calculated because
        the current connector does not define a reliable royalty-rate source.

        Read-only.

        Parameters:
        - date_from: optional invoice start date in YYYY-MM-DD format
        - date_to: optional invoice end date in YYYY-MM-DD format
        - search: optional customer name, invoice number, or reference
        - limit: maximum number of invoice records included in the report

        Provider: Zen Business Solutions
        """

        tool = "get_invoice_product_sales_summary"

        params = {
            "date_from": clean_search(date_from),
            "date_to": clean_search(date_to),
            "search": clean_search(search),
            "limit": limit,
        }

        try:
            invoices, lines = await _fetch_posted_customer_invoices(
                date_from=params["date_from"],
                date_to=params["date_to"],
                search=params["search"],
                limit=limit,
                include_lines=True,
            )

            invoice_by_id = {
                invoice["id"]: invoice
                for invoice in invoices
            }

            products: dict[tuple[int, int], dict[str, Any]] = {}
            totals_by_currency: dict[int, dict[str, Any]] = {}
            included_line_count = 0

            for line in lines:
                display_type = line.get("display_type")

                # Exclude sections, notes, taxes/payment terms, and any other
                # non-product/non-invoice-content lines.
                if display_type in {
                    "line_section",
                    "line_note",
                    "tax",
                    "payment_term",
                }:
                    continue

                product = line.get("product_id")

                # This report is specifically a products-sold summary.
                if not product:
                    continue

                move = line.get("move_id")
                move_id = (
                    move[0]
                    if isinstance(move, (list, tuple)) and move
                    else move
                )

                invoice = invoice_by_id.get(move_id)

                if not invoice:
                    continue

                currency = (
                    line.get("currency_id")
                    or invoice.get("currency_id")
                    or [0, ""]
                )

                currency_id = currency[0] if currency else 0
                currency_name = (
                    currency[1]
                    if currency and len(currency) > 1
                    else ""
                )

                product_id = product[0]
                product_name = (
                    product[1]
                    if len(product) > 1
                    else line.get("name") or str(product_id)
                )

                quantity = float(line.get("quantity") or 0)
                price_unit = float(line.get("price_unit") or 0)
                discount_rate = float(line.get("discount") or 0)
                net_sales = float(line.get("price_subtotal") or 0)
                gross_sales = quantity * price_unit
                discount_amount = gross_sales - net_sales

                # Avoid tiny floating-point artifacts such as 1e-13.
                if abs(discount_amount) < 0.000001:
                    discount_amount = 0.0

                key = (currency_id, product_id)

                if key not in products:
                    products[key] = {
                        "product_id": product_id,
                        "product_name": product_name,
                        "currency_id": currency_id,
                        "currency_name": currency_name,
                        "quantity": 0.0,
                        "gross_sales": 0.0,
                        "discount": 0.0,
                        "net_sales": 0.0,
                        "invoice_count": 0,
                        "line_count": 0,
                        "invoice_ids": set(),
                        "discount_rates": set(),
                        "royalty_rate": None,
                        "royalty": None,
                        "notes": "",
                    }

                item = products[key]
                item["quantity"] += quantity
                item["gross_sales"] += gross_sales
                item["discount"] += discount_amount
                item["net_sales"] += net_sales
                item["line_count"] += 1
                item["invoice_ids"].add(move_id)
                item["discount_rates"].add(discount_rate)

                if currency_id not in totals_by_currency:
                    totals_by_currency[currency_id] = {
                        "currency_id": currency_id,
                        "currency_name": currency_name,
                        "gross_sales": 0.0,
                        "discount": 0.0,
                        "net_sales": 0.0,
                        "product_count": 0,
                        "invoice_ids": set(),
                    }

                currency_total = totals_by_currency[currency_id]
                currency_total["gross_sales"] += gross_sales
                currency_total["discount"] += discount_amount
                currency_total["net_sales"] += net_sales
                currency_total["invoice_ids"].add(move_id)

                included_line_count += 1

            report_products = []

            for item in products.values():
                item["invoice_count"] = len(item["invoice_ids"])
                item["invoice_ids"] = sorted(item["invoice_ids"])
                item["discount_rates"] = sorted(item["discount_rates"])
                report_products.append(item)

            report_products.sort(
                key=lambda item: (
                    item["currency_name"],
                    -item["net_sales"],
                    item["product_name"],
                )
            )

            product_counts_by_currency: dict[int, int] = {}

            for item in report_products:
                currency_id = item["currency_id"]
                product_counts_by_currency[currency_id] = (
                    product_counts_by_currency.get(currency_id, 0) + 1
                )

            currency_totals = []

            for currency_id, item in totals_by_currency.items():
                item["invoice_count"] = len(item["invoice_ids"])
                item["invoice_ids"] = sorted(item["invoice_ids"])
                item["product_count"] = product_counts_by_currency.get(
                    currency_id,
                    0,
                )
                item["royalty"] = None
                currency_totals.append(item)

            currency_totals.sort(
                key=lambda item: item["currency_name"]
            )

            log_tool(
                tool,
                params,
                len(report_products),
            )

            return branded_response(
                {
                    "success": True,
                    "basis": "posted_customer_invoices",
                    "date_field": "invoice_date",
                    "date_from": params["date_from"] or None,
                    "date_to": params["date_to"] or None,
                    "search": params["search"] or None,
                    "includes_paid_invoices": True,
                    "invoice_count": len(invoices),
                    "invoice_line_count": included_line_count,
                    "product_count": len(report_products),
                    "calculation": {
                        "gross_sales": "quantity * price_unit",
                        "discount": "gross_sales - price_subtotal",
                        "net_sales": "price_subtotal",
                        "tax_included": False,
                    },
                    "royalty": {
                        "calculated": False,
                        "reason": (
                            "No reliable royalty-rate field/source is defined "
                            "in the current connector."
                        ),
                    },
                    "totals_by_currency": currency_totals,
                    "products": report_products,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def search_customer_invoices_due(
        due_status: str = "overdue",
        due_within_days: int = 7,
        search: str = "",
        limit: int = 20,
    ):
        """
        Report posted customer invoices that are overdue or almost due.

        Read-only.

        Parameters:
        - due_status: "overdue" or "almost_due"
        - due_within_days: for almost_due, include invoices due from today
          through this many days ahead
        - search: optional customer name or invoice number/reference
        - limit: maximum number of invoice records to return
        """

        tool = "search_customer_invoices_due"

        params = {
            "due_status": clean_search(due_status).lower(),
            "due_within_days": due_within_days,
            "search": clean_search(search),
            "limit": limit,
        }

        try:
            if params["due_status"] not in {
                "overdue",
                "almost_due",
            }:
                raise ValueError(
                    'due_status must be "overdue" or "almost_due".'
                )

            if due_within_days < 0:
                raise ValueError(
                    "due_within_days must be 0 or greater."
                )

            safe_limit = clamp_limit(
                limit,
                settings.max_results,
            )

            today = date.today()
            today_text = today.isoformat()

            domain: list[Any] = [
                ["move_type", "=", "out_invoice"],
                ["state", "=", "posted"],
                ["payment_state", "not in", ["paid", "reversed"]],
                ["amount_residual", ">", 0],
                ["invoice_date_due", "!=", False],
            ]

            if params["due_status"] == "overdue":
                domain.append(
                    ["invoice_date_due", "<", today_text]
                )
            else:
                due_until = (
                    today + timedelta(days=due_within_days)
                ).isoformat()

                domain.extend(
                    [
                        ["invoice_date_due", ">=", today_text],
                        ["invoice_date_due", "<=", due_until],
                    ]
                )

            if params["search"]:
                domain.extend(
                    [
                        "|",
                        "|",
                        [
                            "name",
                            "ilike",
                            params["search"],
                        ],
                        [
                            "ref",
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
                model="account.move",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "ref",
                    "partner_id",
                    "invoice_date",
                    "invoice_date_due",
                    "currency_id",
                    "amount_untaxed",
                    "amount_tax",
                    "amount_total",
                    "amount_residual",
                    "payment_state",
                    "invoice_payment_term_id",
                    "company_id",
                ],
                limit=safe_limit,
                order="invoice_date_due asc, id asc",
            )

            invoices = []

            for row in rows:
                due_date_value = row.get("invoice_date_due")
                days_difference = None

                if due_date_value:
                    due_date = date.fromisoformat(
                        due_date_value
                    )
                    days_difference = (
                        due_date - today
                    ).days

                invoice = dict(row)
                invoice["due_status"] = params["due_status"]

                if params["due_status"] == "overdue":
                    invoice["days_overdue"] = (
                        abs(days_difference)
                        if days_difference is not None
                        else None
                    )
                else:
                    invoice["days_until_due"] = (
                        days_difference
                    )

                invoices.append(invoice)

            total_residual = sum(
                float(invoice.get("amount_residual") or 0)
                for invoice in invoices
            )

            log_tool(
                tool,
                params,
                len(invoices),
            )

            return branded_response(
                {
                    "success": True,
                    "report": params["due_status"],
                    "as_of_date": today_text,
                    "due_within_days": (
                        due_within_days
                        if params["due_status"] == "almost_due"
                        else None
                    ),
                    "count": len(invoices),
                    "total_residual": total_residual,
                    "invoices": invoices,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )



    @mcp.tool()
    async def get_aged_receivables(
        search: str = "",
        limit: int = 100,
    ):
        """
        Return outstanding posted customer invoices grouped into aging buckets.

        Read-only.

        Aging buckets:
        - current
        - 1_30
        - 31_60
        - 61_90
        - 90_plus

        Parameters:
        - search: optional customer name or invoice number/reference
        - limit: maximum number of invoice records to return
        """

        tool = "get_aged_receivables"

        params = {
            "search": clean_search(search),
            "limit": limit,
        }

        try:
            today = date.today()

            domain: list[Any] = [
                ["move_type", "=", "out_invoice"],
                ["state", "=", "posted"],
                ["payment_state", "not in", ["paid", "reversed"]],
                ["amount_residual", ">", 0],
                ["invoice_date_due", "!=", False],
            ]

            if params["search"]:
                domain.extend(
                    [
                        "|",
                        "|",
                        [
                            "name",
                            "ilike",
                            params["search"],
                        ],
                        [
                            "ref",
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
                model="account.move",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "ref",
                    "partner_id",
                    "invoice_date",
                    "invoice_date_due",
                    "currency_id",
                    "amount_total",
                    "amount_residual",
                    "payment_state",
                    "company_id",
                ],
                limit=clamp_limit(
                    limit,
                    settings.max_results,
                ),
                order="invoice_date_due asc, id asc",
            )

            aging_by_currency: dict[int, dict[str, Any]] = {}
            invoices = []

            for row in rows:
                due_value = row.get("invoice_date_due")
                days_overdue = 0

                if due_value:
                    due_date = date.fromisoformat(
                        due_value
                    )
                    days_overdue = max(
                        0,
                        (today - due_date).days,
                    )

                if days_overdue == 0:
                    bucket = "current"
                elif days_overdue <= 30:
                    bucket = "1_30"
                elif days_overdue <= 60:
                    bucket = "31_60"
                elif days_overdue <= 90:
                    bucket = "61_90"
                else:
                    bucket = "90_plus"

                invoice = dict(row)
                invoice["days_overdue"] = days_overdue
                invoice["aging_bucket"] = bucket
                invoices.append(invoice)

                currency = row.get("currency_id") or [0, ""]
                currency_id = currency[0] if currency else 0
                currency_name = (
                    currency[1]
                    if currency and len(currency) > 1
                    else ""
                )

                if currency_id not in aging_by_currency:
                    aging_by_currency[currency_id] = {
                        "currency_id": currency_id,
                        "currency_name": currency_name,
                        "current": 0.0,
                        "1_30": 0.0,
                        "31_60": 0.0,
                        "61_90": 0.0,
                        "90_plus": 0.0,
                        "total_residual": 0.0,
                    }

                residual = float(
                    row.get("amount_residual") or 0
                )

                aging_by_currency[currency_id][bucket] += residual
                aging_by_currency[currency_id]["total_residual"] += residual

            log_tool(
                tool,
                params,
                len(invoices),
            )

            return branded_response(
                {
                    "success": True,
                    "as_of_date": today.isoformat(),
                    "count": len(invoices),
                    "aging_by_currency": list(
                        aging_by_currency.values()
                    ),
                    "invoices": invoices,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def get_aged_payables(
        search: str = "",
        limit: int = 100,
    ):
        """
        Return outstanding posted vendor bills grouped into aging buckets.

        Read-only.

        Aging buckets:
        - current
        - 1_30
        - 31_60
        - 61_90
        - 90_plus

        Parameters:
        - search: optional vendor name or bill number/reference
        - limit: maximum number of vendor bill records to return
        """

        tool = "get_aged_payables"

        params = {
            "search": clean_search(search),
            "limit": limit,
        }

        try:
            today = date.today()

            domain: list[Any] = [
                ["move_type", "=", "in_invoice"],
                ["state", "=", "posted"],
                ["payment_state", "not in", ["paid", "reversed"]],
                ["amount_residual", ">", 0],
                ["invoice_date_due", "!=", False],
            ]

            if params["search"]:
                domain.extend(
                    [
                        "|",
                        "|",
                        [
                            "name",
                            "ilike",
                            params["search"],
                        ],
                        [
                            "ref",
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
                model="account.move",
                domain=domain,
                fields=[
                    "id",
                    "name",
                    "ref",
                    "partner_id",
                    "invoice_date",
                    "invoice_date_due",
                    "currency_id",
                    "amount_total",
                    "amount_residual",
                    "payment_state",
                    "company_id",
                ],
                limit=clamp_limit(
                    limit,
                    settings.max_results,
                ),
                order="invoice_date_due asc, id asc",
            )

            aging_by_currency: dict[int, dict[str, Any]] = {}
            bills = []

            for row in rows:
                due_value = row.get("invoice_date_due")
                days_overdue = 0

                if due_value:
                    due_date = date.fromisoformat(
                        due_value
                    )
                    days_overdue = max(
                        0,
                        (today - due_date).days,
                    )

                if days_overdue == 0:
                    bucket = "current"
                elif days_overdue <= 30:
                    bucket = "1_30"
                elif days_overdue <= 60:
                    bucket = "31_60"
                elif days_overdue <= 90:
                    bucket = "61_90"
                else:
                    bucket = "90_plus"

                bill = dict(row)
                bill["days_overdue"] = days_overdue
                bill["aging_bucket"] = bucket
                bills.append(bill)

                currency = row.get("currency_id") or [0, ""]
                currency_id = currency[0] if currency else 0
                currency_name = (
                    currency[1]
                    if currency and len(currency) > 1
                    else ""
                )

                if currency_id not in aging_by_currency:
                    aging_by_currency[currency_id] = {
                        "currency_id": currency_id,
                        "currency_name": currency_name,
                        "current": 0.0,
                        "1_30": 0.0,
                        "31_60": 0.0,
                        "61_90": 0.0,
                        "90_plus": 0.0,
                        "total_residual": 0.0,
                    }

                residual = float(
                    row.get("amount_residual") or 0
                )

                aging_by_currency[currency_id][bucket] += residual
                aging_by_currency[currency_id]["total_residual"] += residual

            log_tool(
                tool,
                params,
                len(bills),
            )

            return branded_response(
                {
                    "success": True,
                    "as_of_date": today.isoformat(),
                    "count": len(bills),
                    "aging_by_currency": list(
                        aging_by_currency.values()
                    ),
                    "vendor_bills": bills,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def get_customer_outstanding_balance(
        search: str,
        limit: int = 100,
    ):
        """
        Return outstanding posted customer invoices matching a customer.

        Read-only.

        Parameters:
        - search: customer name
        - limit: maximum number of matching invoices to return
        """

        tool = "get_customer_outstanding_balance"

        params = {
            "search": clean_search(search),
            "limit": limit,
        }

        try:
            if not params["search"]:
                raise ValueError(
                    "Provide a customer name."
                )

            rows = await odoo.search_read(
                model="account.move",
                domain=[
                    ["move_type", "=", "out_invoice"],
                    ["state", "=", "posted"],
                    ["payment_state", "not in", ["paid", "reversed"]],
                    ["amount_residual", ">", 0],
                    ["partner_id", "ilike", params["search"]],
                ],
                fields=[
                    "id",
                    "name",
                    "ref",
                    "partner_id",
                    "invoice_date",
                    "invoice_date_due",
                    "currency_id",
                    "amount_total",
                    "amount_residual",
                    "payment_state",
                    "company_id",
                ],
                limit=clamp_limit(
                    limit,
                    settings.max_results,
                ),
                order="invoice_date_due asc, id asc",
            )

            balances: dict[int, dict[str, Any]] = {}

            for row in rows:
                currency = row.get("currency_id") or [0, ""]
                currency_id = currency[0] if currency else 0
                currency_name = (
                    currency[1]
                    if currency and len(currency) > 1
                    else ""
                )

                if currency_id not in balances:
                    balances[currency_id] = {
                        "currency_id": currency_id,
                        "currency_name": currency_name,
                        "outstanding_balance": 0.0,
                    }

                balances[currency_id]["outstanding_balance"] += float(
                    row.get("amount_residual") or 0
                )

            log_tool(
                tool,
                params,
                len(rows),
            )

            return branded_response(
                {
                    "success": True,
                    "search": params["search"],
                    "count": len(rows),
                    "balances_by_currency": list(
                        balances.values()
                    ),
                    "invoices": rows,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    @mcp.tool()
    async def get_vendor_outstanding_balance(
        search: str,
        limit: int = 100,
    ):
        """
        Return outstanding posted vendor bills matching a vendor.

        Read-only.

        Parameters:
        - search: vendor name
        - limit: maximum number of matching vendor bills to return
        """

        tool = "get_vendor_outstanding_balance"

        params = {
            "search": clean_search(search),
            "limit": limit,
        }

        try:
            if not params["search"]:
                raise ValueError(
                    "Provide a vendor name."
                )

            rows = await odoo.search_read(
                model="account.move",
                domain=[
                    ["move_type", "=", "in_invoice"],
                    ["state", "=", "posted"],
                    ["payment_state", "not in", ["paid", "reversed"]],
                    ["amount_residual", ">", 0],
                    ["partner_id", "ilike", params["search"]],
                ],
                fields=[
                    "id",
                    "name",
                    "ref",
                    "partner_id",
                    "invoice_date",
                    "invoice_date_due",
                    "currency_id",
                    "amount_total",
                    "amount_residual",
                    "payment_state",
                    "company_id",
                ],
                limit=clamp_limit(
                    limit,
                    settings.max_results,
                ),
                order="invoice_date_due asc, id asc",
            )

            balances: dict[int, dict[str, Any]] = {}

            for row in rows:
                currency = row.get("currency_id") or [0, ""]
                currency_id = currency[0] if currency else 0
                currency_name = (
                    currency[1]
                    if currency and len(currency) > 1
                    else ""
                )

                if currency_id not in balances:
                    balances[currency_id] = {
                        "currency_id": currency_id,
                        "currency_name": currency_name,
                        "outstanding_balance": 0.0,
                    }

                balances[currency_id]["outstanding_balance"] += float(
                    row.get("amount_residual") or 0
                )

            log_tool(
                tool,
                params,
                len(rows),
            )

            return branded_response(
                {
                    "success": True,
                    "search": params["search"],
                    "count": len(rows),
                    "balances_by_currency": list(
                        balances.values()
                    ),
                    "vendor_bills": rows,
                }
            )

        except Exception as exc:
            return failed(
                tool,
                exc,
                params,
            )


    # ---------------------------------------------------------------------------
