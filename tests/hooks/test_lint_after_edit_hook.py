"""Tests for lint_after_edit_hook.py PostToolUse hook."""

from __future__ import annotations

import io
import json
import subprocess as sp
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _build_event(
    tool_name: str,
    file_path: str,
    tool_response: str = "The file was edited successfully.",
) -> dict:
    return {
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
        "tool_response": tool_response,
    }


def _run_hook(
    event: dict | str,
    *,
    headless: bool = False,
    skill_name: str = "",
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, int]:
    from autoskillit.hooks.lint_after_edit_hook import main  # noqa: PLC0415

    if headless:
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    else:
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

    if skill_name:
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", skill_name)
    else:
        monkeypatch.delenv("AUTOSKILLIT_SKILL_NAME", raising=False)

    stdin_text = json.dumps(event) if isinstance(event, dict) else event
    buf = io.StringIO()
    exit_code = 0
    with (
        patch("sys.stdin", io.StringIO(stdin_text)),
        redirect_stdout(buf),
    ):
        try:
            main()
        except SystemExit as exc:
            exit_code = int(exc.code) if exc.code is not None else 0
    return buf.getvalue(), exit_code


class TestScopingGates:
    """Hook must be silent outside its activation scope."""

    def test_no_op_when_interactive(self, tmp_path, monkeypatch):
        f = tmp_path / "x.py"
        f.write_text("x = 1\n")
        out, code = _run_hook(
            _build_event("Edit", str(f)),
            headless=False,
            monkeypatch=monkeypatch,
        )
        assert out == "" and code == 0

    def test_no_op_when_non_implement_skill(self, tmp_path, monkeypatch):
        f = tmp_path / "x.py"
        f.write_text("x = 1\n")
        out, code = _run_hook(
            _build_event("Edit", str(f)),
            headless=True,
            skill_name="review-pr",
            monkeypatch=monkeypatch,
        )
        assert out == "" and code == 0

    def test_no_op_for_non_python_file(self, tmp_path, monkeypatch):
        f = tmp_path / "data.yaml"
        f.write_text("key: value\n")
        out, code = _run_hook(
            _build_event("Edit", str(f)),
            headless=True,
            skill_name="implement-worktree",
            monkeypatch=monkeypatch,
        )
        assert out == "" and code == 0

    def test_no_op_for_nonexistent_file(self, monkeypatch):
        out, code = _run_hook(
            _build_event("Edit", "/nonexistent/path.py"),
            headless=True,
            skill_name="implement-worktree",
            monkeypatch=monkeypatch,
        )
        assert out == "" and code == 0

    @pytest.mark.parametrize(
        "skill",
        [
            "implement-worktree-no-merge",
            "implement-worktree",
            "implement-experiment",
        ],
    )
    def test_activates_for_implement_skills(self, tmp_path, monkeypatch, skill):
        f = tmp_path / "clean.py"
        f.write_text("x = 1\n")
        _out, code = _run_hook(
            _build_event("Edit", str(f)),
            headless=True,
            skill_name=skill,
            monkeypatch=monkeypatch,
        )
        assert code == 0

    @pytest.mark.parametrize(
        "skill",
        [
            "resolve-review",
            "resolve-failures",
            "resolve-research-review",
        ],
    )
    def test_activates_for_resolve_skills(self, tmp_path, monkeypatch, skill):
        f = tmp_path / "clean.py"
        f.write_text("x = 1\n")
        _out, code = _run_hook(
            _build_event("Edit", str(f)),
            headless=True,
            skill_name=skill,
            monkeypatch=monkeypatch,
        )
        assert code == 0

    def test_write_tool_also_triggers(self, tmp_path, monkeypatch):
        f = tmp_path / "clean.py"
        f.write_text("x = 1\n")
        _out, code = _run_hook(
            _build_event("Write", str(f)),
            headless=True,
            skill_name="implement-worktree",
            monkeypatch=monkeypatch,
        )
        assert code == 0


class TestFailOpen:
    """Hook must fail-open on malformed input or missing tools."""

    def test_malformed_stdin(self, monkeypatch):
        out, code = _run_hook(
            "not json at all",
            headless=True,
            skill_name="implement-worktree",
            monkeypatch=monkeypatch,
        )
        assert out == "" and code == 0

    def test_missing_tool_input(self, monkeypatch):
        out, code = _run_hook(
            {},
            headless=True,
            skill_name="implement-worktree",
            monkeypatch=monkeypatch,
        )
        assert out == "" and code == 0

    def test_ruff_not_found(self, tmp_path, monkeypatch):
        f = tmp_path / "x.py"
        f.write_text("x=1\n")
        with patch(
            "autoskillit.hooks.lint_after_edit_hook.subprocess.run",
            side_effect=FileNotFoundError("ruff not found"),
        ):
            out, code = _run_hook(
                _build_event("Edit", str(f)),
                headless=True,
                skill_name="implement-worktree",
                monkeypatch=monkeypatch,
            )
        assert out == "" and code == 0

    def test_subprocess_timeout(self, tmp_path, monkeypatch):
        f = tmp_path / "x.py"
        f.write_text("x=1\n")
        with patch(
            "autoskillit.hooks.lint_after_edit_hook.subprocess.run",
            side_effect=sp.TimeoutExpired("ruff", 15),
        ):
            out, code = _run_hook(
                _build_event("Edit", str(f)),
                headless=True,
                skill_name="implement-worktree",
                monkeypatch=monkeypatch,
            )
        assert out == "" and code == 0


