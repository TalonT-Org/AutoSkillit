#!/usr/bin/env python3
"""SessionStart hook — injects /autoskillit:open-kitchen reminder on session resume.

Detects resume by checking whether the transcript file at transcript_path has content.
An empty or non-existent transcript means a fresh session; a non-empty transcript means
a resumed session that needs kitchen tools restored.

Stdlib-only — runs under any Python interpreter without the autoskillit package.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        sys.exit(0)  # fail-open on malformed input

    transcript_path = data.get("transcript_path", "")
    if not transcript_path:
        sys.exit(0)

    try:
        size = Path(transcript_path).stat().st_size
    except OSError:
        sys.exit(0)  # fail-open if file is unreadable or missing

    if size == 0:
        sys.exit(0)  # fresh session — no reminder needed

    payload = json.dumps(
        {
            "additionalContext": (
                "RESUME REMINDER: You are resuming a previous AutoSkillit session. "
                "MCP tool access (kitchen) is not automatically restored on resume. "
                "Call /autoskillit:open-kitchen first to regain access to all "
                "AutoSkillit MCP tools before continuing your work."
            )
        }
    )
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
