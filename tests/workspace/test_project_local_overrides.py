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


# ---------------------------------------------------------------------------
