"""Pure detection of project-local skill overrides: scan search dirs and return override names."""

from __future__ import annotations

import pytest

from autoskillit.workspace.skills import override_names

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


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
