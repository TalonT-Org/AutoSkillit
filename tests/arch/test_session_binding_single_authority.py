"""Architecture checks for the hook-side session-binding authority."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_HOOKS_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks"


def _constructs_binding_flag_filename(node: ast.JoinedStr) -> bool:
    literals = "".join(
        value.value
        for value in node.values
        if isinstance(value, ast.Constant) and isinstance(value.value, str)
    )
    return "skill_guard_" in literals and ".flag" in literals and "_denials" not in literals


def test_no_module_recomputes_the_binding_path() -> None:
    """Only the authority may build a binding-flag filename.

    The server tool remains outside this scan because its reader moves to this
    authority in the next implementation part.
    """
    violations: list[str] = []
    for source_path in _HOOKS_ROOT.rglob("*.py"):
        tree = ast.parse(source_path.read_text())
        if source_path.name == "_session_binding.py":
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr) and _constructs_binding_flag_filename(node):
                violations.append(str(source_path.relative_to(_HOOKS_ROOT.parent)))

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
