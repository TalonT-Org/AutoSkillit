"""Tests: token_summary_appender unit helpers and order_id isolation."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.hooks._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS
from tests.infra._token_summary_helpers import _run_hook, _write_sessions

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def test_tsa7_canonical_step_name() -> None:
    """_canonical strips trailing -N suffix; non-digit suffixes are preserved."""
    from autoskillit.hooks.token_summary_hook import _canonical

    assert _canonical("plan-30") == "plan"
    assert _canonical("open-pr-2") == "open-pr"
    assert _canonical("open-pr") == "open-pr"
    assert _canonical("plan-1") == "plan"
    assert _canonical("") == ""
    assert _canonical("implement") == "implement"


def test_tsa_humanize_preserves_decimal() -> None:
    """_humanize must use str(n) not str(int(n)) for sub-1000 values."""
    from autoskillit.hooks.token_summary_hook import _humanize

    assert _humanize(999) == "999"
    assert _humanize(0) == "0"
    assert _humanize(None) == "0"
    assert _humanize(45.7) == "45.7", (
        "str(int(n)) truncates decimals — must be str(n) to match telemetry_fmt.py"
    )


def _make_run_skill_event_with_order_id(
    result_text: str = "Done.\n%%ORDER_UP%%", order_id: str = ""
) -> dict:
    """Create a double-wrapped PostToolUse event for run_skill with optional order_id."""
    inner: dict = {"result": result_text, "success": True}
    if order_id:
        inner["order_id"] = order_id
    outer = {"result": json.dumps(inner)}
    return {
        "tool_name": "mcp__autoskillit_server__run_skill",
        "tool_response": json.dumps(outer),
    }


def test_e1_order_id_isolation_multi_issue_session(tmp_path: Path) -> None:
    """E-1: Three issues sharing kitchen_id; hook with order_id='B' includes only B's sessions."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    kitchen_id = "shared-kitchen"

    (log_root / "sessions.jsonl").write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {
                    "dir_name": "sess-A",
                    "cwd": "/w",
                    "kitchen_id": kitchen_id,
                    "order_id": "issue-A",
                    "step_name": "plan",
                },
                {
                    "dir_name": "sess-B",
                    "cwd": "/w",
                    "kitchen_id": kitchen_id,
                    "order_id": "issue-B",
                    "step_name": "implement",
                },
                {
                    "dir_name": "sess-C",
                    "cwd": "/w",
                    "kitchen_id": kitchen_id,
                    "order_id": "issue-C",
                    "step_name": "review",
                },
            ]
        )
        + "\n"
    )
    for dir_name, step in [("sess-A", "plan"), ("sess-B", "implement"), ("sess-C", "review")]:
        d = log_root / "sessions" / dir_name
        d.mkdir(parents=True)
        (d / "token_usage.json").write_text(
            json.dumps(
                {
                    "step_name": step,
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "timing_seconds": 5.0,
                    "order_id": dir_name.replace("sess-", "issue-"),
                }
            )
        )

    pr_url = "https://github.com/owner/repo/pull/42"
    event = _make_run_skill_event_with_order_id(
        f"pr_url={pr_url}\n%%ORDER_UP%%", order_id="issue-B"
    )
    view_result = MagicMock(returncode=0, stdout="Existing PR body.")
    edit_calls: list = []

    def run_side(args, **kwargs):
        if "api" in args and "--method" not in args:
            return view_result
        if "api" in args and "--method" in args:
            edit_calls.append(args)
            return MagicMock(returncode=0)
        return MagicMock(returncode=0)

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": kitchen_id}))

    with patch("subprocess.run", side_effect=run_side):
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    assert len(edit_calls) == 1, "gh api PATCH must be called once"
    body_arg = edit_calls[0][edit_calls[0].index("--raw-field") + 1]
    body_content = body_arg[len("body="):]
    assert "implement" in body_content, "issue-B's step 'implement' must be in table"
    assert "plan" not in body_content, "issue-A's step 'plan' must NOT be in table"
    assert "review" not in body_content, "issue-C's step 'review' must NOT be in table"


def test_e2_fallback_to_kitchen_id_when_no_order_id(tmp_path: Path) -> None:
    """E-2: When run_skill result lacks 'order_id', hook falls back to kitchen_id filtering."""
    from tests.infra._token_summary_helpers import _make_run_skill_event

    log_root = tmp_path / "logs"
    log_root.mkdir()
    kitchen_id = "my-kitchen"

    _write_sessions(
        log_root,
        [
            {
                "dir_name": "sess-1",
                "cwd": "/w",
                "kitchen_id": kitchen_id,
                "step_name": "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 5.0,
            }
        ],
    )

    pr_url = "https://github.com/owner/repo/pull/10"
    event = _make_run_skill_event(f"pr_url={pr_url}\n%%ORDER_UP%%")

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": kitchen_id}))

    view_result = MagicMock(returncode=0, stdout="Existing body.")
    edit_calls: list = []

    def run_side(args, **kwargs):
        if "api" in args and "--method" not in args:
            return view_result
        if "api" in args and "--method" in args:
            edit_calls.append(args)
            return MagicMock(returncode=0)
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=run_side):
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    assert len(edit_calls) == 1, "gh api PATCH must be called when kitchen_id matches (fallback)"


