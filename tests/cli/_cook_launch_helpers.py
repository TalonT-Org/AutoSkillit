"""Hermetic harness for real-backend cook launch tests."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from autoskillit.config import AutomationConfig
from autoskillit.core import CmdSpec, ManagedSessionHome, PluginLoadMode, ValidatedAddDir


class _Binding:
    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir
        self.identity = SimpleNamespace(managed_path=plugin_dir)
        self.inherited_fds: tuple[int, ...] = ()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Authority:
    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir

    def acquire_launch_binding(self, **_kwargs: object) -> _Binding:
        return _Binding(self.plugin_dir)


def arrange_cook(
    monkeypatch,
    tmp_path: Path,
    *,
    config: AutomationConfig | None = None,
) -> list[CmdSpec]:
    """Patch cook's materialization edges while retaining real backend builders."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    generated_home = tmp_path / "managed-home"
    skills_dir = generated_home / "skills"
    skills_dir.mkdir(parents=True)
    plugin_dir = tmp_path / "projected-plugin"
    plugin_dir.mkdir()
    manager = MagicMock()
    captured: list[CmdSpec] = []

    @contextmanager
    def managed_session(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        yield ManagedSessionHome(
            launch_id="launch",
            generated_home=generated_home,
            skills_dir=ValidatedAddDir(str(skills_dir)),
            pass_fds=(),
        )

    manager.managed_session.side_effect = managed_session
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AUTOSKILLIT_CODEX_STARTUP_TRACE", raising=False)
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda: config or AutomationConfig(),
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_cook.resolve_project_dir",
        lambda: project_dir,
    )
    monkeypatch.setattr(
        "autoskillit.cli.session._session_onboarding.is_first_run", lambda _path: False
    )
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        "autoskillit.workspace.DefaultSessionSkillManager", lambda *args, **kwargs: manager
    )
    monkeypatch.setattr(
        "autoskillit.cli.install._plugin_artifact.interactive_plugin_authority",
        lambda **_kwargs: (_Authority(plugin_dir), PluginLoadMode.EXPLICIT_PLUGIN_DIR),
    )
    monkeypatch.setattr("autoskillit.core.write_registry_entry", lambda *args, **kwargs: None)

    def capture(spec: CmdSpec, **_kwargs: object) -> SimpleNamespace:
        captured.append(spec)
        return SimpleNamespace(pid=1, pgid=1, returncode=0)

    monkeypatch.setattr(
        "autoskillit.cli.session._session_process.run_cook_attempt",
        capture,
    )
    return captured
