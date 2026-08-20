"""Hermetic harness for real-backend cook launch tests."""

from __future__ import annotations

import json
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
    settings_content: dict | None = None,
    project_dir_override: Path | None = None,
) -> list[CmdSpec]:
    """Patch cook's materialization edges while retaining real backend builders.

    settings_content, when given, is written as the project's
    .claude/settings.local.json before cook runs — the composition #4684's
    regression tests exercise (a real populated settings file, not an empty
    tmp_path project directory). None (the default) writes nothing, matching
    every pre-existing call site's behavior unchanged.

    project_dir_override, when given, is used as the resolved project
    directory instead of creating tmp_path/"project" — for the opt-in live
    gate that exercises the real repository root's own .claude/settings.local.json
    (test_cook_real_root_smoke.py). The generated managed-home/plugin
    directories remain tmp_path-isolated regardless; only project_dir (and
    therefore what settings.local.json read) changes. Callers passing an
    override own creating/populating that directory themselves — this
    function will not mkdir() or write settings_content into it, since doing
    so on a real, pre-existing repository root would be destructive.
    """
    if project_dir_override is not None and settings_content is not None:
        raise ValueError(
            "arrange_cook() cannot combine settings_content with "
            "project_dir_override — callers passing project_dir_override own "
            "creating/populating that directory themselves."
        )
    if project_dir_override is not None:
        project_dir = project_dir_override
    else:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        if settings_content is not None:
            claude_dir = project_dir / ".claude"
            claude_dir.mkdir(parents=True, exist_ok=True)
            (claude_dir / "settings.local.json").write_text(json.dumps(settings_content))
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
