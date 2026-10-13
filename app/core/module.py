import os
from pathlib import Path

import app
from app.core import config

def init_app_path():
  
    for addons_path in config['addons_path'].split(','):
        if addons_path not in app.addons.__path__:
            app.addons.__path__.append(addons_path)

def get_modules():
	def is_qualified(adp, mod):
		path = Path(os.path.join(adp, mod))
		# Exclude hidden files and underscore prefix
		if path.is_dir() and not mod.startswith(('.', '_')):
			return True
	modules = []

	for adp in app.addons.__path__:
		modules.extend([mod for mod in os.listdir(adp) if is_qualified(adp, mod)])
	return modules
