"""One-way-import guard for the workspace/skills decomposition (#4833).

External modules (anything outside ``autoskillit.workspace``) may import from
the two facades (``autoskillit.workspace.skills`` and
``autoskillit.workspace.skill_capabilities``) only — submodule paths are
internal. ``TYPE_CHECKING``-guarded imports are excluded (consistent with the
existing REQ-ARCH-001 semantics).
"""

from __future__ import annotations

import pytest

from tests.arch._helpers import SRC_ROOT, _runtime_import_froms

pytestmark = [pytest.mark.small]

_FORBIDDEN_SHARDS: frozenset[str] = frozenset(
    {
        "autoskillit.workspace.skills_records",
        "autoskillit.workspace.skills_overrides",
        "autoskillit.workspace.skills_exploration",
        "autoskillit.workspace.skills_visibility",
        "autoskillit.workspace.skills_frontmatter",
        "autoskillit.workspace.skill_capability_cache",
        "autoskillit.workspace.skill_capability_scanner",
        "autoskillit.workspace.skill_capability_authenticity",
        "autoskillit.workspace.skill_semantic_plan",
    }
)
_ALLOWED_FACADES: frozenset[str] = frozenset(
    {
        "autoskillit.workspace.skills",
        "autoskillit.workspace.skill_capabilities",
    }
)


def test_no_external_module_imports_skill_shards_directly() -> None:
    """No module outside ``autoskillit.workspace`` may import a skill shard path."""
    violations: list[str] = []
    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        rel = py_file.relative_to(SRC_ROOT)
        parts = rel.parts
        if not parts:
            continue
        # Skip the workspace package itself (its __init__.py may import from siblings).
        if parts[0] == "workspace":
            continue
        for import_from in _runtime_import_froms(py_file):
            module = import_from.module or ""
            if module in _FORBIDDEN_SHARDS:
                violations.append(
                    f"{rel}:{import_from.lineno} imports from forbidden shard {module!r}; "
                    f"import from one of the facades: {sorted(_ALLOWED_FACADES)}"
                )
    assert not violations, (
        "External modules must not import skill shard paths directly:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
