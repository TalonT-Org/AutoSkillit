"""Single bootstrap for supported shell-capture module import spellings."""

from __future__ import annotations

import sys
from types import ModuleType

_HOOK_PACKAGE_PREFIX = "autoskillit.hooks."


def _aliases(module_name: str) -> tuple[str, str]:
    if module_name.startswith(_HOOK_PACKAGE_PREFIX):
        short_name = module_name.removeprefix(_HOOK_PACKAGE_PREFIX)
        return short_name, module_name
    return module_name, f"{_HOOK_PACKAGE_PREFIX}{module_name}"


def register_module_aliases(module_name: str) -> ModuleType:
    module = sys.modules[module_name]
    for alias in _aliases(module_name):
        existing = sys.modules.setdefault(alias, module)
        if existing is not module:
            raise RuntimeError(f"conflicting shell-capture module identity for {module_name}")
    return module


_THIS_MODULE = register_module_aliases(__name__)
