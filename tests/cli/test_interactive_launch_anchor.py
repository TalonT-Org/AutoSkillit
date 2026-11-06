"""Interactive launchers (cook on Claude and Codex, order, fleet) anchor hook state.

Each launcher hands its child ``AUTOSKILLIT_STATE_ROOT`` set to the resolved
project root. The cook launchers also carry a launch id the daemon reaper's
``_LAUNCH_ID_RE`` accepts, so both variables of the environ join that
``_candidate_for_pid`` requires are present.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

import pytest

import autoskillit.cli._preview as _patch_cli__preview
import autoskillit.cli.session._session_cook as _patch_session__session_cook
import autoskillit.cli.session._session_onboarding as _patch_session__session_onboarding
import autoskillit.cli.session._session_order as _patch_session__session_order
import autoskillit.cli.session._session_process as _patch_session__session_process
import autoskillit.cli.session._session_reload as _patch_session__session_reload
import autoskillit.cli.ui._timed_input as _patch_ui__timed_input
from autoskillit import cli
from autoskillit.cli.fleet import fleet_campaign
from autoskillit.core import (
    AUTOSKILLIT_STATE_ROOT_ENV_VAR,
    LAUNCH_ID_ENV_VAR,
    CmdSpec,
    InteractiveInvocationValidation,
    SessionAttemptHandle,
    atomic_write,
)
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.execution.process import _daemon_orphans
from tests.cli._cook_launch_helpers import cook_attempt_result
from tests.cli._fleet_helpers import _capture_subprocess, _stub_campaign_resolution, _stub_guards
from tests.cli._logging_helpers import stub_configure_logging
from tests.cli.conftest import _SCRIPT_YAML
from tests.cli.test_cook_env_scrub import _capture_cook_spec
from tests.execution.backends._codex_fixtures import installed_catalog

pytestmark = [
    pytest.mark.layer("cli"),
    pytest.mark.medium,
    pytest.mark.usefixtures("_stub_owner_binding"),
]

_LaunchedEnv = Callable[[pytest.MonkeyPatch, Path, Path], Mapping[str, str]]


def _pin_cook_project_dir(monkeypatch: pytest.MonkeyPatch, project_dir: Path) -> None:
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(_patch_session__session_cook, "resolve_project_dir", Path.cwd)


def _cook_claude_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, project_dir: Path
) -> Mapping[str, str]:
    _pin_cook_project_dir(monkeypatch, project_dir)
    return _capture_cook_spec(monkeypatch, tmp_path).env


def _cook_codex_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, project_dir: Path
) -> Mapping[str, str]:
    source_home = tmp_path / "home" / ".codex"
    source_home.mkdir(parents=True)
    (source_home / "config.toml").write_text(
        'cli_auth_credentials_store = "keyring"\n', encoding="utf-8"
    )
    (source_home / "auth.json").write_text("{}\n", encoding="utf-8")
    bundled_catalog = tmp_path / "bundled-models.json"
    atomic_write(bundled_catalog, json.dumps(installed_catalog()))
    codex_shim = tmp_path / "codex"
    atomic_write(
        codex_shim,
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "import sys\n"
        "if sys.argv[1:] != ['debug', 'models', '--bundled']:\n"
        "    raise SystemExit(64)\n"
        f"sys.stdout.buffer.write(pathlib.Path({str(bundled_catalog)!r}).read_bytes())\n",
    )
    codex_shim.chmod(0o755)
    captured: dict[str, Mapping[str, str]] = {}

    @contextmanager
    def session_attempt_context(
        _self: CodexBackend, **_kwargs: object
    ) -> Iterator[SessionAttemptHandle]:
        yield SessionAttemptHandle(
            view_id="codex-view",
            pass_fds=(),
            _record_spawn=lambda _pid, _pgid: None,
            _record_reaped=lambda _pid, _pgid: None,
            _record_teardown_unproven=lambda _pid, _pgid: None,
        )

    def run_attempt(spec: CmdSpec, **kwargs: object) -> object:
        captured["env"] = spec.env
        kwargs["on_spawn"](101, 101)  # type: ignore[operator]
        kwargs["trace"].record_spawn()  # type: ignore[union-attr]
        kwargs["on_reaped"](101, 101)  # type: ignore[operator]
        return cook_attempt_result()

    _pin_cook_project_dir(monkeypatch, project_dir)
    monkeypatch.setattr(shutil, "which", lambda _name, **_kwargs: str(codex_shim))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(_patch_session__session_onboarding, "is_first_run", lambda _project: False)
    monkeypatch.setattr(_patch_ui__timed_input, "timed_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("autoskillit.core.write_registry_entry", lambda *_a, **_k: None)
    monkeypatch.setattr(_patch_session__session_process, "run_cook_attempt", run_attempt)
    monkeypatch.setattr(
        _patch_session__session_reload, "consume_reload_sentinel", lambda _project: None
    )
    monkeypatch.setattr(CodexBackend, "session_attempt_context", session_attempt_context)
    monkeypatch.setattr(
        "autoskillit.execution.backends._codex_probes._validate_mcp_probe",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        CodexBackend,
        "validate_interactive_invocation",
        lambda _self, _spec: InteractiveInvocationValidation(errors=()),
    )

    cli.cook(backend=CodexBackend(source_codex_home=source_home))

    return captured["env"]


def _order_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, project_dir: Path
) -> Mapping[str, str]:
    recipes_dir = project_dir / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "test-script.yaml").write_text(_SCRIPT_YAML)
    monkeypatch.chdir(project_dir)
    stub_configure_logging(monkeypatch)
    monkeypatch.setattr(_patch_cli__preview, "show_cook_preview", lambda *a, **kw: None)
    monkeypatch.setattr(
        _patch_session__session_order, "_get_ingredients_table", lambda *a, **kw: "| col | val |"
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    monkeypatch.setattr("autoskillit.core.write_registry_entry", lambda *_a, **_k: None)
    captured = _capture_subprocess(monkeypatch)

    cli.order("test-script")

    return captured["env"]


def _fleet_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, project_dir: Path
) -> Mapping[str, str]:
    config_dir = project_dir / ".autoskillit"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("features:\n  fleet: true\n")
    monkeypatch.chdir(project_dir)
    _stub_guards(monkeypatch)
    _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
    captured = _capture_subprocess(monkeypatch)

    fleet_campaign("test-campaign")

    return captured["env"]


class _Launcher(NamedTuple):
    launched_env: _LaunchedEnv
    stubs_prelaunch: bool
    reaper_joinable: bool


@pytest.mark.parametrize(
    "launcher",
    [
        pytest.param(_Launcher(_cook_claude_env, True, True), id="cook-claude"),
        # The shared prelaunch stub's Codex readiness double rejects the plugin_dir
        # keyword a managed home passes, so this row probes the catalog shim instead.
        pytest.param(_Launcher(_cook_codex_env, False, True), id="cook-codex"),
        pytest.param(_Launcher(_order_env, True, False), id="order"),
        pytest.param(_Launcher(_fleet_env, True, False), id="fleet"),
    ],
)
def test_interactive_launch_anchors_state_root_to_project_dir(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    launcher: _Launcher,
) -> None:
    if launcher.stubs_prelaunch:
        request.getfixturevalue("_stub_interactive_prelaunch")
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    env = launcher.launched_env(monkeypatch, tmp_path, project_dir)

    assert env.get(AUTOSKILLIT_STATE_ROOT_ENV_VAR) == str(project_dir.resolve())
    if launcher.reaper_joinable:
        assert _daemon_orphans._LAUNCH_ID_RE.fullmatch(env.get(LAUNCH_ID_ENV_VAR, ""))
