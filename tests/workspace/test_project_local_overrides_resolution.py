"""DefaultSkillResolver detects and applies project-local skill overrides (no projection)."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

from tests.workspace._helpers import _write_effective_skill

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


_MAKE_PLAN_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "make-plan"
    / "SKILL.md"
)


def _weaken_make_plan_contract(content: str, weakened_requirement: str) -> str:
    if weakened_requirement == "plan":
        return re.sub(
            r"\nsemantic_version: 1\nsemantic_requirements:.*?\n---",
            "\n---",
            content,
            count=1,
            flags=re.DOTALL,
        )
    replacements = {
        "join": ("  join:\n    required: true", "  join:\n    required: false"),
        "concurrency": (
            "  concurrency:\n    required: true",
            "  concurrency:\n    required: false",
        ),
        "evidence": (
            "  evidence:\n    required: true\n    independent: true",
            "  evidence:\n    required: false\n    independent: false",
        ),
        "independent_evidence": (
            "  evidence:\n    required: true\n    independent: true",
            "  evidence:\n    required: true\n    independent: false",
        ),
    }
    old, new = replacements[weakened_requirement]
    assert old in content
    return content.replace(old, new, 1)


def _write_make_plan_override(project_root: Path, content: str) -> Path:
    path = project_root / ".claude" / "skills" / "make-plan" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _assert_weakened_make_plan_is_rejected(project_root: Path) -> None:
    from autoskillit.core import (
        SkillExecutionRole,
        SkillInvalidityKind,
        SkillSemanticOperation,
        SkillSource,
    )
    from autoskillit.execution.backends import CodexBackend
    from autoskillit.workspace import compile_session_skill_catalog
    from autoskillit.workspace.skills import DefaultSkillResolver

    resolver = DefaultSkillResolver()
    resolved = resolver.resolve_effective("make-plan", project_root)

    assert resolved is not None
    assert resolved.source is SkillSource.BUNDLED_EXTENDED

    catalog = resolver.list_effective(
        project_root,
        SkillExecutionRole.SESSION,
        cook_session=True,
    )
    exclusion = next(item for item in catalog.exclusions if item.name == "make-plan")
    assert {item.kind for item in exclusion.invalidities} == {
        SkillInvalidityKind.CONTRACT_FLOOR_WEAKENED
    }
    assert exclusion.fallback is SkillSource.BUNDLED_EXTENDED
    assert exclusion.hints == (
        "restore the bundled skill's semantic_requirements (join, concurrency, evidence) "
        "in the project-local override, or delete the override directory so the bundled "
        "definition is effective",
    )

    compilation = compile_session_skill_catalog(catalog, CodexBackend())
    unavailable = next(item for item in compilation.unavailable if item.skill == "make-plan")
    assert unavailable.operation is SkillSemanticOperation.REQUIRED_JOIN


@pytest.mark.parametrize(
    "weakened_requirement",
    ("plan", "join", "concurrency", "evidence", "independent_evidence"),
)
def test_weakened_override_is_rejected_and_bundled_twin_is_effective(
    tmp_path: Path, weakened_requirement: str
) -> None:
    """A local skill cannot lower its bundled join, concurrency, or evidence floor."""
    content = _weaken_make_plan_contract(
        _MAKE_PLAN_PATH.read_text(encoding="utf-8"), weakened_requirement
    )
    _write_make_plan_override(tmp_path, content)

    _assert_weakened_make_plan_is_rejected(tmp_path)


def test_gitignored_override_is_rejected_identically(tmp_path: Path) -> None:
    """Resolver admission inspects disk, independent of Git's tracked-file view."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    exclude_path = tmp_path / ".git" / "info" / "exclude"
    exclude_path.write_text(".claude/\n", encoding="utf-8")
    _write_make_plan_override(
        tmp_path,
        _weaken_make_plan_contract(_MAKE_PLAN_PATH.read_text(encoding="utf-8"), "plan"),
    )

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""
    _assert_weakened_make_plan_is_rejected(tmp_path)


