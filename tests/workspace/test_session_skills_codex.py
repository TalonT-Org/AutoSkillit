"""Tests for Codex-specific session skill layout delegation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import ClaudeDirectoryConventions, SkillExecutionRole, ValidatedAddDir
from tests.workspace._helpers import _CODEX_CAPABILITIES

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _make_codex_backend() -> MagicMock:
    b = MagicMock()
    b.capabilities = _CODEX_CAPABILITIES
    b.conventions.skills_subdir = ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR
    b.ensure_pre_launch.return_value = []
    return b


def _materialize(
    manager,
    session_id: str,
    *,
    backend=None,
    names: frozenset[str] | None = None,
) -> ValidatedAddDir:
    from autoskillit.workspace import DefaultSkillResolver, EffectiveSkillCatalog

    project_root = manager._root
    catalog = DefaultSkillResolver().list_effective(
        project_root,
        SkillExecutionRole.SESSION,
    )
    if names is not None:
        catalog = EffectiveSkillCatalog(
            skills=tuple(member for member in catalog.skills if member.name in names),
            execution_role=SkillExecutionRole.SESSION,
        )
    context = manager._provider.catalog_projection_context(
        catalog,
        project_root,
        backend=backend,
    )
    return manager.init_session(session_id, catalog, context)


@pytest.fixture
def codex_env():
    """Codex backend mock for delegation-contract tests."""
    backend = _make_codex_backend()

    return type(
        "CodexEnv",
        (),
        {
            "backend": backend,
        },
    )()


def test_codex_init_session_creates_skills_subdir(make_session_skill_manager, codex_env) -> None:
    mgr = make_session_skill_manager()
    session_path = _materialize(mgr, "sid", backend=codex_env.backend)
    skill_files = list(
        (session_path / ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR).glob("*/SKILL.md")
    )
    assert len(skill_files) > 0
    assert not (session_path / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR).exists()


def test_codex_init_session_delegates_to_setup_session_dir(
    make_session_skill_manager, codex_env
) -> None:
    mgr = make_session_skill_manager()
    session_path = _materialize(mgr, "sid", backend=codex_env.backend)
    codex_env.backend.setup_session_dir.assert_called_once_with(Path(str(session_path)))


def test_no_backend_skips_setup_session_dir(make_session_skill_manager) -> None:
    mgr = make_session_skill_manager()
    result = _materialize(mgr, "sid")
    assert isinstance(result, ValidatedAddDir)


def test_codex_init_session_returns_validated_add_dir(
    make_session_skill_manager, codex_env
) -> None:
    mgr = make_session_skill_manager()
    result = _materialize(mgr, "sid", backend=codex_env.backend)
    assert isinstance(result, ValidatedAddDir)
    assert str(result).endswith("/sid")


def test_claude_backend_still_uses_dot_claude_layout(make_session_skill_manager) -> None:
    mgr = make_session_skill_manager()
    session_path = _materialize(mgr, "sid")
    skill_files = list(
        (session_path / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR).glob("*/SKILL.md")
    )
    assert len(skill_files) > 0
    assert not (session_path / ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR).exists()


def test_codex_init_session_calls_ensure_pre_launch(make_session_skill_manager, codex_env) -> None:
    """init_session() must call backend.ensure_pre_launch() when mcp_config_capable is True."""
    pre_launch_called: list[bool] = []
    codex_env.backend.ensure_pre_launch.return_value = []
    codex_env.backend.ensure_pre_launch.side_effect = lambda: pre_launch_called.append(True) or []

    mgr = make_session_skill_manager()
    _materialize(mgr, "sid", backend=codex_env.backend)
    assert pre_launch_called, "ensure_pre_launch() must be called during init_session"


def test_codex_init_session_raises_when_pre_launch_fails(
    make_session_skill_manager, codex_env
) -> None:
    """init_session() must raise RuntimeError when ensure_pre_launch() returns errors."""
    codex_env.backend.ensure_pre_launch.return_value = ["Failed to ensure MCP registration: err"]

    mgr = make_session_skill_manager()
    with pytest.raises(RuntimeError, match="Pre-launch check failed"):
        _materialize(mgr, "sid", backend=codex_env.backend)


def test_profile_skills_are_projected_into_session_dir(tmp_path, monkeypatch) -> None:
    """Codex profile skills are copied as projections, never linked to raw sources."""
    from autoskillit.core.io import load_yaml
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.workspace import materialize_codex_profile_skills

    fake_home = tmp_path / "fake_home"
    profile_skill = fake_home / ".codex" / "skills" / "my-skill"
    profile_skill.mkdir(parents=True)
    (profile_skill / "SKILL.md").write_text(
        "---\n"
        "name: my-skill\n"
        "description: Public profile description.\n"
        "uses_capabilities: [agent_model]\n"
        "execution_role: session\n"
        "backend_requirements: [codex]\n"
        "---\n"
        "# MY SKILL\n"
    )

    session_dir = tmp_path / "session"
    (session_dir / "skills").mkdir(parents=True)

    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    count = materialize_codex_profile_skills(session_dir, CodexBackend())

    target = session_dir / "skills" / "my-skill"
    assert target.is_dir()
    assert not target.is_symlink()
    content = (target / "SKILL.md").read_text()
    frontmatter = load_yaml(content.split("---\n", 2)[1])
    assert {
        "uses_capabilities",
        "execution_role",
        "backend_requirements",
    }.isdisjoint(frontmatter)
    assert frontmatter["description"] == "Public profile description."
    assert content.endswith("# MY SKILL\n")
    assert count == 1


def test_missing_profile_skills_dir_does_not_raise(tmp_path, monkeypatch) -> None:
    """Profile projection returns 0 when ~/.codex/skills is absent."""
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.workspace import materialize_codex_profile_skills

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()

    session_dir = tmp_path / "session"
    (session_dir / "skills").mkdir(parents=True)

    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    count = materialize_codex_profile_skills(session_dir, CodexBackend())

    assert count == 0


@pytest.mark.parametrize("backend_kind", ["claude-code", "codex"])
def test_session_projection_is_agent_safe_for_each_backend(
    make_session_skill_manager,
    codex_env,
    backend_kind: str,
) -> None:
    from autoskillit.core.io import load_yaml

    backend = codex_env.backend if backend_kind == "codex" else None
    manager = make_session_skill_manager()
    session_path = _materialize(
        manager,
        f"projection-{backend_kind}",
        backend=backend,
        names=frozenset({"make-arch-diag"}),
    )
    skills_subdir = (
        ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR
        if backend is not None
        else ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
    )
    content = (session_path / skills_subdir / "make-arch-diag" / "SKILL.md").read_text()
    frontmatter = load_yaml(content.split("---\n", 2)[1])
    assert {
        "uses_capabilities",
        "execution_role",
        "backend_requirements",
    }.isdisjoint(frontmatter)
    assert frontmatter["name"] == "make-arch-diag"
