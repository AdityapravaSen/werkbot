"""Source adapters. Each one turns a site into a list of `Job` objects.

To add an adapter:
    1. Create a module here, e.g. `arbeitnow.py`.
    2. Subclass `Adapter`, set `name`, implement `fetch()`.
    3. Decorate the class with `@register`.
    4. Import the module below so it registers itself.
"""

from werkbot.adapters.base import REGISTRY, Adapter, register

__all__ = ["REGISTRY", "Adapter", "register"]
