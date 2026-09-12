"""One-way-import guard for the session-skill and projected-artifact decomposition.

External modules (anything outside ``autoskillit.workspace``) may import from
the two facades (``autoskillit.workspace.session_skills`` and
``autoskillit.workspace._projected_artifact.materialization``) only — submodule
paths are internal. ``TYPE_CHECKING``-guarded imports are excluded (consistent
with REQ-ARCH-001 semantics).

Within ``autoskillit.workspace``, additional rules apply:

1. Session-skill shards may NOT import from their own
   ``autoskillit.workspace.session_skills`` facade; they reach siblings via
   module-scope alias imports. This also holds for ``_projection`` — issue
   #4989 relocated it from the flat, workspace-root ``skill_projection.py``
   into ``session_skills/_projection.py``, so it is now a private member of
   the same package and subject to the same own-facade prohibition.

2. Projected-artifact shards may NOT import from their own
   ``_projected_artifact.materialization`` facade; they reach siblings via
   module-scope alias imports.

3. The ``_provider``/``_materialization`` session shards ARE allowed to
   import the sibling ``_projection`` gateway shard directly. Before #4989
   this was framed as "the cross-subsystem ``skill_projection`` facade" —
   the fan-in restriction itself is unchanged, only its framing: ``_projection``
   is now a private sibling inside the same package, not an external facade,
   so no other module (inside or outside the package) may import it.

4. ``TYPE_CHECKING``-guarded imports are excluded everywhere — owner types
   referenced only for annotations may use ``TYPE_CHECKING`` to break the
   one-way rule.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.arch._helpers import (
    SRC_ROOT,
    _install_parse_counter,
    _runtime_import_froms,
    _runtime_imports,
    _write_source,
)

pytestmark = [pytest.mark.small]

_FORBIDDEN_EXTERNAL_SHARDS: frozenset[str] = frozenset(
    {
        "autoskillit.workspace.session_skills._catalog",
        "autoskillit.workspace.session_skills._provider",
        "autoskillit.workspace.session_skills._lifecycle",
        "autoskillit.workspace.session_skills._materialization",
        "autoskillit.workspace.session_skills._manager",
        "autoskillit.workspace.session_skills._projection",
        "autoskillit.workspace._projected_artifact._documents",
        "autoskillit.workspace._projected_artifact._publication",
        "autoskillit.workspace._projected_artifact._validation",
    }
)

_SESSION_SKILL_SHARDS: frozenset[str] = frozenset(
    {
        "autoskillit.workspace.session_skills._catalog",
        "autoskillit.workspace.session_skills._provider",
        "autoskillit.workspace.session_skills._lifecycle",
        "autoskillit.workspace.session_skills._materialization",
        "autoskillit.workspace.session_skills._manager",
    }
)
# _projection is a gateway shard within the same package: subject to the same
# own-facade prohibition as the five shards above (rule 1), but tracked
# separately from _SESSION_SKILL_SHARDS because its permitted-caller rule
# (rule 3) is asymmetric — only _provider/_materialization may import it.
_PROJECTION_SHARD: str = "autoskillit.workspace.session_skills._projection"
_PROJECTED_ARTIFACT_SHARDS: frozenset[str] = frozenset(
    {
        "autoskillit.workspace._projected_artifact._documents",
        "autoskillit.workspace._projected_artifact._publication",
        "autoskillit.workspace._projected_artifact._validation",
    }
)
_ALLOWED_FACADES: frozenset[str] = frozenset(
    {
        "autoskillit.workspace.session_skills",
        "autoskillit.workspace._projected_artifact.materialization",
    }
)


def _import_violation_message(violations: list[str], header: str) -> str:
    return f"{header}:\n" + "\n".join(f"  {v}" for v in violations)


def _collect_external_session_shard_import_violations() -> list[str]:
    """Loop body shared by the guard test and its parse-count meta-test."""
    violations: list[str] = []
    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        rel = py_file.relative_to(SRC_ROOT)
        parts = rel.parts
        if not parts:
            continue
        if parts[0] == "workspace":
            continue
        import_froms, plain_imports = _runtime_imports(py_file)
        for import_from in import_froms:
            module = import_from.module or ""
            if module in _FORBIDDEN_EXTERNAL_SHARDS:
                violations.append(
                    f"{rel}:{import_from.lineno} imports from forbidden shard {module!r}; "
                    f"import from one of the facades: {sorted(_ALLOWED_FACADES)}"
                )
        for plain_import in plain_imports:
            for name_alias in plain_import.names:
                if name_alias.name in _FORBIDDEN_EXTERNAL_SHARDS or any(
                    name_alias.name.startswith(f"{shard}.") for shard in _FORBIDDEN_EXTERNAL_SHARDS
                ):
                    violations.append(
                        f"{rel}:{plain_import.lineno} imports forbidden shard "
                        f"{name_alias.name!r}; "
                        f"import from one of the facades: {sorted(_ALLOWED_FACADES)}"
                    )
    return violations


def test_no_external_module_imports_session_skill_shards_directly() -> None:
    """No module outside ``autoskillit.workspace`` may import a session-skill shard path."""
    violations = _collect_external_session_shard_import_violations()
    assert not violations, _import_violation_message(
        violations, "External modules must not import session-skill shard paths directly"
    )


def _shard_file_path(shard_module: str) -> Path:
    """Resolve a dotted ``autoskillit....`` module path to its file, under SRC_ROOT.

    ``SRC_ROOT`` already points at ``src/autoskillit``, so the leading
    ``autoskillit.`` segment must be stripped before joining — keeping it
    would look for the file under a nonexistent nested
    ``src/autoskillit/autoskillit/...`` directory and silently no-op every
    caller's ``if not py_file.exists(): continue`` guard.
    """
    assert shard_module.startswith("autoskillit."), shard_module
    rel_path = shard_module.removeprefix("autoskillit.").replace(".", "/") + ".py"
    return SRC_ROOT / rel_path


def _facade_package_dir(facade_module: str) -> Path:
    """Directory a bare ``from . import X`` resolves against, for a file inside it."""
    rel = facade_module.removeprefix("autoskillit.").replace(".", "/")
    return SRC_ROOT / rel


def _own_facade_import_lines(py_file: Path, facade_module: str) -> list[int]:
    """Line numbers where ``py_file`` reaches ``facade_module``, by any import form.

    Four spellings all bind the facade and must all be caught:
    ``from <facade> import X``, ``import <facade> [as x]``,
    ``from <parent package> import <facade stem> [as x]``, and a bare
    same-package relative ``from . import <name>`` written by a file that
    lives directly inside the facade's own package directory. For such a
    file a bare ``.`` resolves to the facade module itself, so importing any
    name through it reaches the facade exactly as if written out in full.
    Legitimate sibling-module imports (``from ._catalog import X``) are
    unaffected — they name a submodule after the dot, not a bare package.
    """
    parent_package, _, facade_stem = facade_module.rpartition(".")
    facade_dir = _facade_package_dir(facade_module)
    lines: list[int] = []
    import_froms, plain_imports = _runtime_imports(py_file)
    for import_from in import_froms:
        module = import_from.module or ""
        if module == facade_module:
            lines.append(import_from.lineno)
        elif module == parent_package and any(
            alias.name == facade_stem for alias in import_from.names
        ):
            lines.append(import_from.lineno)
        elif not module and import_from.level == 1 and py_file.parent == facade_dir:
            lines.append(import_from.lineno)
    for plain_import in plain_imports:
        if any(alias.name == facade_module for alias in plain_import.names):
            lines.append(plain_import.lineno)
    return sorted(lines)


def test_no_projected_artifact_shard_imports_its_own_facade() -> None:
    """Projected-artifact shards must not import their own ``materialization`` facade.

    The facade is the public surface; internal cross-shard references must
    use module-scope facade aliases or sibling imports — never the original
    materialization facade module.
    """
    facade = "autoskillit.workspace._projected_artifact.materialization"
    violations: list[str] = []
    for shard_module in sorted(_PROJECTED_ARTIFACT_SHARDS):
        py_file = _shard_file_path(shard_module)
        if not py_file.exists():
            continue
        for lineno in _own_facade_import_lines(py_file, facade):
            violations.append(
                f"{shard_module}:{lineno} imports its own "
                f"materialization facade; reach the symbol via a sibling shard"
            )
    assert not violations, _import_violation_message(
        violations, "Projected-artifact shards must not import their own facade"
    )


def test_no_session_skill_shard_imports_its_own_facade() -> None:
    """Session-skill shards, including the _projection gateway, must not import
    their own ``session_skills`` facade."""
    facade = "autoskillit.workspace.session_skills"
    violations: list[str] = []
    for shard_module in sorted(_SESSION_SKILL_SHARDS | {_PROJECTION_SHARD}):
        py_file = _shard_file_path(shard_module)
        if not py_file.exists():
            continue
        for lineno in _own_facade_import_lines(py_file, facade):
            violations.append(
                f"{shard_module}:{lineno} imports its own "
                f"session_skills facade; reach the symbol via a sibling shard"
            )
    assert not violations, _import_violation_message(
        violations, "Session-skill shards must not import their own facade"
    )


def test_provider_and_materialization_may_use_projection_gateway() -> None:
    """Only _provider/_materialization may import the _projection gateway shard.

    Confirms the private-sibling allowance. If this test fires, either a
    shard that should not import _projection has started to, or the
    allowance was misapplied to an unrelated shard.
    """
    allowed_callers = {
        "autoskillit.workspace.session_skills._provider",
        "autoskillit.workspace.session_skills._materialization",
    }
    violations: list[str] = []
    for shard_module in sorted(_SESSION_SKILL_SHARDS | _PROJECTED_ARTIFACT_SHARDS):
        py_file = _shard_file_path(shard_module)
        if not py_file.exists():
            continue
        for import_from in _runtime_import_froms(py_file):
            module = import_from.module or ""
            if module == _PROJECTION_SHARD and shard_module not in allowed_callers:
                violations.append(
                    f"{shard_module}:{import_from.lineno} imports the _projection "
                    f"gateway shard; only _provider/_materialization may use it"
                )
    assert not violations, _import_violation_message(
        violations, "_projection gateway shard must only be consumed by allowed shards"
    )


def test_external_session_shard_guard_parses_each_inspected_file_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_source(tmp_path, "server/handler.py", "import os\n")
    _write_source(
        tmp_path, "cli/main.py", "from autoskillit.workspace.session_skills import Catalog\n"
    )
    _write_source(tmp_path, "workspace/session_skills/_catalog.py", "import os\n")
    monkeypatch.setattr(sys.modules[__name__], "SRC_ROOT", tmp_path)
    counter = _install_parse_counter(monkeypatch)

    _collect_external_session_shard_import_violations()

    inspected = [
        p for p in tmp_path.rglob("*.py") if p.relative_to(tmp_path).parts[0] != "workspace"
    ]
    assert counter[0] == len(inspected) == 2


def test_external_session_shard_guard_flags_both_import_forms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_source(
        tmp_path,
        "server/handler.py",
        "from autoskillit.workspace.session_skills._catalog import Catalog\n"
        "import autoskillit.workspace._projected_artifact._documents as docs\n",
    )
    monkeypatch.setattr(sys.modules[__name__], "SRC_ROOT", tmp_path)

    with pytest.raises(AssertionError) as excinfo:
        test_no_external_module_imports_session_skill_shards_directly()

    message = str(excinfo.value)
    from_form = "server/handler.py:1 imports from forbidden shard "
    plain_form = "server/handler.py:2 imports forbidden shard "
    assert from_form + "'autoskillit.workspace.session_skills._catalog'" in message
    assert plain_form + "'autoskillit.workspace._projected_artifact._documents'" in message


def test_own_facade_import_lines_parses_once_and_sees_all_spellings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    facade = "autoskillit.workspace._projected_artifact.materialization"
    shard = tmp_path / "shard.py"
    shard.write_text(
        f"from {facade} import publish\n"
        f"import {facade}\n"
        "from autoskillit.workspace._projected_artifact import materialization\n"
        "from autoskillit.workspace._projected_artifact import _documents\n"
    )
    counter = _install_parse_counter(monkeypatch)

    assert _own_facade_import_lines(shard, facade) == [1, 2, 3]
    assert counter[0] == 1


def test_own_facade_import_lines_catches_bare_relative_self_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A relocated shard's bare ``from . import <export>`` is an own-facade import.

    Once ``session_skills.py`` becomes ``session_skills/__init__.py``, a shard
    sitting directly inside that package (e.g. ``_catalog.py``) can spell a
    self-import of the facade's own reexported name as a bare relative import
    instead of the fully-qualified form. The detector must catch this
    spelling too, without rejecting a legitimate sibling-module import like
    ``from ._provider import SkillsDirectoryProvider`` (module named after the
    dot, not a bare package import).
    """
    facade = "autoskillit.workspace.session_skills"
    monkeypatch.setattr(sys.modules[__name__], "SRC_ROOT", tmp_path)
    shard_dir = tmp_path / "workspace" / "session_skills"
    shard_dir.mkdir(parents=True)

    own_facade_shard = shard_dir / "_catalog.py"
    own_facade_shard.write_text("from . import compile_session_skill_catalog\n")
    assert _own_facade_import_lines(own_facade_shard, facade) == [1]

    legitimate_sibling_shard = shard_dir / "_manager.py"
    legitimate_sibling_shard.write_text("from ._provider import SkillsDirectoryProvider\n")
    assert _own_facade_import_lines(legitimate_sibling_shard, facade) == []
