"""One-way-import guard for the workspace/skills decomposition (#4833).

External modules (anything outside ``autoskillit.workspace``) may import from
the two facades (``autoskillit.workspace.skills`` and
``autoskillit.workspace.skill_capabilities``) only — submodule paths are
internal. ``TYPE_CHECKING``-guarded imports are excluded (consistent with the
existing REQ-ARCH-001 semantics).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT, _runtime_imports

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
    """No module outside ``autoskillit.workspace`` may import a skill shard path.

    Inspects both ``from shard import X`` (``ast.ImportFrom``) and
    ``import shard.path as alias`` / ``import shard.path`` (``ast.Import``)
    forms so the facade-only invariant is enforced regardless of import
    syntax used by callers.
    """
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
        for plain_import in plain_imports:
            for alias in plain_import.names:
                if alias.name in _FORBIDDEN_SHARDS or any(
                    alias.name.startswith(f"{shard}.") for shard in _FORBIDDEN_SHARDS
                ):
                    violations.append(
                        f"{rel}:{plain_import.lineno} imports forbidden shard {alias.name!r}; "
                        f"import from one of the facades: {sorted(_ALLOWED_FACADES)}"
                    )
    assert not violations, (
        "External modules must not import skill shard paths directly:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def _write_source(root: Path, rel: str, source: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)


def test_external_shard_guard_parses_each_inspected_file_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_source(tmp_path, "server/handler.py", "import os\n")
    _write_source(tmp_path, "cli/main.py", "from autoskillit.workspace.skills import discover\n")
    _write_source(tmp_path, "workspace/skills_records.py", "import os\n")
    monkeypatch.setattr(sys.modules[__name__], "SRC_ROOT", tmp_path)
    original_parse = ast.parse
    parse_count = 0

    def counting_parse(*args, **kwargs):
        nonlocal parse_count
        parse_count += 1
        return original_parse(*args, **kwargs)

    monkeypatch.setattr(ast, "parse", counting_parse)

    test_no_external_module_imports_skill_shards_directly()

    inspected = [
        p for p in tmp_path.rglob("*.py") if p.relative_to(tmp_path).parts[0] != "workspace"
    ]
    assert parse_count == len(inspected) == 2


def test_external_shard_guard_flags_both_import_forms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_source(
        tmp_path,
        "server/handler.py",
        "from autoskillit.workspace.skills_records import SkillRecord\n"
        "import autoskillit.workspace.skills_overrides as overrides\n",
    )
    monkeypatch.setattr(sys.modules[__name__], "SRC_ROOT", tmp_path)

    with pytest.raises(AssertionError) as excinfo:
        test_no_external_module_imports_skill_shards_directly()

    message = str(excinfo.value)
    from_form = "server/handler.py:1 imports from forbidden shard "
    plain_form = "server/handler.py:2 imports forbidden shard "
    assert from_form + "'autoskillit.workspace.skills_records'" in message
    assert plain_form + "'autoskillit.workspace.skills_overrides'" in message
