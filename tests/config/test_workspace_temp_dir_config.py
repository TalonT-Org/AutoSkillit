"""Tests for workspace.temp_dir layered config resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.config import WorkspaceConfig, load_config

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


def test_workspace_config_has_temp_dir_field() -> None:
    assert WorkspaceConfig().temp_dir is None


def test_temp_dir_layered_priority_project_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_home = tmp_path / "home"
    user_cfg = user_home / ".autoskillit"
    user_cfg.mkdir(parents=True)
    (user_cfg / "config.yaml").write_text("workspace:\n  temp_dir: /user/x\n")
    monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

    project_dir = tmp_path / "project"
    project_cfg = project_dir / ".autoskillit"
    project_cfg.mkdir(parents=True)
    (project_cfg / "config.yaml").write_text("workspace:\n  temp_dir: /proj/y\n")

    cfg = load_config(project_dir)
    assert cfg.workspace.temp_dir == "/proj/y"


def test_temp_dir_layered_priority_user_wins_over_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_home = tmp_path / "home"
    user_cfg = user_home / ".autoskillit"
    user_cfg.mkdir(parents=True)
    (user_cfg / "config.yaml").write_text("workspace:\n  temp_dir: /user/x\n")
    monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    cfg = load_config(project_dir)
    assert cfg.workspace.temp_dir == "/user/x"


def test_temp_dir_env_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_dir = tmp_path / "project"
    project_cfg = project_dir / ".autoskillit"
    project_cfg.mkdir(parents=True)
    (project_cfg / "config.yaml").write_text("workspace:\n  temp_dir: /proj/y\n")

    monkeypatch.setenv("AUTOSKILLIT_WORKSPACE__TEMP_DIR", "/env/z")
    cfg = load_config(project_dir)
    assert cfg.workspace.temp_dir == "/env/z"


def test_default_yaml_contains_temp_dir_key() -> None:
    import yaml

    from autoskillit.core import pkg_root

    defaults_path = pkg_root() / "config" / "defaults.yaml"
    data = yaml.safe_load(defaults_path.read_text())
    assert data["workspace"]["temp_dir"] == ".autoskillit/temp"


def test_temp_dir_empty_string_normalized_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_WORKSPACE__TEMP_DIR", "")
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    cfg = load_config(project_dir)
    assert cfg.workspace.temp_dir is None
