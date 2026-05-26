"""Tests for Codex-specific session skill layout and config file copying."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import ValidatedAddDir
from autoskillit.workspace.session_skills import (
    _SKILLS_SUBDIR,
    CODEX_SKILLS_SUBDIR,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


@pytest.fixture
def codex_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fake ~/.codex/ home and codex backend mock."""
    fake_home = tmp_path / "fakehome"
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text("[codex]\nmodel = 'o3'\n")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    backend = MagicMock()
    backend.name = "codex"

    return type(
        "CodexEnv",
        (),
        {
            "fake_home": fake_home,
            "codex_dir": codex_dir,
            "backend": backend,
        },
    )()


def test_codex_init_session_creates_skills_subdir(make_session_skill_manager, codex_env) -> None:
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    skill_files = list((session_path / CODEX_SKILLS_SUBDIR).glob("*/SKILL.md"))
    assert len(skill_files) > 0
    assert not (session_path / _SKILLS_SUBDIR).exists()


def test_codex_init_session_copies_config_toml(make_session_skill_manager, codex_env) -> None:
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    copied = session_path / "config.toml"
    assert copied.exists()
    assert copied.read_text() == "[codex]\nmodel = 'o3'\n"


def test_codex_init_session_symlinks_auth_json(make_session_skill_manager, codex_env) -> None:
    (codex_env.codex_dir / "auth.json").write_text('{"token": "test"}')
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    auth = session_path / "auth.json"
    assert auth.is_symlink()
    assert auth.resolve() == (codex_env.codex_dir / "auth.json").resolve()
    assert auth.read_text() == '{"token": "test"}'


def test_codex_init_session_auth_json_symlink_target_absent(
    make_session_skill_manager, codex_env
) -> None:
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    assert not (session_path / "auth.json").exists()
    assert not (session_path / "auth.json").is_symlink()


def test_codex_init_session_copies_env_if_present(make_session_skill_manager, codex_env) -> None:
    (codex_env.codex_dir / ".env").write_text("API_KEY=secret\n")
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    copied = session_path / ".env"
    assert copied.exists()
    assert copied.read_text() == "API_KEY=secret\n"


def test_codex_init_session_skips_env_if_absent(make_session_skill_manager, codex_env) -> None:
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    assert not (session_path / ".env").exists()


def test_codex_init_session_auth_json_missing_no_crash(
    make_session_skill_manager, codex_env
) -> None:
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    assert not (session_path / "auth.json").exists()


def test_codex_init_session_creates_sessions_symlink(
    make_session_skill_manager, codex_env
) -> None:
    mgr = make_session_skill_manager()
    session_path = mgr.init_session("sid", cook_session=True, backend=codex_env.backend)
    sessions_link = session_path / "sessions"
    assert sessions_link.is_symlink()
    target = sessions_link.resolve()
    assert target.is_dir()
    assert str(target).endswith("codex-sessions")


def test_codex_init_session_config_toml_missing_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_skill_manager
) -> None:
    fake_home = tmp_path / "fakehome"
    (fake_home / ".codex").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    backend = MagicMock()
    backend.name = "codex"

    mgr = make_session_skill_manager()
    with pytest.raises(FileNotFoundError):
        mgr.init_session("sid", cook_session=True, backend=backend)


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
    skill_files = list((session_path / _SKILLS_SUBDIR).glob("*/SKILL.md"))
    assert len(skill_files) > 0
    assert not (session_path / CODEX_SKILLS_SUBDIR).exists()
