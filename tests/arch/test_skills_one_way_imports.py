"""One-way-import guard for the workspace/skills decomposition (#4833).

External modules (anything outside ``autoskillit.workspace``) may import from
the two facades (``autoskillit.workspace.skills`` and
``autoskillit.workspace.skill_capabilities``) only — submodule paths are
internal. ``TYPE_CHECKING``-guarded imports are excluded (consistent with the
existing REQ-ARCH-001 semantics).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT, _install_parse_counter, _runtime_imports, _write_source

pytestmark = [pytest.mark.small]

_FORBIDDEN_SHARDS: frozenset[str] = frozenset(
    {
        "autoskillit.workspace.skills._records",
        "autoskillit.workspace.skills._overrides",
        "autoskillit.workspace.skills._exploration",
        "autoskillit.workspace.skills._visibility",
        "autoskillit.workspace.skills._frontmatter",
        "autoskillit.workspace.skills._format",
        "autoskillit.workspace.skills._resources",
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


def _collect_external_skill_shard_import_violations() -> list[str]:
    """Loop body shared by the guard test and its parse-count meta-test."""
    violations: list[str] = []
    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        rel = py_file.relative_to(SRC_ROOT)
        parts = rel.parts
        if not parts:
            continue
        # Skip the workspace package itself (its __init__.py may import from siblings).
        if parts[0] == "workspace":
            continue
        import_froms, plain_imports = _runtime_imports(py_file)
        for import_from in import_froms:
            module = import_from.module or ""
            if module in _FORBIDDEN_SHARDS:
                violations.append(
                    f"{rel}:{import_from.lineno} imports from forbidden shard {module!r}; "
                    f"import from one of the facades: {sorted(_ALLOWED_FACADES)}"
                )
            elif module == "autoskillit.workspace.skills":
                for alias in import_from.names:
                    shard = f"{module}.{alias.name}"
                    if shard in _FORBIDDEN_SHARDS:
                        violations.append(
                            f"{rel}:{import_from.lineno} imports forbidden shard {shard!r}; "
                            f"import from one of the facades: {sorted(_ALLOWED_FACADES)}"
                        )
        for plain_import in plain_imports:
            for alias in plain_import.names:
                if alias.name in _FORBIDDEN_SHARDS or any(
                    alias.name.startswith(f"{shard}.") for shard in _FORBIDDEN_SHARDS
                ):
                    violations.append(
                        f"{rel}:{plain_import.lineno} imports forbidden shard {alias.name!r}; "
                        f"import from one of the facades: {sorted(_ALLOWED_FACADES)}"
                    )
    return violations


def test_no_external_module_imports_skill_shards_directly() -> None:
    """No module outside ``autoskillit.workspace`` may import a skill shard path.

    Inspects both ``from shard import X`` (``ast.ImportFrom``) and
    ``import shard.path as alias`` / ``import shard.path`` (``ast.Import``)
    forms so the facade-only invariant is enforced regardless of import
    syntax used by callers.
    """
    violations = _collect_external_skill_shard_import_violations()
    assert not violations, (
        "External modules must not import skill shard paths directly:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_external_skill_shard_guard_parses_each_inspected_file_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_source(tmp_path, "server/handler.py", "import os\n")
    _write_source(tmp_path, "cli/main.py", "from autoskillit.workspace.skills import discover\n")
    _write_source(tmp_path, "workspace/skills/_records.py", "import os\n")
    monkeypatch.setattr(sys.modules[__name__], "SRC_ROOT", tmp_path)
    counter = _install_parse_counter(monkeypatch)

    _collect_external_skill_shard_import_violations()

    inspected = [
        p for p in tmp_path.rglob("*.py") if p.relative_to(tmp_path).parts[0] != "workspace"
    ]
    assert counter[0] == len(inspected) == 2


def test_external_skill_shard_guard_flags_both_import_forms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_source(
        tmp_path,
        "server/handler.py",
        "from autoskillit.workspace.skills._records import SkillRecord\n"
        "import autoskillit.workspace.skills._overrides as overrides\n"
        "from autoskillit.workspace.skills import _records\n",
    )
    monkeypatch.setattr(sys.modules[__name__], "SRC_ROOT", tmp_path)

    with pytest.raises(AssertionError) as excinfo:
        test_no_external_module_imports_skill_shards_directly()

    message = str(excinfo.value)
    from_form = "server/handler.py:1 imports from forbidden shard "
    plain_form = "server/handler.py:2 imports forbidden shard "
    assert from_form + "'autoskillit.workspace.skills._records'" in message
    assert plain_form + "'autoskillit.workspace.skills._overrides'" in message
    assert (
        "server/handler.py:3 imports forbidden shard 'autoskillit.workspace.skills._records'"
        in message
    )
