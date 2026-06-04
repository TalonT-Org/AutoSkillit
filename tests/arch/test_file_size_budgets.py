"""Per-file source size budgets (REQ-FILE-001, REQ-FILE-002).

Enforces line-count budgets for source modules where the audit identified an
oversized file and a planned split. Each entry below is the post-split
ceiling, not the natural starting size.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_pretty_output_below_budget() -> None:
    """REQ-FILE-001: hooks/pretty_output_hook.py must stay under 350 lines after
    the §4 split (audit finding 8.3). Each split formatter module must stay
    under its own budget."""
    src = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks" / "formatters"
    budgets = {
        "pretty_output_hook.py": 350,
        "_fmt_primitives.py": 200,
        "_fmt_execution.py": 329,
        "_fmt_status.py": 250,
        "_fmt_recipe.py": 300,
    }
    too_big: list[str] = []
    for name, limit in budgets.items():
        f = src / name
        assert f.exists(), f"Required module missing: {name}"
        n = sum(1 for _ in f.read_text().splitlines())
        if n > limit:
            too_big.append(f"{name}: {n} > {limit}")
    assert not too_big, "\n".join(too_big)


_WARNING_ZONE_BUDGETS: dict[str, int] = {
    "execution/clone_guard.py": 750,
    "execution/github.py": 750,
    "execution/session_log.py": 750,
    "fleet/state.py": 750,
    "recipe/io.py": 750,
    "server/tools/tools_git.py": 750,
    "server/tools/tools_github.py": 750,
}


def test_warning_zone_files_under_750_lines() -> None:
    """REQ-FILE-002: Warning-zone files must not exceed 750 lines.

    Tracked files identified by arch audit P8-F09. When a file needs to grow
    beyond 750 lines, split it first (see parent issue #2832).
    """
    src = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
    too_big: list[str] = []
    for rel_path, limit in sorted(_WARNING_ZONE_BUDGETS.items()):
        f = src / rel_path
        assert f.exists(), f"Tracked warning-zone file missing: {rel_path}"
        n = len(f.read_text().splitlines())
        if n > limit:
            too_big.append(f"{rel_path}: {n} > {limit}")
    assert not too_big, (
        "Warning-zone files exceeding 750-line threshold — split before adding code "
        "(see #2832 for split patterns):\n" + "\n".join(f"  {v}" for v in too_big)
    )
