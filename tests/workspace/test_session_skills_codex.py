"""Tests for Codex-specific session skill layout delegation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import ClaudeDirectoryConventions, SkillSource, ValidatedAddDir
from tests.workspace._helpers import _CODEX_CAPABILITIES

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _make_codex_backend() -> MagicMock:
    b = MagicMock()
    b.capabilities = _CODEX_CAPABILITIES
    b.conventions.skills_subdir = ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR
    b.ensure_pre_launch.return_value = []
    return b


def _make_config_with_tier2(tier2_names: list[str]) -> MagicMock:
    config = MagicMock()
    config.skills.tier1 = []
    config.skills.tier2 = tier2_names
    config.skills.tier3 = []
    config.subsets.disabled = []
    config.subsets.custom_tags = {}
    config.packs.enabled = []
    config.features = {}
    config.experimental_enabled = False
    return config


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
    session_path = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    skill_files = list(
        (session_path / ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR).glob("*/SKILL.md")
    )
    assert len(skill_files) > 0
    assert not (session_path / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR).exists()


def test_codex_init_session_delegates_to_setup_session_dir(
    make_session_skill_manager, codex_env
) -> None:
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    codex_env.backend.setup_session_dir.assert_called_once_with(Path(str(session_path)))


def test_no_backend_skips_setup_session_dir(make_session_skill_manager) -> None:
    mgr = make_session_skill_manager()
    result = mgr.init_session("sid", cook_session=True)
    assert isinstance(result, ValidatedAddDir)


def test_codex_init_session_returns_validated_add_dir(
    make_session_skill_manager, codex_env
) -> None:
    mgr = make_session_skill_manager()
    result = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    assert isinstance(result, ValidatedAddDir)
    assert str(result).endswith("/sid")


def test_claude_backend_still_uses_dot_claude_layout(make_session_skill_manager) -> None:
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True)
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
    mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    assert pre_launch_called, "ensure_pre_launch() must be called during init_session"


def test_codex_init_session_raises_when_pre_launch_fails(
    make_session_skill_manager, codex_env
) -> None:
    """init_session() must raise RuntimeError when ensure_pre_launch() returns errors."""
    codex_env.backend.ensure_pre_launch.return_value = ["Failed to ensure MCP registration: err"]

    mgr = make_session_skill_manager()
    with pytest.raises(RuntimeError, match="Pre-launch check failed"):
        mgr.init_session("sid", cook_session=True, backend=codex_env.backend)


def test_profile_skills_symlinked_into_session_dir(tmp_path, monkeypatch) -> None:
    """_materialize_profile_skills symlinks ~/.codex/skills/<name> into session_dir/skills/<name>."""  # noqa: E501
    from autoskillit.execution.backends.codex import _materialize_profile_skills

    fake_home = tmp_path / "fake_home"
    profile_skill = fake_home / ".codex" / "skills" / "my-skill"
    profile_skill.mkdir(parents=True)
    (profile_skill / "SKILL.md").write_text("# MY SKILL")

    session_dir = tmp_path / "session"
    (session_dir / "skills").mkdir(parents=True)

    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    count = _materialize_profile_skills(session_dir)

    target = session_dir / "skills" / "my-skill"
    assert target.is_symlink() or target.is_dir()
    assert (target / "SKILL.md").read_text() == "# MY SKILL"
    assert count == 1


def test_missing_profile_skills_dir_does_not_raise(tmp_path, monkeypatch) -> None:
    """_materialize_profile_skills returns 0 when ~/.codex/skills is absent."""
    from autoskillit.execution.backends.codex import _materialize_profile_skills

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()

    session_dir = tmp_path / "session"
    (session_dir / "skills").mkdir(parents=True)

    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    count = _materialize_profile_skills(session_dir)

    assert count == 0


def test_codex_session_omits_tier2_skills(make_session_skill_manager, codex_env) -> None:
    """Non-cook Codex sessions must not receive tier-2 skill files."""
    mgr = make_session_skill_manager()
    provider = mgr._provider
    tier2_names = [
        s.name for s in provider.list_skills() if s.source == SkillSource.BUNDLED_EXTENDED
    ]
    if not tier2_names:
        pytest.skip("No tier-2 skills available")

    sample = tier2_names[:3]
    config = _make_config_with_tier2(sample)
    session_path = mgr.init_session(
        "sid", cook_session=False, config=config, backend=codex_env.backend
    )
    skills_base = session_path / ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR
    for name in sample:
        assert not (skills_base / name).exists(), (
            f"Tier-2 skill {name!r} should not be written for Codex"
        )


def test_claude_code_session_keeps_tier2_skills(make_session_skill_manager) -> None:
    """Cook sessions without a backend write all skills including tier-2."""
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True)
    skills_base = session_path / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
    skill_files = list(skills_base.glob("*/SKILL.md"))
    assert len(skill_files) > 0, "Cook sessions must write skill files"


def test_codex_session_cook_mode_still_writes_all_skills(
    make_session_skill_manager, codex_env
) -> None:
    """Cook sessions bypass the capability gate — all skills are written."""
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    skills_base = session_path / ClaudeDirectoryConventions.PLUGIN_DIR_SKILLS_SUBDIR
    skill_files = list(skills_base.glob("*/SKILL.md"))
    assert len(skill_files) > 0, "Cook sessions must write all skills regardless of backend"
