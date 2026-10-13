from datetime import date, timedelta
from typing import Any

from app.addons.peltierpro_odoo.audit import log_tool
from app.addons.peltierpro_odoo.branding import branded_response
from app.addons.peltierpro_odoo.security import clamp_limit, clean_search


def register_accounting_tools(mcp, odoo, settings, failed):
    # Accounting
    # ---------------------------------------------------------------------------
    # Reporting tools:
    # - search_customer_invoices_due
    # - get_aged_receivables
    # - get_aged_payables
    # - get_customer_outstanding_balance
    # - get_vendor_outstanding_balance
    # ---------------------------------------------------------------------------

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
