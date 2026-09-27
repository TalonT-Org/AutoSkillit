"""Launch commands apply their terminal log level before any prompt or launch sink.

Each driver reuses the command's existing harness, records every
``configure_logging`` call, records the declared prompt helpers, and stubs the
declared launch sink with an escape sentinel so the command stops at launch.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import pytest

import autoskillit.cli.fleet as _patch_cli_fleet
import autoskillit.cli.fleet._fleet_run as _patch_fleet__fleet_run
import autoskillit.cli.session._session_backend as _patch_session__session_backend
import autoskillit.cli.session._session_cook as _patch_session__session_cook
import autoskillit.cli.session._session_order as _patch_session__session_order
import autoskillit.cli.ui._timed_input as _patch_ui__timed_input
from autoskillit import cli
from autoskillit.config import AutomationConfig, LoggingConfig
from autoskillit.core import atomic_write
from tests.cli._cook_launch_helpers import arrange_cook
from tests.cli._fleet_helpers import _stub_campaign_resolution, _stub_guards

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


class _SinkReached(BaseException):
    """Escape sentinel: passes through the launch paths' ``except Exception`` handlers."""


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def install_configure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "autoskillit.core.configure_logging",
            lambda **kwargs: self.events.append(("configure", kwargs)),
        )

    def prompt(self, name: str, answer: Callable[..., Any]) -> Callable[..., Any]:
        def _prompt(*args: Any, **kwargs: Any) -> Any:
            self.events.append(("prompt", name))
            return answer(*args, **kwargs)

        return _prompt

    def sink(self, name: str) -> Callable[..., Any]:
        def _sink(*_args: Any, **_kwargs: Any) -> Any:
            self.events.append(("sink", name))
            raise _SinkReached

        return _sink


_Driver = Callable[[pytest.MonkeyPatch, Path, LoggingConfig, _Recorder], Callable[[], None]]


def _drive_cook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, logging_cfg: LoggingConfig, rec: _Recorder
) -> Callable[[], None]:
    from autoskillit.execution.backends import ClaudeCodeBackend

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    atomic_write(bin_dir / "claude", "#!/bin/sh\nexit 0\n")
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    arrange_cook(monkeypatch, tmp_path, config=AutomationConfig(logging=logging_cfg))
    rec.install_configure(monkeypatch)
    monkeypatch.setattr(
        _patch_session__session_cook,
        "plugin_launch_binding_scope",
        rec.sink("plugin_launch_binding_scope"),
    )
    return lambda: cli.cook(backend=ClaudeCodeBackend())


def _drive_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, logging_cfg: LoggingConfig, rec: _Recorder
) -> Callable[[], None]:
    from autoskillit.execution.backends import get_backend

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    config = AutomationConfig(logging=logging_cfg)
    monkeypatch.setattr("autoskillit.config.load_config", lambda *_a, **_kw: config)
    monkeypatch.setattr(
        _patch_session__session_backend,
        "resolve_global_backend",
        lambda name, **_kwargs: get_backend(name),
    )
    rec.install_configure(monkeypatch)
    monkeypatch.setattr(
        _patch_session__session_order,
        "_prepare_order_selection",
        rec.prompt(
            "_prepare_order_selection", _patch_session__session_order._prepare_order_selection
        ),
    )
    monkeypatch.setattr(
        _patch_session__session_order,
        "_prepare_order_launch",
        rec.prompt("_prepare_order_launch", lambda _recipe, *, launch, **_kw: (launch, {})),
    )
    monkeypatch.setattr(
        _patch_session__session_order,
        "_launch_cook_session",
        rec.sink("_launch_cook_session"),
    )
    return lambda: cli.order("test-recipe")


def _arrange_fleet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, logging_cfg: LoggingConfig
) -> None:
    monkeypatch.chdir(tmp_path)
    _stub_guards(monkeypatch)
    monkeypatch.setattr(_patch_cli_fleet, "is_feature_enabled", lambda *_a, **_kw: True)
    config = AutomationConfig(features={"fleet": True}, logging=logging_cfg)
    monkeypatch.setattr("autoskillit.config.load_config", lambda *_a, **_kw: config)


