"""Real CLI-boundary exception rendering contracts for Codex cleanup failures."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

_SECRET = "cli-provider-secret-4361"
_CONFIG_SENTINEL = "cli-large-config-sentinel-4361-" * 64


def _run_app_boundary(
    tmp_path: Path,
    mode: str,
    boundary_source: str,
) -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        """
        import importlib
        import logging
        import os
        import sys
        from pathlib import Path

        from autoskillit.core import configure_logging

        class TtyProxy:
            def __init__(self, wrapped):
                self.wrapped = wrapped

            def write(self, value):
                return self.wrapped.write(value)

            def flush(self):
                return self.wrapped.flush()

            def isatty(self):
                return True

        mode = os.environ["AUTOSKILLIT_TEST_RENDER_MODE"]
        if mode == "tty":
            sys.stderr = TtyProxy(sys.stderr)

        root = Path(os.environ["AUTOSKILLIT_TEST_ROOT"])
        """
    )
    script += textwrap.dedent(boundary_source)
    script += textwrap.dedent(
        """
        app_module = importlib.import_module("autoskillit.cli.app")
        init_helpers = importlib.import_module("autoskillit.cli._init_helpers")
        update_checks = importlib.import_module("autoskillit.cli.update._update_checks")
        init_helpers.evict_direct_mcp_entry = lambda _path: None
        update_checks.run_update_checks = lambda **_kwargs: None
        app_module.app = invoke_boundary
        sys.argv = ["autoskillit", "--version"]
        app_module.main()
        """
    )
    env = os.environ.copy()
    env.update(
        AUTOSKILLIT_TEST_RENDER_MODE=mode,
        AUTOSKILLIT_TEST_ROOT=str(tmp_path),
        AUTOSKILLIT_TEST_SECRET=_SECRET,
        AUTOSKILLIT_TEST_CONFIG=_CONFIG_SENTINEL,
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _assert_single_traceback(result: subprocess.CompletedProcess[str], message: str) -> None:
    assert result.returncode != 0
    assert result.stdout == ""
    assert _SECRET not in result.stderr
    assert _CONFIG_SENTINEL not in result.stderr
    assert result.stderr.count("Traceback (most recent call last)") == 1
    assert result.stderr.count(message) == 1


@pytest.mark.parametrize("mode", ["tty", "json"])
def test_app_main_is_single_traceback_owner_for_codex_cleanup_failure(
    tmp_path: Path,
    mode: str,
) -> None:
    result = _run_app_boundary(
        tmp_path,
        mode,
        """
        from autoskillit.core import NoResume
        from autoskillit.execution import CodexSessionStore

        def invoke_boundary():
            configure_logging(
                level=logging.DEBUG,
                json_output=mode == "json",
                stream=sys.stderr,
            )
            home = root / "generated-home"
            home.mkdir()
            for name in ("sessions", "archived_sessions"):
                target = home / f".inert-{name}"
                target.mkdir()
                (home / name).symlink_to(target)
            store = CodexSessionStore(log_dir=root / "logs")
            lease = store.prepare_attempt(
                session_home=home,
                project_dir=root,
                launch_id="0123456789abcdef",
                attempt=1,
                current_resume_spec=NoResume(),
            )
            lease.__enter__()

            def fail_cleanup(_lease):
                provider_secret = os.environ["AUTOSKILLIT_TEST_SECRET"]
                large_config = os.environ["AUTOSKILLIT_TEST_CONFIG"]
                raise RuntimeError("controlled cleanup failure")

            store._exit_attempt = fail_cleanup
            lease.__exit__(None, None, None)
        """,
    )
    _assert_single_traceback(result, "controlled cleanup failure")
    if mode == "json":
        event = next(
            json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")
        )
        assert event["event"] == "codex_attempt_exit_failed"
        assert event["view_id"] == "0123456789abcdef-1"
        assert "exception" not in event


@pytest.mark.parametrize("mode", ["tty", "json"])
def test_app_main_is_single_traceback_owner_for_process_runner_failure(
    tmp_path: Path,
    mode: str,
) -> None:
    result = _run_app_boundary(
        tmp_path,
        mode,
        """
        from autoskillit.cli.session._session_process import run_cook_attempt
        from autoskillit.core import CmdSpec

        class Trace:
            def record_spawn(self):
                raise AssertionError("missing executable must fail before spawn")

        def invoke_boundary():
            configure_logging(
                level=logging.DEBUG,
                json_output=mode == "json",
                stream=sys.stderr,
            )
            run_cook_attempt(
                CmdSpec(
                    cmd=("autoskillit-definitely-missing-executable-4361",),
                    env={},
                    cwd=str(root),
                ),
                pass_fds=(),
                on_spawn=lambda _pid, _pgid: None,
                on_reaped=lambda _pid, _pgid: None,
                trace=Trace(),
                observer=None,
            )
        """,
    )
    _assert_single_traceback(result, "autoskillit-definitely-missing-executable-4361")
    if mode == "json":
        event = next(
            json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")
        )
        assert event["event"] == "cook_attempt_failed"
        assert event["error_type"] == "FileNotFoundError"
        assert "exception" not in event
