import pkgutil
import os.path

__path__ = [
    os.path.abspath(path)
    for path in pkgutil.extend_path(__path__, __name__)
]

from . import core
from . import base
from . import cli
from . import service
from . import addons
from . import tests