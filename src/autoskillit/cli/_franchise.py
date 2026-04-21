"""Franchise error envelope rendering for CLI consumers."""

from __future__ import annotations

import json
import sys


def render_franchise_error(envelope_json: str) -> int:
    """Render a franchise error envelope to stderr.

    Returns exit code: 3 for franchise envelope errors, 0 for non-error envelopes.
    """
    try:
        data = json.loads(envelope_json)
    except (json.JSONDecodeError, TypeError):
        return 0
    if data.get("success") is not False:
        return 0
    msg = data.get("user_visible_message") or data.get("error", "unknown error")
    code = data.get("error", "")
    sys.stderr.write(f"franchise error [{code}]: {msg}\n")
    details = data.get("details")
    if details:
        sys.stderr.write(f"  details: {json.dumps(details)}\n")
    return 3
