"""End-to-end chain tests for capability admission control.

Verifies the full chain from backend capability detection through
load_and_validate to the dispatch_feasible signal.
"""

from __future__ import annotations

import pytest

from autoskillit.core import BACKEND_CAPABILITY_INGREDIENTS, CAPABILITY_GATE_CALLABLES
from autoskillit.recipe._api import load_and_validate

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


_PROJECT_ROOT = pytest.importorskip("pathlib").Path(__file__).resolve().parent.parent.parent


def test_capability_ingredient_keys_match_registry() -> None:
    """BACKEND_CAPABILITY_INGREDIENTS must include backend_supports_git_write."""
    assert "backend_supports_git_write" in BACKEND_CAPABILITY_INGREDIENTS


def test_capability_gate_callables_includes_gate_backend_write() -> None:
    """CAPABILITY_GATE_CALLABLES must include gate_backend_write."""
    assert "gate_backend_write" in CAPABILITY_GATE_CALLABLES


def test_codex_backend_produces_infeasible_recipe() -> None:
    """Codex + implementation recipe chain: backend_supports_git_write=false
    produces dispatch_feasible=False with gate_backend_write infeasible."""
    result = load_and_validate(
        "implementation",
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={"backend_supports_git_write": "false"},
        backend_name="codex",
    )
    assert result.get("valid") is True
    assert result.get("dispatch_feasible") is False
    assert "gate_backend_write" in result.get("infeasible_steps", [])


def test_claude_code_backend_produces_feasible_recipe() -> None:
    """Claude Code + implementation recipe: backend_supports_git_write=true
    produces dispatch_feasible=True."""
    result = load_and_validate(
        "implementation",
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={"backend_supports_git_write": "true"},
        backend_name="claude-code",
    )
    assert result.get("valid") is True
    assert result.get("dispatch_feasible") is True
    assert "infeasible_steps" not in result