def test_both_resolution_entry_points_apply_the_same_floor(tmp_path: Path) -> None:
    """Single-name and catalog resolution share the project-local contract floor."""
    _write_make_plan_override(
        tmp_path,
        _weaken_make_plan_contract(_MAKE_PLAN_PATH.read_text(encoding="utf-8"), "join"),
    )
    from autoskillit.core import SkillSource
    from autoskillit.workspace.skills import DefaultSkillResolver

    resolver = DefaultSkillResolver()
    resolved = resolver.resolve_effective("make-plan", tmp_path)
    skills, exclusions = resolver.scan_effective(tmp_path)

    assert resolved is not None
    assert resolved.source is SkillSource.BUNDLED_EXTENDED
    assert next(skill for skill in skills if skill.name == "make-plan").source is (
        SkillSource.BUNDLED_EXTENDED
    )
    assert [item.name for item in exclusions if item.name == "make-plan"] == ["make-plan"]


def test_matching_or_stricter_override_is_effective(tmp_path: Path) -> None:
    """A byte-identical or resource-extended local definition remains admissible."""
    from autoskillit.core import SkillSource
    from autoskillit.workspace.skills import DefaultSkillResolver

    bundled = _MAKE_PLAN_PATH.read_text(encoding="utf-8")
    resolver = DefaultSkillResolver()
    _write_make_plan_override(tmp_path, bundled)

    matching = resolver.resolve_effective("make-plan", tmp_path)
    assert matching is not None
    assert matching.source is SkillSource.PROJECT_LOCAL
    assert not resolver.scan_effective(tmp_path)[1]

    extended = bundled.replace(
        "activate_deps:\n- write-recipe\n",
        "activate_deps:\n- write-recipe\nrequires_resources:\n- arch-constraint-catalog\n",
        1,
    )
    _write_make_plan_override(tmp_path, extended)
    stricter = resolver.resolve_effective("make-plan", tmp_path)

    assert stricter is not None
    assert stricter.source is SkillSource.PROJECT_LOCAL


def test_resolve_effective_observes_new_override_without_cross_dispatch_cache(
    tmp_path, monkeypatch
):
    """A higher-priority source created between fresh dispatches is immediately effective."""
    from autoskillit.workspace.skills import DefaultSkillResolver

    bundled = tmp_path / "bundled"
    extended = tmp_path / "extended"
    project = tmp_path / "project"
    bundled.mkdir()
    extended.mkdir()
    project.mkdir()
    bundled_path = _write_effective_skill(
        bundled,
        "target",
        capabilities=("github_api_write", "open_kitchen"),
        execution_role="session",
        body="bundled body",
    )

    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", bundled)
    monkeypatch.setattr(resolver, "_extended_dir", extended)

    first = resolver.resolve_effective("target", project)
    assert first is not None
    assert first.path == bundled_path
    assert first.uses_capabilities == frozenset({"github_api_write", "open_kitchen"})

    override_path = _write_effective_skill(
        project / ".claude" / "skills",
        "target",
        capabilities=("test_check", "run_skill"),
        execution_role="orchestrator",
        body="fresh override body",
    )
    second = resolver.resolve_effective("target", project)

    assert second is not None
    assert second is not first
    assert second.path == override_path
    assert second.source.value == "project_local"
    assert second.uses_capabilities == frozenset({"test_check", "run_skill"})
    assert second.execution_role.value == "orchestrator"


def test_project_local_rewrite_reclassifies_with_process_cache(
    tmp_path, evidence_cache, scan_calls
) -> None:
    """Changed canonical bytes must bypass a resident semantic classification."""
    import autoskillit.workspace.skill_capabilities as capability_module
    from autoskillit.workspace.skills import DefaultSkillResolver

    project = tmp_path / "project"
    skill_root = project / ".claude" / "skills"
    skill_path = _write_effective_skill(
        skill_root,
        "cache-rewrite-target",
        capabilities=("test_check",),
        execution_role="session",
        body="Call `test_check()` for the first sentinel.",
    )
    resolver = DefaultSkillResolver()

    first = resolver.resolve_effective("cache-rewrite-target", project)

    assert first is not None
    assert not first.invalidities
    first_evidence = capability_module.classify_skill_capability_evidence(
        first.canonical_content,
        first.name,
    )
    assert first_evidence[0].source == "Call `test_check()` for the first sentinel."
    assert first.canonical_digest == hashlib.sha256(skill_path.read_bytes()).hexdigest()

    _write_effective_skill(
        skill_root,
        "cache-rewrite-target",
        capabilities=("test_check",),
        execution_role="session",
        body="Call `test_check()` for the second sentinel.",
    )
    second = resolver.resolve_effective("cache-rewrite-target", project)

    assert second is not None
    assert second is not first
    assert second.canonical_content != first.canonical_content
    assert second.canonical_digest != first.canonical_digest
    assert second.canonical_digest == hashlib.sha256(skill_path.read_bytes()).hexdigest()
    second_evidence = capability_module.classify_skill_capability_evidence(
        second.canonical_content,
        second.name,
    )
    assert second_evidence[0].source == "Call `test_check()` for the second sentinel."
    assert second_evidence[0].source_span == (7, 7)
    assert not second.invalidities
    assert scan_calls == [
        (first.canonical_content, "cache-rewrite-target"),
        (second.canonical_content, "cache-rewrite-target"),
    ]


