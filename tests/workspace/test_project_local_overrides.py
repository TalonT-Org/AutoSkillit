"""Tests for project-local skill override detection and enforcement (T-OVR-001..011)."""

from __future__ import annotations

import pytest

from autoskillit.core.types import PACK_REGISTRY
from autoskillit.workspace.skills import override_names

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

# Tags for packs that are disabled by default (e.g. research, exp-lens).
# Shared by T-OVR-014 and T-OVR-017 to avoid duplication.
_DEFAULT_DISABLED_TAGS: frozenset[str] = frozenset(
    tag for tag, pack_def in PACK_REGISTRY.items() if not pack_def.default_enabled
)


def _project_skill_document(name: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: Project-local {name} fixture.\n---\n{body}\n"


# ---------------------------------------------------------------------------
# T-OVR-001..006,019..021: detect_project_local_overrides() — pure detection function
# ---------------------------------------------------------------------------


def test_detect_project_local_overrides_empty(tmp_path):
    """T-OVR-001: Returns empty frozenset when no override dirs exist."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    result = detect_project_local_overrides(tmp_path)
    assert result == frozenset()


def test_detect_project_local_overrides_claude_skills(tmp_path):
    """T-OVR-002: Detects skill in .claude/skills/<name>/SKILL.md."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    skill_dir = tmp_path / ".claude" / "skills" / "review-pr"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# review-pr")
    result = detect_project_local_overrides(tmp_path)
    assert override_names(result) == frozenset({"review-pr"})


def test_detect_project_local_overrides_autoskillit_skills(tmp_path):
    """T-OVR-003: Detects skill in .autoskillit/skills/<name>/SKILL.md."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    skill_dir = tmp_path / ".autoskillit" / "skills" / "open-pr"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# open-pr")
    result = detect_project_local_overrides(tmp_path)
    assert override_names(result) == frozenset({"open-pr"})


def test_detect_project_local_overrides_union(tmp_path):
    """T-OVR-004: Returns union from both .claude/skills/ and .autoskillit/skills/."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    for subdir, name in [
        (".claude/skills/review-pr", "review-pr"),
        (".autoskillit/skills/open-pr", "open-pr"),
    ]:
        d = tmp_path / subdir
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# skill")
    result = detect_project_local_overrides(tmp_path)
    assert override_names(result) == frozenset({"review-pr", "open-pr"})


def test_detect_project_local_overrides_ignores_missing_skill_md(tmp_path):
    """T-OVR-005: Directories without SKILL.md are ignored."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    (tmp_path / ".claude" / "skills" / "review-pr").mkdir(parents=True)
    result = detect_project_local_overrides(tmp_path)
    assert result == frozenset()


def test_detect_project_local_overrides_missing_dirs_no_crash(tmp_path):
    """T-OVR-006: Missing parent directories do not raise."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    result = detect_project_local_overrides(tmp_path / "nonexistent")
    assert result == frozenset()


def test_detect_project_local_overrides_codex_skills(tmp_path):
    """T-OVR-019: Detects skill in .codex/skills/<name>/SKILL.md."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    skill_dir = tmp_path / ".codex" / "skills" / "codex-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# codex-review")
    result = detect_project_local_overrides(tmp_path)
    assert override_names(result) == frozenset({"codex-review"})


def test_detect_project_local_overrides_agents_skills(tmp_path):
    """T-OVR-020: Detects skill in .agents/skills/<name>/SKILL.md."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    skill_dir = tmp_path / ".agents" / "skills" / "agent-deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# agent-deploy")
    result = detect_project_local_overrides(tmp_path)
    assert override_names(result) == frozenset({"agent-deploy"})


