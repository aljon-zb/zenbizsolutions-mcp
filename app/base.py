# Defines the Tools registry metaclass
import inspect
import importlib
import pkgutil
from typing import List, Type, Any, Dict, Unpack
from collections.abc import Mapping

import functools
import logging

from fastmcp.tools import tool
from mcp.server.mcpserver import Icon
from mcp.types import ToolAnnotations
from app.core import config


_logger = logging.getLogger(__name__)

mcp_tool = tool

class ToolDecorator:
    name: str | None
    description: str | None
    title: str | None
    tags: set[str] | None
    icons: list[Icon]  
    annotation: ToolAnnotations | dict | None
    meta: dict[str, Any] | None
    timeout: float | None
    output_schema: dict[str, Any] | None
    run_in_thread: bool = True
    

def mcp_wrapper_tool(**metadata: Unpack[ToolDecorator]):
    
    def register(func):
        @functools.wraps(func) # Preserves original function metadata
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        
        # Tag the wrapper function
        wrapper._is_mcp_tool = True
        wrapper._tool_name = metadata.get('name') or func.__name__
        wrapper._tool_description = metadata.get('description') or func.__doc__
        wrapper._tool_tags = metadata.get('tags', {})
        return wrapper

    return register

class ToolRegistryMeta(type):
    registry = {}

    def __new__(mcs, name, bases, namespace):
        attrs = namespace
        cls = super().__new__(mcs, name, bases, namespace)
        # Skip registering the base class itself
        if name != "BaseTool":
            mcs.registry[name] = cls
            
        return cls

class BaseTool(metaclass=ToolRegistryMeta):
    tool_name: str = ""
    description: str = ""

    def __init__(self, mcp=None):
        self.mcp_server = mcp #TODO: Add MCP server/Add MCP to subclass environment 
        self._registered_classes = []

    def discover_and_register(self, package_path):
        """
        Dynamically imports all modules within a package, instantiates 
        tool classes, and registers methods decorated with @tool().
        """
        package = importlib.import_module(package_path)

        for _, module_name, _ in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            module = importlib.import_module(module_name)

            # Find all classes defined in this module
            for name, obj in inspect.getmembers(module, inspect.isclass):
                # Ensure the class belongs to the target module to avoid re-registering imports
                if obj.__module__ == module_name and name in ToolRegistryMeta.registry:
                    self._register_class_tools(obj)

    def _register_class_tools(self, cls: Type):
        """Instantiates a class and hooks up its bound methods to FastMCP."""
        try:
            # 1. Instantiate the tool container
            # In production, inject required configuration parameters here if needed
            instance = cls() 
            # 2. Iterate through methods to find those marked with @register_tool
            for attr_name in dir(instance):
                attr = getattr(instance, attr_name)
                
                # Skip private methods
                if attr_name.startswith(('_', '___')):
                    continue

                # Check if the method has been wrapped by the standalone @register_tool decorator
                if inspect.ismethod(attr):
                    if hasattr(attr, "__fastmcp__"):
                        # 3. Register the properly bound instance method
                        # self.mcp.add_tool(attr)

                        #TODO: Create a method decorator for internal wrapping of tools before registering
                        #TODO: Re-wrap the tool with the metadata
                        #TODO: Map configuration permissions against tool tags/scope
                        # Before registering it
                        _logger.info(f'Registering tool {attr_name} on module: {cls.__name__}')
                        # Register the tool
                        self.mcp_server.add_tool(attr)

                        self._registered_classes.append(f"{cls.__name__}.{attr_name}")
                    
                    if hasattr(attr, '_is_mcp_tool'):
                        #TODO: Map the ToolDecorator Class
                        wrapper_func_attr = [
                            'name', 'description', 'tags'
                        ]
                        metadata = {}
                        for at in wrapper_func_attr:
                            metadata[at] = getattr(attr, '_tool_%s' % at)

                        _logger.info(f'Registering tool {attr_name} on module: {cls.__name__}')
                        # Register the tool
                        new_tool = tool(**metadata)(attr)
                        self.mcp_server.add_tool(new_tool)

                        self._registered_classes.append(f"{cls.__name__}.{attr_name}")

        except Exception as e:
            _logger.error(f"Failed to bootstrap tool class {cls.__name__}: {e}")

    def list_registered(self) -> List[str]:
        return self._registered_classes

class MCPTool(BaseTool):
    pass