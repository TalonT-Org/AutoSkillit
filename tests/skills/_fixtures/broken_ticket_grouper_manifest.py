"""Verbatim #4610-producing grouping manifest used by the live-behavior probe.

Stored in a sibling ``.md`` file (``broken_ticket_grouper_manifest.md``) so the
text keeps its raw newlines and indentation intact; this module re-exports it
as a string for the live-behavior test.

Keeping the fixture in a separate file means the file-wide E501 ignore in
``pyproject.toml`` for ``test_ticket_grouper_live_behavior.py`` only has to
cover that single test module — the long manifest lines live here and remain
subject to the project's 99-char limit.
"""

from __future__ import annotations

from pathlib import Path

_FIXTURE_PATH = Path(__file__).parent / "broken_ticket_grouper_manifest.md"
BROKEN_MANIFEST = _FIXTURE_PATH.read_text(encoding="utf-8")
