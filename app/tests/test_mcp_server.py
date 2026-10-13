from fastmcp import FastMCP
import logging

from fastmcp.utilities.logging import configure_logging


# 2. Blow away FastMCP's root handlers and force your custom layout
#logging.basicConfig(
#level=logging.INFO,
#format="%(asctime)s - [%(levelname)s] - [%(client_id)s] %(name)s - %(message)s",
#force=True  # <-- This clears out FastMCP's internal configuration
#)
#custom_logger = logging.getLogger('__name__')

mcp_server = FastMCP()

root_logger = logging.getLogger(__name__)
if root_logger.handlers:
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - [%(levelname)s] - [%(client_id)s] %(name)s - %(message)s")
handler.setFormatter(formatter)
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)
# Bind your logger directly to the FastMCP utility layer
#configure_logging(level="INFO", logger=root_logger, enable_rich_tracebacks=False)



root_logger.info('Starting', extra={'client_id': '1234'})




mcp_server.run(transport="http", host="0.0.0.0", port=8000)