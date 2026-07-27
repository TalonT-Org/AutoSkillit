"""Ratchet: every upstream-neutralized Codex limit finding must be disclosed in the ADRs.

Derived from CODEX_LIMIT_VERIFICATION_REGISTRY so a future verification that flips a
status automatically requires — and self-checks — the corresponding ADR disclosure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.execution.backends._codex_config import CODEX_LIMIT_VERIFICATION_REGISTRY

pytestmark = [pytest.mark.layer("docs"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_neutralized_limits_are_disclosed_in_the_governing_adrs() -> None:
    adr_0004 = (REPO_ROOT / "docs/decisions/0004-recipe-redelivery.md").read_text()
    adr_0005 = (REPO_ROOT / "docs/decisions/0005-output-budget-protocol.md").read_text()
    neutralized = [
        entry
        for entry in CODEX_LIMIT_VERIFICATION_REGISTRY.values()
        if entry.status == "upstream_neutralized"
    ]
    assert neutralized, "expected at least one upstream_neutralized entry to disclose"
    for entry in neutralized:
        value = entry.upstream_effective_value
        assert value is not None
        rendered = {str(value), f"{value:,}"}
        for adr_name, adr_text in (("0004", adr_0004), ("0005", adr_0005)):
            assert entry.governed_symbol in adr_text, (
                f"ADR-{adr_name} does not disclose {entry.governed_symbol}"
            )
            assert any(r in adr_text for r in rendered), (
                f"ADR-{adr_name} does not disclose the effective value "
                f"({rendered}) for {entry.governed_symbol}"
            )
