from app import base
from app.base import mcp_wrapper_tool, mcp_tool as register_tool


class AccountTool(base.MCPTool):

	@register_tool(name="find_name", description="Executes and search name", tags={'admin'})
	def get_name(self):
		return "Accounting Tools"
	
	@register_tool()
	def get_partner_name(self):
		return self.name
	
	@register_tool()
	def get_list_invoices(self):
		return self.name

	@mcp_wrapper_tool(name="find_list_partners", description="Executes and search partner name")
	def get_list_of_partners(self):
		return 'Return List of Vendors'

	def _get_search(self):
		pass