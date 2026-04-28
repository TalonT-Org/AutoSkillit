"""Stable display order registry for bundled recipes.

Controls the display position of bundled recipes within their group (Group 0 = Bundled
Recipes). Recipes listed here appear first in registry order; discovered recipes whose
names are absent sort alphabetically after the last registered entry.

To add a new recipe at a specific position, insert its name here. To leave it at the
bottom, add the YAML without touching this file.
"""

from __future__ import annotations

BUNDLED_RECIPE_ORDER: list[str] = [
    "implementation",
    "remediation",
    "implementation-groups",
    "merge-prs",
]
