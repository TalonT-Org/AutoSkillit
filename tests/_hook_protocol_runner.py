"""Run standalone hook scripts for protocol tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path


def run_hook(
    script_rel: str | Path,
    payload: dict[str, object] | str,
    *,
    env: Mapping[str, str] | None = None,
    unset: Iterable[str] = (),
    cwd: Path | None = None,
    timeout: float | None = 10,
):
    """Run one hook in a fresh interpreter and adapt its process result."""
    from autoskillit.hooks._runtime._hook_output import HookEmission
    from tests.conftest import production_interpreter_env

    script = Path(script_rel)
    if not script.is_absolute():
        script = Path(__file__).parents[1] / "src" / "autoskillit" / "hooks" / script
    input_text = payload if isinstance(payload, str) else json.dumps(payload)
    run_env = production_interpreter_env()
    run_env.pop("AUTOSKILLIT_STATE_ROOT", None)
    run_env.pop("AUTOSKILLIT_STATE_DIR", None)
    run_env.pop("AUTOSKILLIT_LOG_DIR", None)
    if env is None or "AUTOSKILLIT_HEADLESS" not in env:
        run_env.pop("AUTOSKILLIT_HEADLESS", None)
    for name in unset:
        run_env.pop(name, None)
    run_env.update(env or {})
    run_env.pop("AUTOSKILLIT_STATE_ROOT", None)

    with tempfile.TemporaryDirectory(prefix="hook-protocol-") as temp_dir:
        log_dir = Path(temp_dir) / "logs"
        log_dir.mkdir()
        if env is None or "AUTOSKILLIT_LOG_DIR" not in env:
            run_env["AUTOSKILLIT_LOG_DIR"] = str(log_dir)
        result = subprocess.run(
            [sys.executable, "-B", str(script)],
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            env=run_env,
            timeout=timeout,
        )
    return HookEmission(result.stdout, result.stderr, result.returncode)
