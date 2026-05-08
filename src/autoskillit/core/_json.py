"""Fast JSON loading/dumping via orjson with stdlib fallback."""

from __future__ import annotations

import json
from typing import Any

try:
    import orjson as _orjson

    def fast_loads(s: str | bytes) -> Any:
        return _orjson.loads(s)

    def fast_dumps(
        obj: Any,
        *,
        sort_keys: bool = False,
        indent: bool = False,
        default: Any = None,
    ) -> str:
        opts = 0
        if sort_keys:
            opts |= _orjson.OPT_SORT_KEYS
        if indent:
            opts |= _orjson.OPT_INDENT_2
        return _orjson.dumps(obj, option=opts or None, default=default).decode("utf-8")

except ImportError:

    def fast_loads(s: str | bytes) -> Any:  # type: ignore[misc]
        if isinstance(s, bytes):
            s = s.decode("utf-8")
        return json.loads(s)

    def fast_dumps(  # type: ignore[misc]
        obj: Any,
        *,
        sort_keys: bool = False,
        indent: bool = False,
        default: Any = None,
    ) -> str:
        return json.dumps(
            obj,
            sort_keys=sort_keys,
            indent=2 if indent else None,
            default=default,
        )