def test_detect_project_local_overrides_union_four_paths(tmp_path):
    """T-OVR-021: Returns union across all four override search dirs."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    for subdir, name in [
        (".claude/skills/review-pr", "review-pr"),
        (".autoskillit/skills/open-pr", "open-pr"),
        (".codex/skills/codex-review", "codex-review"),
        (".agents/skills/agent-deploy", "agent-deploy"),
    ]:
        d = tmp_path / subdir
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# skill")
    result = detect_project_local_overrides(tmp_path)
    assert override_names(result) == frozenset(
        {"review-pr", "open-pr", "codex-review", "agent-deploy"}
    )


def test_detect_project_local_overrides_explicit_search_dirs(tmp_path):
    """T-OVR-022: search_dirs limits detection to the supplied dirs only."""
    from autoskillit.workspace.skills import detect_project_local_overrides

    for subdir, name in [
        (".claude/skills/claude-only", "claude-only"),
        (".autoskillit/skills/as-only", "as-only"),
        (".codex/skills/codex-only", "codex-only"),
        (".agents/skills/agents-only", "agents-only"),
    ]:
        d = tmp_path / subdir
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {name}")
    result = detect_project_local_overrides(
        tmp_path, search_dirs=(".codex/skills", ".agents/skills")
    )
    assert override_names(result) == frozenset({"codex-only", "agents-only"})


def test_detect_project_local_overrides_codex_backend_scoping(tmp_path):
    """T-OVR-023: CodexBackend's convention search dirs scope detection."""
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.workspace.skills import detect_project_local_overrides

    for subdir, name in [
        (".claude/skills/claude-excluded", "claude-excluded"),
        (".codex/skills/codex-included", "codex-included"),
    ]:
        d = tmp_path / subdir
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {name}")
    result = detect_project_local_overrides(
        tmp_path, search_dirs=CodexBackend().conventions.project_local_skill_search_dirs
    )
    assert override_names(result) == frozenset({"codex-included"})


def test_detect_project_local_overrides_claude_code_backend_scoping(tmp_path):
    """T-OVR-024: ClaudeCodeBackend's convention search dirs scope detection."""
    from autoskillit.execution.backends.claude import ClaudeCodeBackend
    from autoskillit.workspace.skills import detect_project_local_overrides

    for subdir, name in [
        (".claude/skills/claude-included", "claude-included"),
        (".autoskillit/skills/as-included", "as-included"),
        (".codex/skills/codex-excluded", "codex-excluded"),
        (".agents/skills/agents-included", "agents-included"),
    ]:
        d = tmp_path / subdir
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {name}")
    result = detect_project_local_overrides(
        tmp_path, search_dirs=ClaudeCodeBackend().conventions.project_local_skill_search_dirs
    )
    assert override_names(result) == frozenset(
        {"claude-included", "as-included", "agents-included"}
    )


def _write_effective_skill(
    root,
    name,
    *,
    capabilities: tuple[str, ...],
    execution_role: str,
    body: str,
):
    skill_path = root / name / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(
        "\n".join(
            (
                "---",
                f"name: {name}",
                "description: Effective source fixture.",
                f"uses_capabilities: [{', '.join(capabilities)}]",
                f"execution_role: {execution_role}",
                "---",
                body,
                "",
            )
        )
    )
    return skill_path


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
        capabilities=("github_api_write", "agent_model"),
        execution_role="session",
        body="bundled body",
    )

    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", bundled)
    monkeypatch.setattr(resolver, "_extended_dir", extended)

    first = resolver.resolve_effective("target", project)
    assert first is not None
    assert first.path == bundled_path
    assert first.uses_capabilities == frozenset({"github_api_write", "agent_model"})

    override_path = _write_effective_skill(
        project / ".claude" / "skills",
        "target",
        capabilities=("git_metadata_write", "run_skill"),
        execution_role="orchestrator",
        body="fresh override body",
    )
    second = resolver.resolve_effective("target", project)

    assert second is not None
    assert second is not first
    assert second.path == override_path
    assert second.source.value == "project_local"
    assert second.uses_capabilities == frozenset({"git_metadata_write", "run_skill"})
    assert second.execution_role.value == "orchestrator"


