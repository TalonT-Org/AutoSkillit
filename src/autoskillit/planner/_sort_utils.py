"""Natural sort key — shared within planner/."""

from __future__ import annotations

import re

_NATURAL_SORT_RE = re.compile(r"(\d+)")


def _natural_sort_key(s: str) -> list[int | str]:
    return [int(tok) if tok.isdigit() else tok for tok in _NATURAL_SORT_RE.split(s)]
