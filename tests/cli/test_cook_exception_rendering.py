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


@pytest.mark.parametrize("mode", ["tty", "json"])
def test_app_main_is_single_traceback_owner_for_codex_cleanup_failure(
    tmp_path: Path,
    mode: str,
) -> None:
    script = textwrap.dedent(
        """
        import importlib
        import logging
        import os
        import sys
        from pathlib import Path

        from autoskillit.core import NoResume, configure_logging
        from autoskillit.execution import CodexSessionStore

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

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert _SECRET not in result.stderr
    assert _CONFIG_SENTINEL not in result.stderr
    assert result.stderr.count("Traceback (most recent call last)") == 1
    assert result.stderr.count("controlled cleanup failure") == 1
    if mode == "json":
        event = next(
            json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")
        )
        assert event["event"] == "codex_attempt_exit_failed"
        assert event["view_id"] == "0123456789abcdef-1"
        assert "exception" not in event