def test_project_local_rewrite_reclassifies_with_process_cache(tmp_path, monkeypatch) -> None:
    """Changed canonical bytes must bypass a resident semantic classification."""
    import hashlib

    import autoskillit.workspace.skill_capabilities as capability_module
    from autoskillit.workspace.skills import DefaultSkillResolver

    cache = capability_module._SkillCapabilityEvidenceCache(
        max_entries=capability_module._SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_ENTRIES,
        max_bytes=capability_module._SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_BYTES,
        max_input_bytes=(capability_module._SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_INPUT_BYTES),
    )
    monkeypatch.setattr(
        capability_module,
        "_SKILL_CAPABILITY_EVIDENCE_CACHE",
        cache,
    )
    scan_keys: list[tuple[str, str]] = []
    original_scanner = capability_module._scan_skill_capability_evidence_uncached

    def recording_scanner(content: str, effective_name: str):
        scan_keys.append((content, effective_name))
        return original_scanner(content, effective_name)

    monkeypatch.setattr(
        capability_module,
        "_scan_skill_capability_evidence_uncached",
        recording_scanner,
    )
    project = tmp_path / "project"
    skill_root = project / ".claude" / "skills"
    skill_path = _write_effective_skill(
        skill_root,
        "cache-rewrite-target",
        capabilities=("git_metadata_write",),
        execution_role="session",
        body='git commit -m "first sentinel"',
    )
    resolver = DefaultSkillResolver()

    first = resolver.resolve_effective("cache-rewrite-target", project)

    assert first is not None
    assert first.invalid_reason is None
    first_evidence = capability_module.classify_skill_capability_evidence(
        first.canonical_content,
        first.name,
    )
    assert first_evidence[0].source == 'git commit -m "first sentinel"'
    assert first.canonical_digest == hashlib.sha256(skill_path.read_bytes()).hexdigest()

    _write_effective_skill(
        skill_root,
        "cache-rewrite-target",
        capabilities=("agent_model",),
        execution_role="session",
        body='git commit -m "second sentinel"',
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
    assert second_evidence[0].source == 'git commit -m "second sentinel"'
    assert second_evidence[0].source_span == (7, 7)
    assert second.invalid_reason is not None
    assert "missing declaration for 'git_metadata_write'" in second.invalid_reason
    assert "second sentinel" in second.invalid_reason
    assert "first sentinel" not in second.invalid_reason
    assert scan_keys == [
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
        capabilities=("github_api_write", "agent_model"),
        execution_role="session",
        body="fallback bundled body",
    )
    override_path = _write_effective_skill(
        project / ".claude" / "skills",
        "target",
        capabilities=("git_metadata_write", "run_skill"),
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
    from pathlib import Path

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
        capabilities=("agent_model",),
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
        capabilities=("git_metadata_write",),
        execution_role="session",
        body="lower priority",
    )

    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", bundled)
    monkeypatch.setattr(resolver, "_extended_dir", extended)
    effective = resolver.resolve_effective("target", project)

    assert effective is not None
    assert effective.path == claude_path
    assert effective.path.read_text().endswith("first match\n")
    assert effective.uses_capabilities == frozenset({"github_api_write"})


def test_backend_rendering_uses_the_selected_effective_source(tmp_path, monkeypatch):
    from pathlib import Path

    from autoskillit.core import BackendConventions, render_target_skill_command
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
        capabilities=("agent_model",),
        execution_role="session",
        body="bundled",
    )
    winning_path = _write_effective_skill(
        project / ".claude" / "skills",
        "target",
        capabilities=("github_api_write",),
        execution_role="session",
        body="selected override",
    )

    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", bundled)
    monkeypatch.setattr(resolver, "_extended_dir", extended)
    selected = resolver.resolve_effective("target", project)

    assert selected is not None
    assert selected.path == winning_path
    assert selected.source_ref is not None
    rendered = render_target_skill_command(
        "/autoskillit:target --flag",
        selected.source_ref,
        BackendConventions(skills_subdir=Path("skills"), skill_sigil="@"),
    )
    assert rendered == "@target --flag"


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
    assert override_path.read_text(encoding="utf-8").endswith('Call run_skill("child").\n')


