"""Lazy gateway for the canonical recipe API implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import _api as _api
    from . import _api_cache as _api_cache
    from . import _api_listing as _api_listing
    from ._api import (
        format_recipe_list_response,
        list_all,
        load_and_validate,
        validate_from_path,
    )


def __getattr__(name: str) -> object:
    """Lazily resolve API symbols and concrete child modules."""
    if name in _LAZY_MODULES:
        from importlib import import_module

        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    module_name = _LAZY_SYMBOL_TO_MODULE.get(name)
    if module_name is not None:
        from importlib import import_module

        module = import_module(f"{__name__}.{module_name}")
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Return discoverable names without resolving lazy attributes."""
    return sorted(set(globals()) | set(__all__) | _LAZY_MODULES)


_LAZY_MODULES: frozenset[str] = frozenset({"_api", "_api_cache", "_api_listing"})

_LAZY_SYMBOL_TO_MODULE: dict[str, str] = {
    "load_and_validate": "_api",
    "list_all": "_api",
    "format_recipe_list_response": "_api",
    "validate_from_path": "_api",
}

__all__ = [
    "load_and_validate",
    "list_all",
    "format_recipe_list_response",
    "validate_from_path",
]
