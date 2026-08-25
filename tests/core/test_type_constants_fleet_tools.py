"""Tests for fleet dispatch / skill tool classification constants."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]


def test_fleet_dispatch_tools_constant_exists() -> None:
    """FLEET_DISPATCH_TOOLS is a frozenset of exactly the 4 fleet-dispatch discovery tools."""
    from autoskillit.core import FLEET_DISPATCH_TOOLS

    assert FLEET_DISPATCH_TOOLS == frozenset(
        {
            "list_recipes",
            "load_recipe",
            "fetch_github_issue",
            "get_issue_title",
        }
    )


def test_fleet_menu_tools_in_type_constants() -> None:
    """FLEET_MENU_TOOLS must live in core._type_constants, not fleet.__init__."""
    from autoskillit.core.types._type_constants_registries import FLEET_MENU_TOOLS

    assert isinstance(FLEET_MENU_TOOLS, tuple)
    assert "dispatch_food_truck" in FLEET_MENU_TOOLS


def test_fleet_menu_tools_not_in_fleet_init() -> None:
    """FLEET_MENU_TOOLS must no longer be exported from fleet.__init__."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import autoskillit.fleet as f; print(hasattr(f, 'FLEET_MENU_TOOLS'))",
        ],
        env=production_interpreter_env(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Subprocess failed:\n{result.stderr}"
    assert result.stdout.strip() == "False", (
        "FLEET_MENU_TOOLS still lives in fleet.__init__; move it to core._type_constants"
    )


def test_fleet_tools_matches_expected() -> None:
    """FLEET_TOOLS must match a hardcoded expected set — not derived from tags."""
    from autoskillit.core import FLEET_TOOLS

    expected = frozenset(
        {
            "batch_cleanup_clones",
            "get_pipeline_report",
            "get_token_summary",
            "get_timing_summary",
            "get_quota_events",
            "dispatch_food_truck",
            "record_gate_dispatch",
            "reset_dispatch",
        }
    )
    assert FLEET_TOOLS == expected, "Update expected set when FLEET_TOOLS changes"


def test_skill_tools_matches_expected() -> None:
    """SKILL_TOOLS must match a hardcoded expected set."""
    from autoskillit.core import SKILL_TOOLS

    assert SKILL_TOOLS == frozenset({"run_skill"})


def test_config_authority_keys_constant() -> None:
    """CONFIG_AUTHORITY_KEYS must contain exactly the six config-authority keys."""
    from autoskillit.core import CONFIG_AUTHORITY_KEYS

    assert isinstance(CONFIG_AUTHORITY_KEYS, frozenset)
    assert CONFIG_AUTHORITY_KEYS == frozenset(
        {
            "source_dir",
            "base_branch",
            "local_review_rounds",
            "adversarial_review_level",
            "is_fleet_dispatch",
            "dispatch_id",
        }
    )