def test_e3_backward_compat_sessions_without_order_id_field(tmp_path: Path) -> None:
    """E-3: Sessions without 'order_id' key are skipped when order_id filter is active."""
    log_root = tmp_path / "logs"
    log_root.mkdir()
    kitchen_id = "kitchen-xyz"

    (log_root / "sessions.jsonl").write_text(
        json.dumps(
            {
                "dir_name": "old-sess",
                "cwd": "/w",
                "kitchen_id": kitchen_id,
                "step_name": "plan",
            }
        )
        + "\n"
    )
    d = log_root / "sessions" / "old-sess"
    d.mkdir(parents=True)
    (d / "token_usage.json").write_text(
        json.dumps(
            {
                "step_name": "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 5.0,
            }
        )
    )

    pr_url = "https://github.com/owner/repo/pull/20"
    event = _make_run_skill_event_with_order_id(
        f"pr_url={pr_url}\n%%ORDER_UP%%", order_id="issue-X"
    )

    hook_config = tmp_path / ".autoskillit_hook_config.json"
    hook_config.write_text(json.dumps({"kitchen_id": kitchen_id}))

    with patch("subprocess.run") as mock_run:
        _, exit_code = _run_hook(event, log_root=log_root, hook_config_path=hook_config)

    assert exit_code == 0
    patch_calls = [
        call for call in mock_run.call_args_list if "--method" in (call[0][0] if call[0] else [])
    ]
    assert len(patch_calls) == 0, "Old sessions without order_id should be skipped"


def test_e4_kitchen_id_renamed_in_hook_config(tmp_path: Path) -> None:
    """E-4: _read_kitchen_id reads 'kitchen_id' key; falls back to 'pipeline_id' for old."""
    from autoskillit.hooks.token_summary_hook import _read_kitchen_id

    cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({"kitchen_id": "new-kitchen-uuid"}))

    result = _read_kitchen_id(base=tmp_path)
    assert result == "new-kitchen-uuid"

    cfg_path.write_text(json.dumps({"pipeline_id": "legacy-pipeline-uuid"}))
    result = _read_kitchen_id(base=tmp_path)
    assert result == "legacy-pipeline-uuid"


class TestUnwrapMcpResponse:
    """Unit tests for the shared double-unwrap helper."""

    def _call(self, tool_name: str, raw: str):
        from autoskillit.hooks.token_summary_hook import _unwrap_mcp_response

        return _unwrap_mcp_response(tool_name, raw)

    def test_invalid_json_returns_none(self):
        assert self._call("mcp__x__y", "not json") is None

    def test_json_array_returns_none(self):
        assert self._call("mcp__x__y", json.dumps([1, 2, 3])) is None

    def test_non_mcp_tool_returns_outer_dict(self):
        payload = {"result": "some text", "success": True}
        result = self._call("run_python", json.dumps(payload))
        assert result == payload

    def test_mcp_tool_double_wrapped_returns_inner(self):
        inner = {"result": "https://github.com/o/r/pull/1", "order_id": "abc"}
        outer = {"result": json.dumps(inner)}
        result = self._call("mcp__autoskillit__run_skill", json.dumps(outer))
        assert result == inner

    def test_mcp_tool_inner_parse_fails_returns_outer(self):
        outer = {"result": "not-json-inner"}
        result = self._call("mcp__autoskillit__run_skill", json.dumps(outer))
        assert result == outer

    def test_mcp_tool_inner_not_dict_returns_outer(self):
        outer = {"result": json.dumps([1, 2, 3])}
        result = self._call("mcp__autoskillit__run_skill", json.dumps(outer))
        assert result == outer

    def test_mcp_tool_extra_keys_skips_double_unwrap(self):
        """Outer dict with more than just 'result' key → not a double-wrapped response."""
        outer = {"result": json.dumps({"foo": "bar"}), "extra": "key"}
        result = self._call("mcp__autoskillit__run_skill", json.dumps(outer))
        assert result == outer


def test_hook_subprocess_calls_have_timeout() -> None:
    """All subprocess.run() calls in token_summary_hook.py must have timeout=."""
    src = Path("src/autoskillit/hooks/token_summary_hook.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
        ):
            kw_names = {kw.arg for kw in node.keywords}
            assert "timeout" in kw_names, (
                f"subprocess.run() at line {node.lineno} in token_summary_hook.py missing timeout="
            )
