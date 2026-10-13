from .connection import register_connection_tools
from .users import register_users_tools
from .projects import register_projects_tools
from .crm import register_crm_tools
from .sales import register_sales_tools
from .accounting import register_accounting_tools
from .inventory import register_inventory_tools
from .contacts import register_contacts_tools

ALL_REGISTRARS = (
    register_connection_tools,
    register_users_tools,
    register_projects_tools,
    register_crm_tools,
    register_sales_tools,
    register_accounting_tools,
    register_inventory_tools,
    register_contacts_tools,
)
