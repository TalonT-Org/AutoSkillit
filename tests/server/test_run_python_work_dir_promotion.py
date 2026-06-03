"""Tests for run_python work_dir auto-promotion from args."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools._execution_helpers import (
    maybe_promote_work_dir,
    validate_path_arg_anchoring,
)
from autoskillit.server.tools.tools_execution import run_python

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# T1a: promotes when args has work_dir and top-level is empty
def test_maybe_promote_work_dir_promotes_from_args():
    args = {"output_dir": ".autoskillit/temp/test", "work_dir": "/abs/path"}
    result = maybe_promote_work_dir(args, "")
    assert result == "/abs/path"


# T1b: no-op when top-level work_dir already set
def test_maybe_promote_work_dir_noop_when_toplevel_set():
    args = {"work_dir": "/other"}
    result = maybe_promote_work_dir(args, "/existing")
    assert result == "/existing"


# T1c: no-op when args is None
def test_maybe_promote_work_dir_noop_when_args_none():
    result = maybe_promote_work_dir(None, "")
    assert result == ""


# T1d: no-op when args has no work_dir
def test_maybe_promote_work_dir_noop_when_no_work_dir_in_args():
    args = {"output_dir": "rel/path"}
    result = maybe_promote_work_dir(args, "")
    assert result == ""


# T1e: no-op when args work_dir is empty string
def test_maybe_promote_work_dir_noop_when_empty_string():
    args = {"work_dir": ""}
    result = maybe_promote_work_dir(args, "")
    assert result == ""


# T1f: no-op when args work_dir is not a string
def test_maybe_promote_work_dir_noop_when_non_string():
    args = {"work_dir": 123}
    result = maybe_promote_work_dir(args, "")
    assert result == ""


# T2a: run_python succeeds when work_dir misplaced inside args (no relative path-like conflict)
@pytest.mark.anyio
async def test_run_python_auto_promotes_work_dir_from_args(tool_ctx_kitchen_open, tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    output_dir = tmp_path / "output"  # absolute — validate passes, auto-promotion works
    result_json = await run_python(
        callable="autoskillit.smoke_utils.diagnose_merge_gate",
        args={
            "test_stdout": "FAILED test_foo",
            "test_stderr": "",
            "output_dir": str(output_dir),
            "work_dir": str(work_dir),  # misplaced inside args
        },
        work_dir="",  # empty top-level
    )
    result = json.loads(result_json)
    assert result["success"] is True
    expected = output_dir / "diagnosis.md"
    assert expected.exists()


# T2b: work_dir is stripped by sentinel introspection for callables that don't accept it
@pytest.mark.anyio
async def test_run_python_strips_work_dir_from_args_after_promotion(
    tool_ctx_kitchen_open, tmp_path
):
    result_json = await run_python(
        callable="tests.server._type_coercion_fixtures._typed_callable",
        args={
            "name": "test",
            "count": "1",
            "work_dir": str(tmp_path),
        },
        work_dir="",
    )
    result = json.loads(result_json)
    assert result["success"] is True
    assert result["result"] == {"name": "test", "count": 1, "ratio": 1.0}


# T2b2: work_dir is preserved for callables that accept it
@pytest.mark.anyio
async def test_run_python_preserves_work_dir_for_callable_that_accepts_it(
    tool_ctx_kitchen_open, tmp_path
):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    result_json = await run_python(
        callable="tests.server._type_coercion_fixtures._work_dir_param",
        args={
            "base_branch": "main",
            "work_dir": str(work_dir),
        },
        work_dir="",
    )
    result = json.loads(result_json)
    assert result["success"] is True
    assert result["result"]["work_dir"] == str(work_dir)
    assert result["result"]["base_branch"] == "main"


# T2c: run_python surfaces diagnostic when work_dir is misplaced AND path-like arg is relative
@pytest.mark.anyio
async def test_run_python_surfaces_diagnostic_when_work_dir_misplaced_with_relative_path(
    tool_ctx_kitchen_open, tmp_path
):
    result_json = await run_python(
        callable="some.module.func",
        args={
            "output_dir": ".autoskillit/temp/test",  # relative — triggers diagnostic
            "work_dir": str(tmp_path),  # misplaced inside args
        },
        work_dir="",
    )
    result = json.loads(result_json)
    assert result["success"] is False
    assert "inside args" in result["error"]


# T3a: validate_path_arg_anchoring mentions misplaced work_dir in error
def test_validate_path_arg_anchoring_detects_misplaced_work_dir():
    args: dict[str, object] = {"output_dir": "rel/path", "work_dir": "/abs/path"}
    err = validate_path_arg_anchoring(args, "")
    assert err is not None
    assert "inside args" in err