def test_resolve_effective_observes_removed_override_and_falls_back(tmp_path, monkeypatch):
    """Removing a winning override exposes the lower-priority source on the next lookup."""
    from autoskillit.workspace.skills import DefaultSkillResolver

    bundled = tmp_path / "bundled"
    extended = tmp_path / "extended"
    project = tmp_path / "project"
    bundled.mkdir()
    extended.mkdir()
    project.mkdir()
    bundled_path = _write_effective_skill(
        bundled,
        "target",
        capabilities=("github_api_write", "open_kitchen"),
        execution_role="session",
        body="fallback bundled body",
    )
    override_path = _write_effective_skill(
        project / ".claude" / "skills",
        "target",
        capabilities=("test_check", "run_skill"),
        execution_role="orchestrator",
        body="temporary override body",
    )

    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", bundled)
    monkeypatch.setattr(resolver, "_extended_dir", extended)

    first = resolver.resolve_effective("target", project)
    assert first is not None
    assert first.path == override_path
    assert first.source.value == "project_local"
    assert "temporary override body" in first.canonical_content

    override_path.unlink()
    second = resolver.resolve_effective("target", project)

    assert second is not None
    assert second is not first
    assert second.path == bundled_path
    assert second.source.value == "bundled"
    assert second.source_ref is not None
    assert second.source_ref.identity.origin.value == "bundled"
    assert "fallback bundled body" in second.canonical_content
    assert "temporary override body" not in second.canonical_content


@pytest.mark.parametrize("symlink_kind", ["directory", "file"])
def test_effective_resolution_rejects_symlinked_project_overrides(
    tmp_path,
    monkeypatch,
    symlink_kind: str,
) -> None:
    from autoskillit.core import SkillExecutionRole
    from autoskillit.workspace.skills import DefaultSkillResolver

    bundled = tmp_path / "bundled"
    extended = tmp_path / "extended"
    project = tmp_path / "project"
    external = tmp_path / "external"
    bundled.mkdir()
    extended.mkdir()
    project.mkdir()
    bundled_path = _write_effective_skill(
        bundled,
        "target",
        capabilities=(),
        execution_role="session",
        body="trusted bundled body",
    )
    external_path = _write_effective_skill(
        external,
        "target",
        capabilities=("github_api_write",),
        execution_role="session",
        body="external body",
    )
    override_entry = project / ".claude" / "skills" / "target"
    override_entry.parent.mkdir(parents=True)
    if symlink_kind == "directory":
        override_entry.symlink_to(external_path.parent, target_is_directory=True)
    else:
        override_entry.mkdir()
        (override_entry / "SKILL.md").symlink_to(external_path)

    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", bundled)
    monkeypatch.setattr(resolver, "_extended_dir", extended)

    effective = resolver.resolve_effective("target", project)
    catalog = resolver.list_effective(project, SkillExecutionRole.SESSION)

    assert effective is not None
    assert effective.path == bundled_path
    assert next(skill for skill in catalog.skills if skill.name == "target").source.value == (
        "bundled"
    )


