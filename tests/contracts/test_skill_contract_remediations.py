"""Forcing-function guards for the skill contract remediation registry.

T7: bundled/repo-local skill hygiene at merge time (not runtime hard-fail).
T8: every SkillInvalidityKind has a registered remediation, and every
    DETERMINISTIC remediation has adapter support.
T9: a historical corpus of pre-contract skill shapes must either validate
    cleanly under the current contract or be deterministically migratable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    SKILL_CAPABILITY_REGISTRY,
    SKILL_CONTRACT_REMEDIATIONS,
    RemediationAction,
    SkillContractError,
    SkillExecutionRole,
    SkillInvalidityKind,
    SkillSource,
    SkillSourceRef,
)
from autoskillit.migration.engine import MigrationFile, SkillMigrationAdapter
from autoskillit.workspace.skills import DefaultSkillResolver, _skill_info_from_frontmatter

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORPUS_DIR = Path(__file__).parent / "fixtures" / "skill_contract_corpus"


# ---------------------------------------------------------------------------
# T7 — bundled-hygiene merge gate
# ---------------------------------------------------------------------------


def test_every_bundled_and_repo_local_skill_validates_cleanly() -> None:
    """AutoSkillit's own catalog must be strict — a merge-time pin, not a
    runtime hard-fail. Passes today only because prior hotfixes hand-fixed
    the repo's own copies; this pins that state going forward."""
    resolver = DefaultSkillResolver()
    offenders: list[str] = []

    for skill in resolver.list_all():
        if skill.invalid_reason is not None:
            offenders.append(f"bundled: {skill.name} ({skill.path}): {skill.invalid_reason}")

    repo_local_dir = _REPO_ROOT / ".claude" / "skills"
    if repo_local_dir.is_dir():
        for skill_dir in sorted(repo_local_dir.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_dir.is_dir() or not skill_md.is_file():
                continue
            info = _skill_info_from_frontmatter(
                skill_dir.name,
                SkillSource.PROJECT_LOCAL,
                skill_md,
                source_ref=SkillSourceRef(
                    origin=SkillSource.PROJECT_LOCAL,
                    logical_name=skill_dir.name,
                    skill_path=skill_md,
                ),
            )
            if info.invalid_reason is not None:
                offenders.append(f"repo-local: {info.name} ({info.path}): {info.invalid_reason}")

    assert not offenders, "Invalid skill contract(s):\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# T8 — forcing-function guards
# ---------------------------------------------------------------------------


def test_every_invalidity_kind_has_registered_remediation() -> None:
    """Adding a SkillInvalidityKind member without a registry entry must fail CI."""
    missing = sorted(set(SkillInvalidityKind) - set(SKILL_CONTRACT_REMEDIATIONS))
    assert not missing, f"SkillInvalidityKind member(s) missing a remediation: {missing}"


def test_migration_adapter_covers_every_deterministic_remediation() -> None:
    """Every DETERMINISTIC kind registered today is one of the three kinds
    SkillMigrationAdapter.migrate() has a branch for."""
    deterministic_kinds = {
        kind
        for kind, remediation in SKILL_CONTRACT_REMEDIATIONS.items()
        if remediation.action is RemediationAction.DETERMINISTIC
    }
    handled = {
        SkillInvalidityKind.UNDECLARED_CAPABILITY,
        SkillInvalidityKind.SEMANTIC_MISSING_VERSION,
        SkillInvalidityKind.SEMANTIC_UNDECLARED_TOKENS,
    }
    assert deterministic_kinds, "expected at least one DETERMINISTIC remediation kind"
    unhandled = deterministic_kinds - handled
    assert not unhandled, (
        f"SkillMigrationAdapter.migrate() has no branch for DETERMINISTIC "
        f"kind(s) {sorted(k.value for k in unhandled)} — add one before "
        "marking them DETERMINISTIC"
    )


@pytest.mark.anyio
async def test_migration_adapter_rejects_a_kind_it_does_not_know(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Meta-test: the DETERMINISTIC-coverage guard above actually has teeth.

    Temporarily marks FIELD_SHAPE (registered ADVISORY, no adapter support)
    as DETERMINISTIC and confirms migrate() refuses rather than silently
    no-oping — mirrors test_reconciler_rejects_a_shape_it_does_not_know.
    """
    skill_dir = tmp_path / ".claude" / "skills" / "broken-field-shape"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\nname: broken-field-shape\ndescription: test\ncategories: not-a-list\n---\nbody\n",
        encoding="utf-8",
    )

    patched = dict(SKILL_CONTRACT_REMEDIATIONS)
    patched[SkillInvalidityKind.FIELD_SHAPE] = patched[SkillInvalidityKind.FIELD_SHAPE]._replace(
        action=RemediationAction.DETERMINISTIC
    )
    monkeypatch.setattr("autoskillit.migration.engine.SKILL_CONTRACT_REMEDIATIONS", patched)

    file = MigrationFile(
        name="broken-field-shape", path=skill_path, file_type="skill", current_version=None
    )
    adapter = SkillMigrationAdapter()
    with pytest.raises(SkillContractError, match="no migration for"):
        await adapter.migrate(file, temp_dir=tmp_path / "temp")


def test_capability_registry_is_fully_kinded() -> None:
    """Adding a 10th capability without a matching failure-kind story must
    not silently bypass the forcing function: an unrecognized-capability
    contract violation must still resolve to UNKNOWN_CAPABILITY or
    UNDECLARED_CAPABILITY, both of which are registered kinds."""
    assert SkillInvalidityKind.UNKNOWN_CAPABILITY in SKILL_CONTRACT_REMEDIATIONS
    assert SkillInvalidityKind.UNDECLARED_CAPABILITY in SKILL_CONTRACT_REMEDIATIONS
    # SKILL_CAPABILITY_REGISTRY itself has no direct per-entry kind mapping —
    # every entry's contract violations (unknown name, missing declaration,
    # unsupported declaration) all resolve to one of the two kinds above.
    assert SKILL_CAPABILITY_REGISTRY  # non-empty: there is something to cover


# ---------------------------------------------------------------------------
# T9 — historical corpus
# ---------------------------------------------------------------------------

_CORPUS_FIXTURES = (
    "precontract_audit_bugs.md",
    "missing_semantic_version.md",
    "legacy_spawner.md",
)
# Filename -> the `name:` value declared in that fixture's own frontmatter.
_CORPUS_SKILL_NAMES = {
    "precontract_audit_bugs.md": "audit-bugs",
    "missing_semantic_version.md": "research-helper",
    "legacy_spawner.md": "legacy-spawner",
}


@pytest.mark.anyio
@pytest.mark.parametrize("fixture_name", _CORPUS_FIXTURES)
async def test_corpus_is_valid_or_deterministically_migratable(
    fixture_name: str, tmp_path: Path
) -> None:
    """Each fixture either validates cleanly today, or SkillMigrationAdapter
    transforms it so revalidation passes. Any future contract tightening
    that strands the corpus fails CI unless a remediation is registered
    and implemented."""
    source = _CORPUS_DIR / fixture_name
    assert source.is_file(), f"missing corpus fixture: {source}"
    skill_name = _CORPUS_SKILL_NAMES[fixture_name]

    skill_dir = tmp_path / ".claude" / "skills" / skill_name
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def _current_info():
        return _skill_info_from_frontmatter(
            skill_name,
            SkillSource.PROJECT_LOCAL,
            skill_path,
            source_ref=SkillSourceRef(
                origin=SkillSource.PROJECT_LOCAL,
                logical_name=skill_name,
                skill_path=skill_path,
            ),
        )

    info = _current_info()
    if info.invalid_reason is None:
        return  # validates cleanly today — nothing more to prove

    file = MigrationFile(name=skill_name, path=skill_path, file_type="skill", current_version=None)
    adapter = SkillMigrationAdapter()
    result = await adapter.migrate(file, temp_dir=tmp_path / "temp")
    assert result.success, f"{fixture_name}: migration failed: {result.error}"
    assert result.migrated_content is not None
    skill_path.write_text(result.migrated_content, encoding="utf-8")

    revalidated = _current_info()
    assert revalidated.invalid_reason is None, (
        f"{fixture_name}: still invalid after migration: {revalidated.invalid_reason}"
    )
    assert revalidated.execution_role is SkillExecutionRole.SESSION
