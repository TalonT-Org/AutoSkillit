"""Architecture checks for the hook-side session-binding authority."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"


def _constructs_binding_flag_filename(tree: ast.Module) -> bool:
    docstring_nodes = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_nodes
    ]
    has_binding_prefix = any(
        "skill_guard_" in value and "_denials" not in value and "<session_id>" not in value
        for value in literals
    )
    return has_binding_prefix and any(".flag" in value for value in literals)


@pytest.mark.parametrize(
    "source",
    [
        'filename = f"skill_guard_{session_id}.flag"',
        'filename = "skill_guard_" + session_id + ".flag"',
        'filename = "skill_guard_{}.flag".format(session_id)',
        'PREFIX = "skill_guard_"\nSUFFIX = ".flag"\nfilename = f"{PREFIX}{session_id}{SUFFIX}"',
    ],
)
def test_binding_filename_detector_covers_construction_forms(source: str) -> None:
    assert _constructs_binding_flag_filename(ast.parse(source))


def test_no_module_recomputes_the_binding_path() -> None:
    """Only the hook-side authority may build a binding-flag filename."""
    violations: list[str] = []
    for source_path in _SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(source_path.read_text())
        if source_path == _SOURCE_ROOT / "hooks" / "_session_binding.py":
            continue
        if _constructs_binding_flag_filename(tree):
            violations.append(str(source_path.relative_to(_SOURCE_ROOT)))

    assert not violations, (
        "Only hooks/_session_binding.py may construct skill_guard_<session_id>.flag: "
        f"{sorted(set(violations))}"
    )


def test_projection_manifest_schema_version_hook_copy_is_pinned_to_core() -> None:
    """The unavoidable stdlib-side schema copy stays synchronized with core."""
    from autoskillit.hooks._session_binding import (  # noqa: PLC0415
        PROJECTION_MANIFEST_SCHEMA_VERSION,
    )
    from autoskillit.workspace._projection_cache import (  # noqa: PLC0415
        PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    )

    assert PROJECTION_MANIFEST_SCHEMA_VERSION == PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION
