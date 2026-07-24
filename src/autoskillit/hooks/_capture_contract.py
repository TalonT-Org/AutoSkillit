"""Shared stdlib-only transport contract for shell capture."""

from __future__ import annotations

import re

_CAPTURE_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_MAX_COMMAND_BYTES = 64 * 1024
