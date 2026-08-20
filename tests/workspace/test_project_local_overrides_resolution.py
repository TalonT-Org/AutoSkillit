"""DefaultSkillResolver detects and applies project-local skill overrides (no projection)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.workspace._helpers import _write_effective_skill

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


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
    override_path = _write_effective_skill(
        project / ".claude" / "skills",
        "sous-chef",
        capabilities=("run_skill",),
        execution_role="orchestrator",
        body='Call run_skill("child").',
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
