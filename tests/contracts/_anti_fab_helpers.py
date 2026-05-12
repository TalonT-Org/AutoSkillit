"""Shared anti-fabrication regex for contract tests — mirrors production guard pattern."""

from __future__ import annotations

import re

FABRICATION_GUARD_RE = re.compile(
    r"(?i)(?:fabricat|embellish|invent|hallucinat|attribute.*missing.*to)",
)
