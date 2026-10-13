
import os

import argparse
import configparser
import dataclasses
from dataclasses import dataclass, field
from typing import Any, get_origin, get_args
from dotenv import load_dotenv
from os.path import expandvars, expanduser, abspath, realpath, normcase

class Settings:
    MCP_NAME: str = "ZENMCP v1.0 Configuration"
    MCP_DESC: str = "ZEN Business Solutions Inc."
    config_file: str = ""
    env_file: str = ""
    client_id: str = ""
    client_name: str = ""
    provider_name: str = ""
    service_name: str = ""

    website_url: str | None = None
    icon_url: str | None = None
    access_mode: str = "public"

    odoo_url: str = ""
    odoo_database: str = ""
    odoo_api_key: str = ""

    addons_path: str | None = None
    mcp_transport: str = "http"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    request_timeout_seconds: float = 30.0
    max_results: int = 100

    log_level: str = "INFO"

    allowed_hosts: list[str] = field(default_factory=list)
    allowed_origins: list[str] = field(default_factory=list)

    auth_enabled: bool = False
    auth_issuer_url: str | None = None
    auth_resource_server_url: str | None = None
    auth_jwks_url: str | None = None
    auth_audience: str | None = None

    auth_required_scopes: list[str] = field(default_factory=list)
    auth_algorithms: list[str] = field(default_factory=lambda: ["RS256"])

    # Peltier/Odoo OAuth integration settings. These are optional and do not
    # change ZenMCP behavior unless PELTIER_ENABLED=true.
    peltier_enabled: bool = False
    mcp_public_url: str | None = None
    auth_access_token_ttl_seconds: int = 3600
    auth_refresh_token_ttl_seconds: int = 2592000
    auth_code_ttl_seconds: int = 300
    permissions_enabled: bool = False
    permissions_file: str = "config/permissions.yaml"

# @dataclass(frozen=True, order=True)
class SettingsManager(Settings):
    
    @classmethod
    def load_environment(cls):

        load_dotenv()
        settings = cls()

        for field_name, field_type in Settings.__annotations__.items():
            env_name = field_name.upper()

            if env_name in os.environ:
                value = os.environ[env_name]
                setattr(
                    settings,
                    field_name,
                    cls.convert(value, field_type)
                )

        return settings
    
    @staticmethod
    def convert(value, annotation):

        origin = get_origin(annotation)
        if annotation == bool:
            return value.lower() in ("1", "true", "yes", "on")

        if annotation == int:
            return int(value)

        if annotation == float:
            return float(value)

        if annotation == str:
            return value

        if origin is list:
            return [v.strip() for v in value.split(",")]

        return value


def validate(settings):

    for name, annotation in Settings.__annotations__.items():
        value = getattr(settings, name)
        origin = get_origin(annotation)

        if origin is list:

            if not isinstance(value, list | dataclasses.Field):
                raise TypeError(f"{name} must be list")

        elif annotation == int:

            if not isinstance(value, int):
                raise TypeError(f"{name} must be int")

        elif annotation == float:

            if not isinstance(value, float):
                raise TypeError(f"{name} must be float")

        elif annotation == bool:

            if not isinstance(value, bool):
                raise TypeError(f"{name} must be bool")

        elif annotation == str:

            if not isinstance(value, str):
                raise TypeError(f"{name} must be str")

#TODO: Load the initial default value first
#      Check for configuration file on the arguments map in on self.options
#      Check for environment variable map it on self.options
#      Otherwise, take the supplied argument value on the command line


# Store the original add_argument method from the base container class
_original_add_argument = argparse._ActionsContainer.add_argument

def custom_add_argument(self, *args, **kwargs):
    # Extract your custom keyword arguments (e.g., 'custom_meta' or 'category')
    # Use kwargs.pop() so argparse doesn't see them and raise an error.
    my_default = kwargs.pop('my_default', None)
    
    # Call the original add_argument method to construct the standard Action object
    action = _original_add_argument(self, *args, **kwargs)
    
    # Attach your custom attributes directly to the returned Action object
    action.my_default = my_default
  
    return action

# Overwrite the method globally so it applies to both parsers and groups
argparse._ActionsContainer.add_argument = custom_add_argument

class MCPArgumentParser(argparse.ArgumentParser):
    def add_argument(self, *args, **kwargs):
        # Extract your custom attributes (provide a default if not passed)
        my_default = kwargs.pop('my_default', None)
        
        # Call the standard add_argument logic
        action = super().add_argument(*args, **kwargs)
        
        # Attach the custom attributes to the action object
        action.my_default = my_default
        return action

    def parse_args(self, args=None, namespace=None):

        ns = super().parse_args(args, namespace)

        for action in self._actions:

            if hasattr(action, "my_default") and action.dest != 'help':
                print(action.dest)
                value = getattr(ns, action.dest)

                if value is None:

                    setattr(
                        ns,
                        action.dest,
                        action.my_default
                    )

        return ns


