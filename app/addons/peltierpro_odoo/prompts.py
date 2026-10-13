def register_prompts(mcp):
    @mcp.prompt()
    def peltierpro_welcome():
        """
        Welcome message for Peltier Pro users.
        """

        return """
Welcome!

You are connected to the Peltier Pro Odoo reporting MCP.

Available reporting areas:
- CRM
- Sales
- Accounting
- Inventory
- Projects / Tasks

Powered by: Zen Business Solutions
""".strip()

    @mcp.prompt()
    def sales_report_help():
        """
        Show example prompts for Sales reporting.
        """

        return """
Sales reporting examples:
- Give me this month's sales summary.
- Who are our top customers?
- Show sales performance by salesperson.
- Which confirmed sales orders still need to be invoiced?
- Which quotations are expiring soon?

Powered by: Zen Business Solutions
""".strip()

    @mcp.prompt()
    def accounting_report_help():
        """
        Show example prompts for Accounting reporting.
        """

        return """
Accounting reporting examples:
- Show overdue customer invoices.
- Which invoices are due within the next 7 days?
- Give me our aged receivables.
- Give me our aged payables.
- How much does a customer currently owe us?
- How much do we currently owe a vendor?

Powered by: Zen Business Solutions
""".strip()

    @mcp.prompt()
    def inventory_report_help():
        """
        Show example prompts for Inventory reporting.
        """

        return """
Inventory reporting examples:
- Which products are low on stock?
- Which products are out of stock?
- Give me an inventory stock summary.
- Which inventory transfers are late?
- Show pending incoming receipts.
- Show pending customer deliveries.

Powered by: Zen Business Solutions
""".strip()

    @mcp.prompt()
    def crm_report_help():
        """
        Show example prompts for CRM reporting.
        """

        return """
CRM reporting examples:
- Give me an overview of our CRM pipeline.
- How many opportunities are in each stage?
- Which opportunities are closing soon?
- Which opportunities have not been updated recently?
- Show a salesperson's active CRM pipeline.

Powered by: Zen Business Solutions
""".strip()