def test_prepare_effective_dispatch_separates_project_root_from_cwd(tmp_path, monkeypatch) -> None:
    """L2 source selection is project-root-bound while execution stays cwd-bound."""
    from pathlib import Path

    from autoskillit.execution.backends import get_backend
    from autoskillit.workspace import prepare_effective_skill_dispatch
    from autoskillit.workspace.skills import DefaultSkillResolver

    project_root = tmp_path / "source-project"
    cwd = tmp_path / "execution-worktree"
    cwd.mkdir()
    _write_effective_skill(
        project_root / ".claude" / "skills",
        "process-issues",
        capabilities=("run_skill",),
        execution_role="orchestrator",
        body=(
            "winning project-root body\n"
            "merge {{DEFAULT_BASE_BRANCH}} from {{AUTOSKILLIT_TEMP}}\n"
            'run_skill("/test child")'
        ),
    )
    _write_effective_skill(
        cwd / ".claude" / "skills",
        "process-issues",
        capabilities=("run_skill",),
        execution_role="orchestrator",
        body='wrong execution-cwd body\nrun_skill("/test child")',
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    plugin_source, contract = prepare_effective_skill_dispatch(
        resolved_command="dispatch",
        project_root=project_root,
        cwd=cwd,
        backend=get_backend("codex"),
        resolver=DefaultSkillResolver(),
        visibility=None,
        default_base_branch=None,
        recipe_packs=None,
        recipe_features=None,
    )

    assert contract.project_root == str(project_root.resolve())
    assert contract.cwd == str(cwd.resolve())
    assert "winning project-root body" in contract.projected_artifacts["process-issues"]
    assert "wrong execution-cwd body" not in contract.projected_artifacts["process-issues"]
    assert "{{DEFAULT_BASE_BRANCH}}" not in contract.projected_artifacts["process-issues"]
    assert "{{AUTOSKILLIT_TEMP}}" not in contract.projected_artifacts["process-issues"]
    assert contract.projected_artifacts["process-issues"] == (
        plugin_source.plugin_dir / "skills" / "process-issues" / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_winning_override_identity_policy_projection_and_digests_are_atomic(
    tmp_path, monkeypatch
) -> None:
    """No bundled SkillInfo can leak after a project override wins resolution."""
    import hashlib

    import autoskillit.workspace.skills as skills_module
    from autoskillit.core import SkillExecutionRole, render_target_skill_command
    from autoskillit.workspace import (
        SkillProjectionContext,
        build_effective_skill_dispatch_contract,
        project_agent_skill_document,
    )
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
        capabilities=("agent_model",),
        execution_role="session",
        body="bundled sentinel body",
    )
    override_path = _write_effective_skill(
        project / ".claude" / "skills",
        "target",
        capabilities=("github_api_write", "git_metadata_write"),
        execution_role="session",
        body=(
            'winning override body\ngh issue edit 42 --body-file report.md\ngit commit -m "test"'
        ),
    )
    source_before = override_path.read_bytes()

    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", bundled)
    monkeypatch.setattr(resolver, "_extended_dir", extended)
    monkeypatch.setattr(skills_module, "_LIST_ALL_CACHE", None)
    monkeypatch.setattr(skills_module, "_LIST_ALL_CACHE_KEY", None)
    monkeypatch.setattr(
        resolver,
        "resolve",
        lambda _name: pytest.fail("bundled resolver lookup leaked into effective flow"),
    )

    invocation = resolver.resolve_invocation(
        "target",
        project,
        SkillExecutionRole.SESSION,
    )
    context = SkillProjectionContext(cwd=tmp_path, invocation=invocation)
    document = project_agent_skill_document(invocation.root, context)
    contract = build_effective_skill_dispatch_contract("/target", context)

    assert invocation.root.path == override_path
    assert invocation.root.execution_role is SkillExecutionRole.SESSION
    assert invocation.capability_union == frozenset({"github_api_write", "git_metadata_write"})
    assert "winning override body" in invocation.root.canonical_content
    assert "bundled sentinel body" not in invocation.root.canonical_content
    assert "winning override body" in document.content
    assert contract.source_identities["target"].origin.value == "project_local"
    assert contract.canonical_digests["target"] == hashlib.sha256(source_before).hexdigest()
    assert (
        contract.projected_digests["target"]
        == hashlib.sha256(document.content.encode()).hexdigest()
    )
    assert (
        render_target_skill_command("/autoskillit:target", invocation.root.source_ref) == "/target"
    )
    assert override_path.read_bytes() == source_before


# ---------------------------------------------------------------------------
