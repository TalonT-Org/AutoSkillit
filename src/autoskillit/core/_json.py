"""Backward-compat shim for _json — see core.io.json."""

from autoskillit.core.io.json import _USE_ORJSON, fast_dumps, fast_loads  # noqa: F401

__all__ = ["_USE_ORJSON", "fast_dumps", "fast_loads"]
