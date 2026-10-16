"""Source adapters. Each one turns a site into a list of `Job` objects.

To add an adapter, create a module in this folder (e.g. `mysite.py`) with a class that
subclasses `Adapter`, sets `name`, implements `fetch()` and is decorated with `@register`.
Every module in this folder is imported automatically below, so nothing else needs editing.
"""

import importlib
import pkgutil

from werkbot.adapters.base import REGISTRY, Adapter, register

# Helper modules that contain no adapters
_NOT_ADAPTERS = {"base", "html"}

for _module in pkgutil.iter_modules(__path__):
    if _module.name not in _NOT_ADAPTERS and not _module.name.startswith("_"):
        importlib.import_module(f"{__name__}.{_module.name}")

__all__ = ["REGISTRY", "Adapter", "register"]
