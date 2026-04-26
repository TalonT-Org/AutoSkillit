"""Marker completeness: fleet test files carry feature('fleet'), infra tests do not.

Also contains:
- off-state smoke test: package imports cleanly regardless of feature env state
- visibility round-trip: MCP tool listing respects fleet feature state
- generic marker test: all feature-dir test files carry appropriate feature markers
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
_FLEET_CROSS_DIR_FILES: frozenset[Path] = frozenset(
    [
        _TESTS_ROOT / "cli" / "test_fleet_cli.py",
        _TESTS_ROOT / "server" / "test_tools_dispatch.py",
        _TESTS_ROOT / "cli" / "test_food_truck_prompt.py",
        _TESTS_ROOT / "cli" / "test_l3_orchestrator_prompt.py",
        _TESTS_ROOT / "cli" / "test_reap.py",
        _TESTS_ROOT / "cli" / "test_signal_guard.py",
    ]
)

# Union: all files requiring pytestmark feature("fleet")
_ALL_FLEET_FILES = [*_FLEET_DIR_FILES, *_FLEET_CROSS_DIR_FILES]

# Classes within mixed files that MUST carry @pytest.mark.feature("fleet")
_FLEET_CLASS_MARKERS: dict[str, set[str]] = {
    "server/test_server_init.py": {"TestSessionTypeVisibility", "TestFeatureGateVisibility"},
    "server/test_tools_execution.py": {"TestTierAwareGateEnforcement"},
    "cli/test_doctor.py": {"TestGroupMFranchiseDoctorChecks", "TestGroupNFeatureGateDoctorChecks"},
}

# Standalone functions within mixed files that MUST carry @pytest.mark.feature("fleet")
_FLEET_FUNC_MARKERS: dict[str, set[str]] = {
    "cli/test_reload_loop.py": {"test_fleet_reload_relaunches_without_resume"},
    "server/test_helpers_tier_guards.py": {
        "test_A11_require_fleet_permits_fleet_session",
        "test_A12_require_fleet_denies_orchestrator",
        "test_A13_require_fleet_denies_interactive_no_session_type",
    },
    "server/test_tools_recipe.py": {
        "test_list_recipes_mcp_tool_hides_campaign_when_fleet_disabled",
    },
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


def _func_has_feature_decorator(source: str, func_name: str, feature_name: str) -> bool:
    """Return True if func_name has @pytest.mark.feature(feature_name) in its decorator list."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != func_name:
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


def test_all_feature_dir_test_files_carry_feature_marker():
    """For every feature in FEATURE_REGISTRY with a tests/{name}/ directory,
    all test_*.py files carry pytest.mark.feature(name) in pytestmark."""
    from autoskillit.core import FEATURE_REGISTRY

    missing = []
    checked_count = 0
    for feat_name in FEATURE_REGISTRY:
        feat_dir = _TESTS_ROOT / feat_name
        if not feat_dir.is_dir():
            continue
        for path in sorted(feat_dir.glob("test_*.py")):
            checked_count += 1
            if not _pytestmark_has_feature(path.read_text(), feat_name):
                missing.append(f"{path.relative_to(_TESTS_ROOT)} (feature={feat_name!r})")
    assert checked_count > 0, (
        "No feature test directories found — FEATURE_REGISTRY has no tests/{name}/ directories. "
        "Add at least one feature directory under tests/ or update FEATURE_REGISTRY."
    )
    assert not missing, "Files missing pytest.mark.feature(name) in pytestmark:\n" + "\n".join(
        f"  {r}" for r in missing
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


def test_fleet_func_markers_present():
    """Specific test functions in mixed files must carry @pytest.mark.feature('fleet')."""
    missing = []
    for rel, func_names in _FLEET_FUNC_MARKERS.items():
        path = _TESTS_ROOT / rel
        assert path.exists(), f"Expected test file not found: {path}"
        source = path.read_text()
        for fn in func_names:
            if not _func_has_feature_decorator(source, fn, "fleet"):
                missing.append(f"{rel}::{fn}")
    assert not missing, "These functions are missing @pytest.mark.feature('fleet'):\n" + "\n".join(
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


def test_fleet_cross_dir_files_no_duplicates():
    paths = list(_FLEET_CROSS_DIR_FILES)
    assert len(paths) == len(set(paths)), "Duplicate paths in _FLEET_CROSS_DIR_FILES"


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
    from autoskillit.core import ALL_VISIBILITY_TAGS, FLEET_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    mcp._transforms.clear()
    for tag in sorted(ALL_VISIBILITY_TAGS):
        mcp.disable(tags={tag})

    try:
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
    finally:
        mcp._transforms.clear()
        for tag in sorted(ALL_VISIBILITY_TAGS):
            mcp.disable(tags={tag})