class ConfigManager(object):

    def __init__(self, *args, **kwargs):
        self.options = {}
        self.cast = {}
        self.parser = parser = argparse.ArgumentParser(
            prog=SettingsManager.MCP_NAME,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
            description=SettingsManager.MCP_DESC)

        # =====================================================================
        # Custom Configuration Group
        # =====================================================================
        auth_group = parser.add_argument_group("Custom Configuration")
        auth_group.add_argument("-c", "--config-file", help="MCP configuration file")
        auth_group.add_argument("-e", "--env-file", help="MCP configuration file")
        auth_group.add_argument("-a", "--auth", help="Permission configuration file") 

        # =====================================================================
        # Client Information Group
        # =====================================================================
        
        client = parser.add_argument_group("Client Information Configurations")
        client.add_argument("-cid", "--client-id", my_default="", help="Client identifier")
        client.add_argument("-cname", "--client-name", my_default="", help="Client name")
        client.add_argument("-pname", "--provider-name",  my_default="", help="Provider name")
        client.add_argument("-sname", "--service-name", my_default="", help="Service name")
        client.add_argument("--website-url", help="Website URL")
        client.add_argument("--icon-url", help="Icon URL")
        client.add_argument("--access-mode", help="Access mode")
        
        # =====================================================================
        # Odoo Connection Group
        # =====================================================================
        odoo = parser.add_argument_group("Odoo Connection Configurations")
        odoo.add_argument("-odoo_url", "--odoo-url", help="Odoo server URL")
        odoo.add_argument("-odoo_db", "--odoo-database", help="Odoo database")
        odoo.add_argument("-odoo_key","--odoo-api-key", help="Odoo API Key")
        # =====================================================================
        # MCP Runtime Group
        # =====================================================================
        runtime = parser.add_argument_group("MCP Runtime Configurations")
        runtime.add_argument("-addons_path", "--addons-path", type=self._check_addons_path, help="MCP custom addons path")
        runtime.add_argument("-mcp_transport", "--mcp-transport", choices=["stdio", "http", "sse", "streamable-http"], my_default="http", help="Transport protocol")
        runtime.add_argument("-mcp_host", "--mcp-host", my_default="0.0.0.0", help="Bind address")
        runtime.add_argument("-mcp_port", "--mcp-port", type=int, my_default=8000, help="Listening port")
        runtime.add_argument("--request-timeout-seconds", type=float, my_default=30.0, help="Request timeout in seconds")
        runtime.add_argument("--max-results", type=int, my_default=100, help="Maximum returned records")
        runtime.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], my_default="INFO", help="Logging level")
        runtime.add_argument("--allowed-hosts", nargs="*", my_default=[], metavar="HOST", help="Allowed hosts")
        runtime.add_argument("--allowed-origins", nargs="*", my_default=[], metavar="ORIGIN", help="Allowed CORS origins")

        # =====================================================================
        # OAuth Authentication Group
        # =====================================================================
        auth = parser.add_argument_group("OAuth Authentication Configurations")
        auth.add_argument("--auth-enabled", action="store_true", help="Enable OAuth authentication")
        auth.add_argument("--auth-issuer-url", help="OAuth issuer URL")
        auth.add_argument("--auth-resource-server-url", help="OAuth Resource Server URL")
        auth.add_argument("--auth-jwks-url", help="JWKS endpoint URL")
        auth.add_argument("--auth-audience", help="JWT audience")
        auth.add_argument("--auth-required-scopes", nargs="*", my_default=[], metavar="SCOPE", help="Required OAuth scopes")
        auth.add_argument("--auth-algorithms", nargs="*", my_default=["RS256"], metavar="ALG", help="Allowed JWT algorithms")
        auth.add_argument("--auth-access-token-ttl-seconds", type=int, my_default=3600, help="OAuth access token TTL")
        auth.add_argument("--auth-refresh-token-ttl-seconds", type=int, my_default=2592000, help="OAuth refresh token TTL")
        auth.add_argument("--auth-code-ttl-seconds", type=int, my_default=300, help="OAuth authorization code TTL")

        # =====================================================================
        # Peltier integration
        # =====================================================================
        peltier = parser.add_argument_group("Peltier Odoo Integration")
        peltier.add_argument("--peltier-enabled", action="store_true", help="Enable Peltier Odoo tools/OAuth integration")
        peltier.add_argument("--mcp-public-url", help="Public MCP base URL")
        peltier.add_argument("--permissions-enabled", action="store_true", help="Enable per-user tool permissions")
        peltier.add_argument("--permissions-file", my_default="config/permissions.yaml", help="Permissions YAML path")

        for group in parser._action_groups:
            for action in group._group_actions:
                if action.dest not in self.options:
                    # TODO: Override function to have custom default arguments rather than using the default
                    self.options[action.dest] = action.my_default
                    self.cast[action.dest] = action

        self.parse_config()

    def load_config(self):
        cfg = configparser.ConfigParser()
        try:
            
            cfg.read(self.config_file)
            for (name, value) in cfg.items('options'):
                if value=='True' or value=='true':
                    value = True
                if value=='False' or value=='false':
                    value = False
                self.options[name] = value
        except IOError:
            pass
        except configparser.NoSectionError:
            pass

    def parse_config(self, args=None):
        if args is None:
            args = []

        # Compatibility aliases used by the original mcp-peltier project.
        # ZenMCP remains authoritative; aliases are mapped into its option names.
        env_aliases = {
            "TRANSPORT": "MCP_TRANSPORT",
            "HOST": "MCP_HOST",
            "PORT": "MCP_PORT",
        }
        for source, target in env_aliases.items():
            if source in os.environ and target not in os.environ:
                os.environ[target] = os.environ[source]
        # TODO: ADD environment config vs file config
        # if not env config exists load the default or the config file
        # Call the environment method instead
        opt = self.parser.parse_args(args)               
        if opt.config_file:
            # Load configuration file 
            self.config_file = os.path.abspath(
                opt.config_file
            )
            self.load_config()

        # Also check and load for an environment file
        settings = SettingsManager.load_environment()
         # validate settings attribute values
        validate(settings)

        # Map settings attribute to action.dest
        for name, annotation in Settings.__annotations__.items():
            # TODO: Map attributes to environment variables
            # We want to make sure not to map the environment settings
            # when configuration file is in the arguments
            # TODO: map the settings default on config defaults to avoid
            # overriding the configuration
            # Check if a non empty environment is loaded 
            # to avoid overriding the config file
            # We don't want to load the settings default
            # instead use the config manager default values
            if getattr(settings, name, None) is not None and name.upper() in os.environ:
                self.options[name] = getattr(settings, name)

            # Re-map the self.options with commandline arguments
            if getattr(opt, name, None) is not None and name.upper() not in os.environ:
                self.options[name] = getattr(opt, name)

        self.options['root_path'] = self._normalize(os.path.join(os.path.dirname(__file__), '..'))

        if not self.options['addons_path'] or self.options['addons_path']=='None':
            default_addons = []
            base_addons = os.path.join(self.options['root_path'], 'addons')
            if os.path.exists(base_addons):
                default_addons.append(base_addons)
            main_addons = os.path.abspath(os.path.join(self.options['root_path'], '../addons'))
           
            if os.path.exists(main_addons):
                default_addons.append(main_addons)
            self.options['addons_path'] = ",".join(default_addons)
        else:

            self.options['addons_path'] = ','.join(
                self._normalize(x)
                for x in self.options['addons_path'].split(',')
                if x.strip())
        
        # TODO: Final validations and type casting of the self.options mapping
        # for x in self.options:
        #    print('*********************')
        #    print('Value', x, self.options[x])
        #    print('*********************')
        for k in self.options:
                
            if k in self.cast and self.cast[k].type:
                self.options[k] = self.cast[k].type(self.options[k])
    
    def _is_addons_path(self, path):
        for f in os.listdir(path):
            modpath = os.path.join(path, f)
            if os.path.isdir(modpath):
                def hasfile(filename):
                    return os.path.isfile(os.path.join(modpath, filename))
                if hasfile('__init__.py'):
                    return True
        return False

    def _check_addons_path(self, value):
        ad_paths = []
        for path in value.split(','):
            path = path.strip()
            
            res = os.path.abspath(os.path.expanduser(path))
            if not os.path.isdir(res):
                raise argparse.ArgumentTypeError(f"Value {value} must be a directory")
            
            if not self._is_addons_path(res):
                raise argparse.ArgumentTypeError(f"Value {value} is not a valid addons path")
            if path:
                ad_paths.append(res)

        ad_paths = ad_paths and ','.join(ad_paths) or None
        return ad_paths

    def _normalize(self, path):
        if not path:
            return ''
        return normcase(realpath(abspath(expanduser(expandvars(path.strip())))))

    def __getitem__(self, key):
        return self.options[key]

config = ConfigManager()
