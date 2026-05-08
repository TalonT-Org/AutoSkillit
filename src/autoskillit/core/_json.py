"""Fast JSON loading/dumping via orjson with stdlib fallback."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

try:
    import orjson as _orjson

    _USE_ORJSON: bool = True

    def fast_loads(s: str | bytes) -> Any:
        """Raises orjson.JSONDecodeError (a subclass of json.JSONDecodeError) on invalid input."""
        return _orjson.loads(s)

    def fast_dumps(
        obj: Any,
        *,
        sort_keys: bool = False,
        indent: bool = False,
        default: Callable[[Any], Any] | None = None,
    ) -> str:
        opts = 0
        if sort_keys:
            opts |= _orjson.OPT_SORT_KEYS
        if indent:
            opts |= _orjson.OPT_INDENT_2
        return _orjson.dumps(obj, option=opts or None, default=default).decode("utf-8")

except ImportError:
    _USE_ORJSON: bool = False  # type: ignore[no-redef]

    def fast_loads(s: str | bytes) -> Any:  # type: ignore[misc]
        """Raises json.JSONDecodeError on invalid input."""
        if isinstance(s, bytes):
            s = s.decode("utf-8")
        return json.loads(s)

    def fast_dumps(  # type: ignore[misc]
        obj: Any,
        *,
        sort_keys: bool = False,
        indent: bool = False,
        default: Callable[[Any], Any] | None = None,
    ) -> str:
        return json.dumps(
            obj,
            sort_keys=sort_keys,
            indent=2 if indent else None,
            default=default,
        )