class TestLintBehavior:
    """Hook must detect and report lint issues via ruff."""

    def test_clean_file_silent(self, tmp_path, monkeypatch):
        f = tmp_path / "clean.py"
        f.write_text("x = 1\n")
        out, code = _run_hook(
            _build_event("Edit", str(f)),
            headless=True,
            skill_name="implement-worktree",
            monkeypatch=monkeypatch,
        )
        assert out == "" and code == 0

    def test_autofix_signals_reread(self, tmp_path, monkeypatch):
        f = tmp_path / "bad_fmt.py"
        f.write_text("x=1\n")
        out, code = _run_hook(
            _build_event("Edit", str(f)),
            headless=True,
            skill_name="implement-worktree",
            monkeypatch=monkeypatch,
        )
        assert code == 0
        from autoskillit.hooks.lint_after_edit_hook import (
            LINT_AUTOFIX_TRIGGER,
        )

        assert out != "", "ruff should auto-format x=1 to x = 1"
        parsed = json.loads(out)
        updated = parsed["hookSpecificOutput"]["updatedToolResult"]
        assert LINT_AUTOFIX_TRIGGER in updated
        assert "re-read" in updated.lower()

    def test_unused_import_not_removed(self, tmp_path, monkeypatch):
        """F4xx import rules must NOT be auto-fixed — import may be used in a later edit."""
        f = tmp_path / "staged_import.py"
        f.write_text("import asyncio\n\nx = 1\n")
        out, code = _run_hook(
            _build_event("Edit", str(f)),
            headless=True,
            skill_name="implement-worktree",
            monkeypatch=monkeypatch,
        )
        assert code == 0
        # The import must survive — ruff should NOT have removed it
        assert "import asyncio" in f.read_text()

    def test_unfixable_error_reported(self, tmp_path, monkeypatch):
        f = tmp_path / "long_line.py"
        long_var = "a" * 200
        f.write_text(f'{long_var} = "x"\n')
        out, code = _run_hook(
            _build_event("Edit", str(f)),
            headless=True,
            skill_name="implement-worktree",
            monkeypatch=monkeypatch,
        )
        assert code == 0
        from autoskillit.hooks.lint_after_edit_hook import LINT_ERROR_TRIGGER

        assert out != "", "E501 should be reported for 200-char variable"
        parsed = json.loads(out)
        updated = parsed["hookSpecificOutput"]["updatedToolResult"]
        assert LINT_ERROR_TRIGGER in updated

    def test_autofix_ignore_flag_is_narrow(self):
        """The --ignore flag in the ruff check --fix call must be 'F4', not a real rule code.

        'F4' is a no-op prefix in ruff (prefix matching is not supported), which prevents
        the auto-fix pass from suppressing real violations like F401, F811, or E501.
        """
        from pathlib import Path

        hook_source = (
            Path(__file__).parent.parent.parent
            / "src"
            / "autoskillit"
            / "hooks"
            / "lint_after_edit_hook.py"
        ).read_text()
        assert '"--ignore", "F4"' in hook_source or "'--ignore', 'F4'" in hook_source, (
            "lint_after_edit_hook.py --ignore flag in auto-fix call changed from 'F4'"
        )
        for bad_rule in ("F401", "F811", "E501"):
            assert f'"--ignore", "{bad_rule}"' not in hook_source, (
                f"lint_after_edit_hook.py suppresses {bad_rule} in auto-fix pass"
            )


class TestOutputContract:
    """updatedToolResult must not contain the original tool_response content."""

    def test_output_excludes_tool_response_content(self, tmp_path, monkeypatch):
        """updatedToolResult must not contain tool_response content."""
        f = tmp_path / "bad_fmt.py"
        f.write_text("x=1\n")
        large_tool_response = "The file was edited successfully. originalFile: " + "A" * 10000
        out, code = _run_hook(
            _build_event("Edit", str(f), tool_response=large_tool_response),
            headless=True,
            skill_name="implement-worktree",
            monkeypatch=monkeypatch,
        )
        assert code == 0
        assert out != "", "Hook should emit output for bad_fmt.py"
        parsed = json.loads(out)
        updated = parsed["hookSpecificOutput"]["updatedToolResult"]
        assert "originalFile:" not in updated
        assert "A" * 100 not in updated

    def test_output_excludes_short_tool_response(self, tmp_path, monkeypatch):
        """Even short tool_response must not appear in updatedToolResult."""
        f = tmp_path / "bad_fmt.py"
        f.write_text("x=1\n")
        out, code = _run_hook(
            _build_event("Edit", str(f), tool_response="The file was edited."),
            headless=True,
            skill_name="implement-worktree",
            monkeypatch=monkeypatch,
        )
        assert code == 0
        assert out != ""
        parsed = json.loads(out)
        updated = parsed["hookSpecificOutput"]["updatedToolResult"]
        assert "The file was edited." not in updated


class TestRegistration:
    def test_hook_registered_in_registry(self):
        from autoskillit.hook_registry import HOOK_REGISTRY

        post_write_edit = [
            h
            for h in HOOK_REGISTRY
            if h.event_type == "PostToolUse" and h.matcher == r"Write|Edit"
        ]
        assert len(post_write_edit) == 1
        assert "lint_after_edit_hook.py" in post_write_edit[0].scripts
        assert post_write_edit[0].session_scope == "headless_only"
