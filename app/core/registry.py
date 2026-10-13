import logging
from app.core import get_modules, config
from app.base import BaseTool

_logger = logging.getLogger(__name__)

def load_registry(server=None):
    if server is not None:
        registry = BaseTool(server)
        for mod in get_modules():
            _logger.info("Loading modules %s" % mod)
            registry.discover_and_register(f'app.addons.{mod}')