def _drive_fleet_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, logging_cfg: LoggingConfig, rec: _Recorder
) -> Callable[[], None]:
    _arrange_fleet(monkeypatch, tmp_path, logging_cfg)
    monkeypatch.setattr(_patch_cli_fleet, "_print_dispatch_preview", lambda: "")
    rec.install_configure(monkeypatch)
    monkeypatch.setattr(
        _patch_ui__timed_input, "timed_prompt", rec.prompt("timed_prompt", lambda *_a, **_kw: "")
    )
    monkeypatch.setattr(
        _patch_cli_fleet, "_launch_fleet_session", rec.sink("_launch_fleet_session")
    )
    return _patch_cli_fleet.fleet_dispatch


def _drive_fleet_campaign(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, logging_cfg: LoggingConfig, rec: _Recorder
) -> Callable[[], None]:
    _arrange_fleet(monkeypatch, tmp_path, logging_cfg)
    _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
    rec.install_configure(monkeypatch)
    monkeypatch.setattr(
        _patch_cli_fleet,
        "_select_campaign",
        rec.prompt("_select_campaign", lambda *_a: ("test-campaign", None)),
    )
    monkeypatch.setattr(
        _patch_cli_fleet, "_launch_fleet_session", rec.sink("_launch_fleet_session")
    )
    return lambda: _patch_cli_fleet.fleet_campaign("test-campaign")


def _drive_fleet_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, logging_cfg: LoggingConfig, rec: _Recorder
) -> Callable[[], None]:
    monkeypatch.chdir(tmp_path)
    config = AutomationConfig(logging=logging_cfg)
    monkeypatch.setattr(
        _patch_fleet__fleet_run, "_fleet_run_preflight", lambda *_a, **_kw: (config, None)
    )
    rec.install_configure(monkeypatch)
    monkeypatch.setattr(
        _patch_fleet__fleet_run, "_execute_fleet_run", rec.sink("_execute_fleet_run")
    )
    return lambda: _patch_fleet__fleet_run.fleet_run("test-recipe", task="probe")


class _Command(NamedTuple):
    driver: _Driver
    default_level: int
    feature: str | None = None


_COMMANDS: dict[str, _Command] = {
    "cook": _Command(_drive_cook, logging.WARNING),
    "order": _Command(_drive_order, logging.WARNING),
    "fleet-dispatch": _Command(_drive_fleet_dispatch, logging.WARNING, "fleet"),
    "fleet-campaign": _Command(_drive_fleet_campaign, logging.WARNING, "fleet"),
    "fleet-run": _Command(_drive_fleet_run, logging.INFO, "fleet"),
}


@pytest.mark.parametrize("configured_level", [None, "DEBUG"], ids=["unset", "debug"])
@pytest.mark.parametrize(
    "command",
    [
        pytest.param(
            name,
            marks=[pytest.mark.feature(spec.feature)] if spec.feature else [],
            id=name,
        )
        for name, spec in sorted(_COMMANDS.items())
    ],
)
def test_launch_command_applies_config_level_before_launch(
    command: str,
    configured_level: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    driver, default_level, _feature = _COMMANDS[command]
    rec = _Recorder()
    invoke = driver(monkeypatch, tmp_path, LoggingConfig(level=configured_level), rec)

    with pytest.raises(_SinkReached):
        invoke()

    configures = [payload for kind, payload in rec.events if kind == "configure"]
    assert len(configures) == 1, rec.events
    expected = default_level if configured_level is None else logging.DEBUG
    assert configures[0].get("level") == expected
    assert configures[0].get("stream") is sys.stderr
    kinds = [kind for kind, _ in rec.events]
    assert kinds[0] == "configure", rec.events
    assert kinds[-1] == "sink", rec.events
