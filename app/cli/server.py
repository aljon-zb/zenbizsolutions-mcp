import sys
import os

import asyncio
import app
from app.core import config, init_app_path, get_modules
from app.base import BaseTool

from pathlib import Path
from app.tests.test_tool_register import test_get_name_tool

def main(args=None):
	# Initialize BaseTool Registry based on the given arguments
	# if no custom arguments
	if args is None:
		args = sys.argv[1:]

	# Load and parse configuration
	config.parse_config(args)

	# initialize logging
	app.service.logsrv.init_logging()
	# Initialize module path
	init_app_path()

	# initialize and run global mcp server
	server = app.service.server.start()
	# TODO : create configuration for tests module
	asyncio.run(test_get_name_tool(server))
	rc = app.service.server.run()
	sys.exit(rc)
	