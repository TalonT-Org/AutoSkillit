"""Tests for run_python work_dir path resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.server.tools._execution_helpers import (
    _PATH_LIKE_ARGS,
    resolve_relative_path_args,
)
from autoskillit.server.tools.tools_execution import run_python

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_run_python_resolves_relative_output_dir_against_work_dir(
    tool_ctx_kitchen_open, tmp_path
):
    """run_python must anchor relative output_dir to work_dir, not server process CWD."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    output_rel = ".autoskillit/temp/diagnose-test"

    result_json = await run_python(
        callable="autoskillit.smoke_utils.diagnose_merge_gate",
        args={"test_stdout": "FAILED test_foo", "test_stderr": "", "output_dir": output_rel},
        work_dir=str(work_dir),
    )
    result = json.loads(result_json)
    assert result["success"] is True, f"Expected success, got: {result}"
    expected = work_dir / output_rel / "diagnosis.md"
    assert expected.exists(), f"File should be at {expected}, not under server CWD"
    diagnosis_path = result["result"]["diagnosis_path"]
    assert Path(diagnosis_path).exists(), "run_skill must be able to read this file"
    assert Path(diagnosis_path).is_absolute(), "Returned path must be absolute"


def test_run_python_callable_cwd_arg_not_hijacked(tmp_path):
    """Tool-level work_dir must not consume or overwrite a callable's own cwd arg in args."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    callable_cwd = str(tmp_path / "callable_cwd")

    args: dict[str, object] = {"cwd": callable_cwd, "output_dir": ".autoskillit/temp/test"}
    resolved = resolve_relative_path_args(args, str(work_dir))

    assert "cwd" not in _PATH_LIKE_ARGS, (
        "cwd must not be in _PATH_LIKE_ARGS — it is a callable-level arg, not path-anchored"
    )
    assert resolved["cwd"] == callable_cwd, (
        f"Callable's cwd arg was mutated: {resolved['cwd']!r} != {callable_cwd!r}"
    )
    assert resolved["output_dir"] == str(work_dir / ".autoskillit/temp/test"), (
        "Relative output_dir must be anchored to work_dir"
    )


@pytest.mark.anyio
async def test_run_python_absolute_output_dir_unchanged_by_work_dir(
    tool_ctx_kitchen_open, tmp_path
):
    """Absolute output_dir values must not be modified by work_dir resolution."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    abs_output = str(tmp_path / "absolute-output")

    result_json = await run_python(
        callable="autoskillit.smoke_utils.diagnose_merge_gate",
        args={"test_stdout": "FAILED test_baz", "test_stderr": "", "output_dir": abs_output},
        work_dir=str(work_dir),
    )
    result = json.loads(result_json)
    assert result["success"] is True
    expected_file = Path(abs_output) / "diagnosis.md"
    assert expected_file.exists(), "Absolute output_dir must be used unchanged"
