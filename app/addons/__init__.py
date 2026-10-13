import pkgutil
import os.path

__path__ = [
    os.path.abspath(path)
    for path in pkgutil.extend_path(__path__, __name__)
]

#print('Path:', __path__)
#print('Name:', __name__)
#TODO: Check addons path __Path__
from . import account_tools
