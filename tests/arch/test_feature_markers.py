"""Marker completeness: franchise test files carry feature('franchise'), infra tests do not.

Also contains:
- off-state smoke test: package imports cleanly regardless of feature env state
- visibility round-trip: MCP tool listing respects franchise feature state
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from unittest.mock import patch

import pytest

_TESTS_ROOT = Path(__file__).parent.parent

# Auto-discover all test files in the franchise directory — self-maintaining
_FRANCHISE_DIR_FILES = sorted((_TESTS_ROOT / "franchise").glob("test_*.py"))

# Cross-directory franchise test files — require deliberate enumeration
_FRANCHISE_CROSS_DIR_FILES = [
    _TESTS_ROOT / "server" / "test_tools_dispatch.py",
    _TESTS_ROOT / "cli" / "test_franchise_cli.py",
    _TESTS_ROOT / "cli" / "test_food_truck_prompt.py",
    _TESTS_ROOT / "cli" / "test_l3_orchestrator_prompt.py",
    _TESTS_ROOT / "cli" / "test_reap.py",
    _TESTS_ROOT / "cli" / "test_signal_guard.py",
]

# Union: all files requiring pytestmark feature("franchise")
_ALL_FRANCHISE_FILES = [*_FRANCHISE_DIR_FILES, *_FRANCHISE_CROSS_DIR_FILES]

# Classes within mixed files that MUST carry @pytest.mark.feature("franchise")
_FRANCHISE_CLASS_MARKERS: dict[str, set[str]] = {
    "server/test_server_init.py": {"TestSessionTypeVisibility"},
}

# Infrastructure files that must NOT have a feature("franchise") pytestmark
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


def test_franchise_test_files_carry_feature_marker():
    """Every franchise-specific test file must have feature('franchise') in its pytestmark."""
    missing = []
    for path in _ALL_FRANCHISE_FILES:
        rel = path.relative_to(_TESTS_ROOT)
        assert path.exists(), f"Expected test file not found: {path}"
        if not _pytestmark_has_feature(path.read_text(), "franchise"):
            missing.append(str(rel))
    assert not missing, (
        "These files are missing pytest.mark.feature('franchise') in pytestmark:\n"
        + "\n".join(f"  {r}" for r in missing)
    )


def test_franchise_class_markers_present():
    """Specific test classes must carry @pytest.mark.feature('franchise') decorator."""
    missing = []
    for rel, class_names in _FRANCHISE_CLASS_MARKERS.items():
        path = _TESTS_ROOT / rel
        assert path.exists(), f"Expected test file not found: {path}"
        source = path.read_text()
        for cls in class_names:
            if not _class_has_feature_decorator(source, cls, "franchise"):
                missing.append(f"{rel}::{cls}")
    assert not missing, (
        "These classes are missing @pytest.mark.feature('franchise'):\n"
        + "\n".join(f"  {r}" for r in missing)
    )


def test_no_feature_marker_on_infrastructure_tests():
    """Infrastructure tests that are not franchise-exclusive must NOT carry a feature marker."""
    unexpected = []
    for rel in _INFRASTRUCTURE_FILE_EXCLUSIONS:
        path = _TESTS_ROOT / rel
        assert path.exists(), (
            f"Infrastructure exclusion list references non-existent file: {rel}\n"
            "Fix: update the path or remove the entry from _INFRASTRUCTURE_FILE_EXCLUSIONS."
        )
        if _pytestmark_has_feature(path.read_text(), "franchise"):
            unexpected.append(rel)
    assert not unexpected, (
        "Infrastructure tests must not carry feature('franchise') pytestmark:\n"
        + "\n".join(f"  {r}" for r in unexpected)
    )


def test_import_safety_with_features_disabled():
    """Top-level package and MCP server import cleanly regardless of AUTOSKILLIT_TEST_FEATURES."""
    # This is not about conditional imports (franchise is always importable);
    # it validates there are no import-time side effects that blow up when
    # a feature is not listed in AUTOSKILLIT_TEST_FEATURES.
    # Modules may already be cached in sys.modules; the assertions below confirm
    # that the package objects remain accessible without raising under patched env.
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
