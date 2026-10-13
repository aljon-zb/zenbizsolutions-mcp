import logging
import logging.config
from app.core import config

def init_logging():
    LOGGING_CONFIG = {
            "version": 1,
            "disable_existing_loggers": False, # Keeps pre-existing loggers active
            "formatters": {
                "mcp_formatter": {
                    "format": f"[%(asctime)s] %(levelname)s [{config['client_id']}] %(name)s: %(message)s",
                    "datefmt": "%H:%M:%S",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "mcp_formatter",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {
                "level": config['log_level'] or logging.INFO,
                "handlers": ["console"],
            },
        }
    logging.config.dictConfig(LOGGING_CONFIG)
