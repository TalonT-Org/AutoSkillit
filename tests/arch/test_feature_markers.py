"""Marker completeness: fleet test files carry feature('fleet'), infra tests do not.

Also contains:
- off-state smoke test: package imports cleanly regardless of feature env state
- visibility round-trip: MCP tool listing respects fleet feature state
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from unittest.mock import patch

import pytest

_TESTS_ROOT = Path(__file__).parent.parent

# Auto-discover all test files in the fleet directory — self-maintaining
_FLEET_DIR_FILES = sorted((_TESTS_ROOT / "fleet").glob("test_*.py"))

# Cross-directory fleet test files — require deliberate enumeration
_FLEET_CROSS_DIR_FILES = [
    _TESTS_ROOT / "cli" / "test_franchise_cli.py",
    _TESTS_ROOT / "server" / "test_tools_dispatch.py",
    _TESTS_ROOT / "cli" / "test_food_truck_prompt.py",
    _TESTS_ROOT / "cli" / "test_l3_orchestrator_prompt.py",
]

# Union: all files requiring pytestmark feature("fleet")
_ALL_FLEET_FILES = [*_FLEET_DIR_FILES, *_FLEET_CROSS_DIR_FILES]

# Classes within mixed files that MUST carry @pytest.mark.feature("fleet")
_FLEET_CLASS_MARKERS: dict[str, set[str]] = {
    "server/test_server_init.py": {"TestSessionTypeVisibility"},
}

# Infrastructure files that must NOT have a feature("fleet") pytestmark
_INFRASTRUCTURE_FILE_EXCLUSIONS = [
    "recipe/test_rules_campaign.py",
    "recipe/test_campaign_loader.py",
    "core/test_session_type.py",
    "infra/test_fleet_dispatch_guard.py",
]


def _pytestmark_has_feature(source: str, feature_name: str) -> bool:
    """Return True if module-level pytestmark contains feature(feature_name)."""
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            continue
        for subnode in ast.walk(node.value):
            if (
                isinstance(subnode, ast.Call)
                and isinstance(subnode.func, ast.Attribute)
                and subnode.func.attr == "feature"
                and subnode.args
                and isinstance(subnode.args[0], ast.Constant)
                and subnode.args[0].value == feature_name
            ):
                return True
    return False


def _class_has_feature_decorator(source: str, class_name: str, feature_name: str) -> bool:
    """Return True if class_name has @pytest.mark.feature(feature_name) in its decorator list."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "feature"
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
                and dec.args[0].value == feature_name
            ):
                return True
    return False


def test_fleet_test_files_carry_feature_marker():
    """Every fleet-specific test file must have feature('fleet') in its pytestmark."""
    missing = []
    for path in _ALL_FLEET_FILES:
        rel = path.relative_to(_TESTS_ROOT)
        assert path.exists(), f"Expected test file not found: {path}"
        if not _pytestmark_has_feature(path.read_text(), "fleet"):
            missing.append(str(rel))
    assert not missing, (
        "These files are missing pytest.mark.feature('fleet') in pytestmark:\n"
        + "\n".join(f"  {r}" for r in missing)
    )


def test_fleet_class_markers_present():
    """Specific test classes must carry @pytest.mark.feature('fleet') decorator."""
    missing = []
    for rel, class_names in _FLEET_CLASS_MARKERS.items():
        path = _TESTS_ROOT / rel
        assert path.exists(), f"Expected test file not found: {path}"
        source = path.read_text()
        for cls in class_names:
            if not _class_has_feature_decorator(source, cls, "fleet"):
                missing.append(f"{rel}::{cls}")
    assert not missing, "These classes are missing @pytest.mark.feature('fleet'):\n" + "\n".join(
        f"  {r}" for r in missing
    )


def test_no_feature_marker_on_infrastructure_tests():
    """Infrastructure tests that are not fleet-exclusive must NOT carry a feature marker."""
    unexpected = []
    for rel in _INFRASTRUCTURE_FILE_EXCLUSIONS:
        path = _TESTS_ROOT / rel
        assert path.exists(), (
            f"Infrastructure exclusion list references non-existent file: {rel}\n"
            "Fix: update the path or remove the entry from _INFRASTRUCTURE_FILE_EXCLUSIONS."
        )
        if _pytestmark_has_feature(path.read_text(), "fleet"):
            unexpected.append(rel)
    assert not unexpected, (
        "Infrastructure tests must not carry feature('fleet') pytestmark:\n"
        + "\n".join(f"  {r}" for r in unexpected)
    )


def test_import_safety_with_features_disabled():
    """Top-level package and MCP server import cleanly regardless of AUTOSKILLIT_TEST_FEATURES."""
    with patch.dict(os.environ, {"AUTOSKILLIT_TEST_FEATURES": ""}):
        import autoskillit  # noqa: F401
        from autoskillit.server import mcp  # noqa: F401

        assert autoskillit is not None
        assert mcp is not None


@pytest.mark.parametrize("fleet_enabled", [True, False])
@pytest.mark.anyio
async def test_tool_listing_matches_feature_state(fleet_enabled: bool, monkeypatch):
    """MCP tool listing includes/excludes fleet tools based on session-type feature state."""
    from autoskillit.core import FLEET_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    # Reset to known baseline: all gated tags disabled
    mcp.disable(tags={"fleet", "kitchen", "headless"})

    if fleet_enabled:
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")
    else:
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)

    _apply_session_type_visibility()

    from fastmcp.client import Client

    async with Client(mcp) as client:
        tools = await client.list_tools()
    tool_names = {t.name for t in tools}

    for name in FLEET_TOOLS:
        if fleet_enabled:
            assert name in tool_names, f"{name} should be visible when fleet enabled"
        else:
            assert name not in tool_names, f"{name} should be hidden when fleet disabled"

    # Cleanup: restore baseline
    mcp.disable(tags={"fleet", "kitchen", "headless"})