def test_effective_resolution_rejects_external_symlinked_search_root(
    tmp_path,
    monkeypatch,
) -> None:
    from autoskillit.workspace.skills import DefaultSkillResolver

    bundled = tmp_path / "bundled"
    extended = tmp_path / "extended"
    project = tmp_path / "project"
    external = tmp_path / "external"
    bundled.mkdir()
    extended.mkdir()
    project.mkdir()
    bundled_path = _write_effective_skill(
        bundled,
        "target",
        capabilities=(),
        execution_role="session",
        body="trusted bundled body",
    )
    _write_effective_skill(
        external / ".claude" / "skills",
        "target",
        capabilities=("github_api_write",),
        execution_role="session",
        body="external body",
    )
    (project / ".claude").symlink_to(
        external / ".claude",
        target_is_directory=True,
    )

    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", bundled)
    monkeypatch.setattr(resolver, "_extended_dir", extended)

    effective = resolver.resolve_effective("target", project)

    assert effective is not None
    assert effective.path == bundled_path
    assert "external body" not in effective.canonical_content


def test_effective_resolution_fails_closed_on_override_io_error(
    tmp_path,
    monkeypatch,
) -> None:
    from autoskillit.core import SkillContractError
    from autoskillit.workspace.skills import DefaultSkillResolver

    bundled = tmp_path / "bundled"
    extended = tmp_path / "extended"
    project = tmp_path / "project"
    bundled.mkdir()
    extended.mkdir()
    project.mkdir()
    _write_effective_skill(
        bundled,
        "target",
        capabilities=(),
        execution_role="session",
        body="bundled fallback must not run",
    )
    override_path = _write_effective_skill(
        project / ".claude" / "skills",
        "target",
        capabilities=(),
        execution_role="session",
        body="selected override",
    )
    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", bundled)
    monkeypatch.setattr(resolver, "_extended_dir", extended)
    original_resolve = Path.resolve

    def fail_override_resolution(path: Path, strict: bool = False) -> Path:
        if path == override_path:
            raise PermissionError("override unavailable")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_override_resolution)

    with pytest.raises(
        SkillContractError,
        match="cannot validate project-local skill 'target'",
    ):
        resolver.resolve_effective("target", project)


def test_resolve_effective_uses_one_first_match_for_policy_and_identity(tmp_path, monkeypatch):
    """Source precedence cannot mix policy metadata with bytes from a lower-priority source."""
    from autoskillit.workspace.skills import DefaultSkillResolver

    bundled = tmp_path / "bundled"
    extended = tmp_path / "extended"
    project = tmp_path / "project"
    bundled.mkdir()
    extended.mkdir()
    project.mkdir()
    _write_effective_skill(
        bundled,
        "target",
        capabilities=("open_kitchen",),
        execution_role="session",
        body="bundled",
    )
    claude_path = _write_effective_skill(
        project / ".claude" / "skills",
        "target",
        capabilities=("github_api_write",),
        execution_role="session",
        body="first match",
    )
    _write_effective_skill(
        project / ".autoskillit" / "skills",
        "target",
        capabilities=("test_check",),
        execution_role="session",
        body="lower priority",
    )

    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", bundled)
    monkeypatch.setattr(resolver, "_extended_dir", extended)
    effective = resolver.resolve_effective("target", project)

    assert effective is not None
    assert effective.path == claude_path
    assert "\nfirst match\n" in effective.path.read_text()
    assert effective.uses_capabilities == frozenset({"github_api_write"})


def test_project_local_internal_override_is_not_duplicated(tmp_path) -> None:
    from autoskillit.core import SkillExecutionRole
    from autoskillit.workspace.skills import DefaultSkillResolver

    project = tmp_path / "project"
    bundled = DefaultSkillResolver().resolve("sous-chef")
    assert bundled is not None
    override_path = project / ".claude" / "skills" / "sous-chef" / "SKILL.md"
    override_path.parent.mkdir(parents=True)
    override_path.write_text(
        bundled.canonical_content + '\nCall run_skill("child").\n',
        encoding="utf-8",
    )

    catalog = DefaultSkillResolver().list_effective(
        project,
        SkillExecutionRole.ORCHESTRATOR,
    )
    matches = [skill for skill in catalog.skills if skill.name == "sous-chef"]

    assert len(matches) == 1
    assert matches[0].source.value == "project_local"
    assert matches[0].canonical_digest
    assert '\nCall run_skill("child").\n' in override_path.read_text(encoding="utf-8")
