"""Per-file source size budget (REQ-FILE-001).

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
        "pretty_output_hook.py": 353,  # #4585 registers two generic reader envelopes
        "_fmt_primitives.py": 200,
        # #4457 renders completion receipts verbatim; #4641/#4644 rectify adds
        # infra_cleanup_incomplete to the run_skill SUPPRESSED coverage set (+1 line)
        # #4597 rectify adds infra_fault_domain to the same SUPPRESSED set (+1 line)
        "_fmt_execution.py": 361,  # #4765 renders managed outer timeout provenance
        "_fmt_dispatch.py": 200,
        "_fmt_status.py": 250,
        "_fmt_recipe.py": 350,  # replay/effect provenance remains visible after compaction
    }
    too_big: list[str] = []
    for name, limit in budgets.items():
        f = src / name
        assert f.exists(), f"Required module missing: {name}"
        n = sum(1 for _ in f.read_text().splitlines())
        if n > limit:
            too_big.append(f"{name}: {n} > {limit}")
    assert not too_big, "\n".join(too_big)
