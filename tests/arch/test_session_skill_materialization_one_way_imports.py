"""One-way-import guard for the session-skill and projected-artifact decomposition.

External modules (anything outside ``autoskillit.workspace``) may import from
the two facades (``autoskillit.workspace.session_skills`` and
``autoskillit.workspace._projected_artifact.materialization``) only — submodule
paths are internal. ``TYPE_CHECKING``-guarded imports are excluded (consistent
with REQ-ARCH-001 semantics).

Within ``autoskillit.workspace``, additional rules apply:

1. Session-skill shards may NOT import from their own
   ``autoskillit.workspace.session_skills`` facade; they reach siblings via
   module-scope alias imports.

2. Projected-artifact shards may NOT import from their own
   ``_projected_artifact.materialization`` facade; they reach siblings via
   module-scope alias imports.

3. The session provider/materialization shards ARE allowed to use the
   cross-subsystem ``autoskillit.workspace.skill_projection`` facade.

4. ``TYPE_CHECKING``-guarded imports are excluded everywhere — owner types
   referenced only for annotations may use ``TYPE_CHECKING`` to break the
   one-way rule.
"""

from __future__ import annotations

import pytest

from tests.arch._helpers import SRC_ROOT, _runtime_import_froms, _runtime_plain_imports

pytestmark = [pytest.mark.small]

_FORBIDDEN_EXTERNAL_SHARDS: frozenset[str] = frozenset(
    {
        "autoskillit.workspace.session_skill_catalog",
        "autoskillit.workspace.session_skill_provider",
        "autoskillit.workspace.session_skill_lifecycle",
        "autoskillit.workspace.session_skill_materialization",
        "autoskillit.workspace.session_skill_manager",
        "autoskillit.workspace._projected_artifact._documents",
        "autoskillit.workspace._projected_artifact._publication",
        "autoskillit.workspace._projected_artifact._validation",
    }
)

_SESSION_SKILL_SHARDS: frozenset[str] = frozenset(
    {
        "autoskillit.workspace.session_skill_catalog",
        "autoskillit.workspace.session_skill_provider",
        "autoskillit.workspace.session_skill_lifecycle",
        "autoskillit.workspace.session_skill_materialization",
        "autoskillit.workspace.session_skill_manager",
    }
)
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
        "autoskillit.workspace.skill_projection",  # cross-subsystem facade
    }
)


def _import_violation_message(violations: list[str], header: str) -> str:
    return f"{header}:\n" + "\n".join(f"  {v}" for v in violations)


def test_no_external_module_imports_session_skill_shards_directly() -> None:
    """No module outside ``autoskillit.workspace`` may import a session-skill shard path."""
    violations: list[str] = []
    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        rel = py_file.relative_to(SRC_ROOT)
        parts = rel.parts
        if not parts:
            continue
        if parts[0] == "workspace":
            continue
        for import_from in _runtime_import_froms(py_file):
            module = import_from.module or ""
            if module in _FORBIDDEN_EXTERNAL_SHARDS:
                violations.append(
                    f"{rel}:{import_from.lineno} imports from forbidden shard {module!r}; "
                    f"import from one of the facades: {sorted(_ALLOWED_FACADES)}"
                )
        for plain_import in _runtime_plain_imports(py_file):
            for name_alias in plain_import.names:
                if name_alias.name in _FORBIDDEN_EXTERNAL_SHARDS or any(
                    name_alias.name.startswith(f"{shard}.") for shard in _FORBIDDEN_EXTERNAL_SHARDS
                ):
                    violations.append(
                        f"{rel}:{plain_import.lineno} imports forbidden shard "
                        f"{name_alias.name!r}; "
                        f"import from one of the facades: {sorted(_ALLOWED_FACADES)}"
                    )
    assert not violations, _import_violation_message(
        violations, "External modules must not import session-skill shard paths directly"
    )


def _shard_file_path(shard_module: str):
    rel_path = shard_module.replace(".", "/") + ".py"
    return SRC_ROOT / rel_path


def test_no_projected_artifact_shard_imports_its_own_facade() -> None:
    """Projected-artifact shards must not import their own ``materialization`` facade.

    The facade is the public surface; internal cross-shard references must
    use module-scope facade aliases or sibling imports — never the original
    materialization facade module.
    """
    violations: list[str] = []
    for shard_module in sorted(_PROJECTED_ARTIFACT_SHARDS):
        py_file = _shard_file_path(shard_module)
        if not py_file.exists():
            continue
        for import_from in _runtime_import_froms(py_file):
            module = import_from.module or ""
            if module == "autoskillit.workspace._projected_artifact.materialization":
                violations.append(
                    f"{shard_module}:{import_from.lineno} imports its own "
                    f"materialization facade; reach the symbol via a sibling shard "
                    f"or the cross-subsystem skill_projection facade"
                )
        for plain_import in _runtime_plain_imports(py_file):
            for name_alias in plain_import.names:
                if name_alias.name == (
                    "autoskillit.workspace._projected_artifact.materialization"
                ):
                    violations.append(
                        f"{shard_module}:{plain_import.lineno} imports its own "
                        f"materialization facade; reach the symbol via a sibling shard "
                        f"or the cross-subsystem skill_projection facade"
                    )
    assert not violations, _import_violation_message(
        violations, "Projected-artifact shards must not import their own facade"
    )


def test_no_session_skill_shard_imports_its_own_facade() -> None:
    """Session-skill shards must not import their own ``session_skills`` facade."""
    violations: list[str] = []
    for shard_module in sorted(_SESSION_SKILL_SHARDS):
        py_file = _shard_file_path(shard_module)
        if not py_file.exists():
            continue
        for import_from in _runtime_import_froms(py_file):
            module = import_from.module or ""
            if module == "autoskillit.workspace.session_skills":
                violations.append(
                    f"{shard_module}:{import_from.lineno} imports its own "
                    f"session_skills facade; reach the symbol via a sibling shard "
                    f"or the cross-subsystem skill_projection facade"
                )
        for plain_import in _runtime_plain_imports(py_file):
            for name_alias in plain_import.names:
                if name_alias.name == "autoskillit.workspace.session_skills":
                    violations.append(
                        f"{shard_module}:{plain_import.lineno} imports its own "
                        f"session_skills facade; reach the symbol via a sibling shard "
                        f"or the cross-subsystem skill_projection facade"
                    )
    assert not violations, _import_violation_message(
        violations, "Session-skill shards must not import their own facade"
    )


def test_session_provider_and_materialization_may_use_skill_projection_facade() -> None:
    """The provider and materialization shards ARE permitted to import skill_projection.

    Confirms the cross-subsystem allowance. If this test fires, either a
    session shard that should not import skill_projection has started to,
    or the cross-subsystem allowance was misapplied to an unrelated shard.
    """
    allowed_callers = {
        "autoskillit.workspace.session_skill_provider",
        "autoskillit.workspace.session_skill_materialization",
    }
    violations: list[str] = []
    for shard_module in sorted(_SESSION_SKILL_SHARDS | _PROJECTED_ARTIFACT_SHARDS):
        py_file = _shard_file_path(shard_module)
        if not py_file.exists():
            continue
        for import_from in _runtime_import_froms(py_file):
            module = import_from.module or ""
            if module == "autoskillit.workspace.skill_projection":
                if shard_module not in allowed_callers:
                    violations.append(
                        f"{shard_module}:{import_from.lineno} imports skill_projection "
                        f"facade; only provider/materialization session shards may use it"
                    )
    assert not violations, _import_violation_message(
        violations, "skill_projection facade must only be consumed by allowed shards"
    )
