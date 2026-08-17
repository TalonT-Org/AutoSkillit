"""Verbatim #4610-producing grouping manifest used by the live-behavior probe.

The text lives in a sibling ``.md`` file (``broken_ticket_grouper_manifest.md``)
so its raw newlines and indentation stay intact; this module re-exports it as
a string for the live-behavior test.
"""

from __future__ import annotations

from pathlib import Path

_FIXTURE_PATH = Path(__file__).parent / "broken_ticket_grouper_manifest.md"
BROKEN_MANIFEST = _FIXTURE_PATH.read_text(encoding="utf-8")
