"""Tests for compose_pr_body_guard PreToolUse hook.

Validates that gh pr create --body-file is intercepted and denied when the
body file lacks a Closes #N reference and the prep file specifies a
closing_issue.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY, NEW_SUBDIR_BASENAMES

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_prep_file(tmp_path: Path, closing_issue: str) -> Path:
    """Create a prep file at tmp_path/.autoskillit/temp/prepare-pr/pr_prep_test.md.

    When closing_issue is empty, the line reads ``- closing_issue: `` (trailing
    space, no value).
    """
    prep_path = tmp_path / ".autoskillit" / "temp" / "prepare-pr" / "pr_prep_test.md"
    prep_path.parent.mkdir(parents=True, exist_ok=True)
    content = f"""# PR Prep: Test

## Metadata

- feature_branch: test-branch
- base_branch: main
- closing_issue: {closing_issue}
- plan_paths: test_plan.md

## Title

Test PR
"""
    prep_path.write_text(content, encoding="utf-8")
    return prep_path


def _setup_body_file(tmp_path: Path, content: str) -> Path:
    """Create a body file at tmp_path/.autoskillit/temp/compose-pr/pr_body_test.md."""
    body_path = tmp_path / ".autoskillit" / "temp" / "compose-pr" / "pr_body_test.md"
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_text(content, encoding="utf-8")
    return body_path


def _build_event(command: str, tool_name: str = "Bash") -> dict:
    """Build the stdin JSON dict. Uses 'command' key for Bash, 'cmd' for run_cmd."""
    if tool_name.startswith("mcp__") and tool_name.endswith("__run_cmd"):
        return {"tool_name": tool_name, "tool_input": {"cmd": command}}
    return {"tool_name": tool_name, "tool_input": {"command": command}}


def _run_hook(
    event: dict,
    monkeypatch,
    *,
    headless: bool = True,
) -> str:
    """Import main(), patch sys.stdin, capture stdout. Returns stdout string."""
    from autoskillit.hooks.guards.compose_pr_body_guard import main  # noqa: PLC0415

    if headless:
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    else:
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

    stdin_text = json.dumps(event)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            main()
    except SystemExit:
        pass
    return buf.getvalue()


def _is_denied(output: str) -> bool:
    if not output:
        return False
    data = json.loads(output)
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# ---------------------------------------------------------------------------
# T1: TestScopingGates
# ---------------------------------------------------------------------------


class TestScopingGates:
    def test_allows_non_compose_pr_skill(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "implement-wp")
        monkeypatch.chdir(tmp_path)
        body = _setup_body_file(tmp_path, "Closes #123")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_no_skill_name_set(self, monkeypatch, tmp_path):
        monkeypatch.delenv("AUTOSKILLIT_SKILL_NAME", raising=False)
        monkeypatch.chdir(tmp_path)
        body = _setup_body_file(tmp_path, "Closes #123")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_non_gh_pr_create_command(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        event = _build_event("git push origin main")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_gh_pr_create_without_body_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        event = _build_event("gh pr create --fill")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""


# ---------------------------------------------------------------------------
# T2: TestFailOpen
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_malformed_json_stdin(self, monkeypatch, tmp_path):
        from autoskillit.hooks.guards.compose_pr_body_guard import main  # noqa: PLC0415

        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.stdin", io.StringIO("not valid json {{{"))
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                main()
        except SystemExit:
            pass
        assert buf.getvalue() == ""

    def test_missing_tool_input_key(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        event = {"tool_name": "Bash"}
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_body_file_does_not_exist(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        event = _build_event("gh pr create --body-file /nonexistent/path --base main")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_no_prep_files_exist(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        body = _setup_body_file(tmp_path, "Some content with no closing ref")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_shlex_split_failure(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        event = _build_event('gh pr create --body-file "unclosed --base main')
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""


# ---------------------------------------------------------------------------
# T3: TestAllow
# ---------------------------------------------------------------------------


class TestAllow:
    def test_allows_body_with_closes_ref(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Some description\n\nCloses #123")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_when_closing_issue_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "")
        body = _setup_body_file(tmp_path, "Some content with no closing ref")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_alternative_closing_keywords(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Fixes #123")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_resolves_keyword(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Resolves #123")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_case_insensitive_keyword(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "closes #123")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_colon_form(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Closes: #123")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_any_closing_ref_regardless_of_number(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Closes #999")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""


# ---------------------------------------------------------------------------
# T4: TestDeny
# ---------------------------------------------------------------------------


class TestDeny:
    def test_denies_missing_closes_reference(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Some description with no closing ref")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)

    def test_deny_reason_contains_issue_number(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Some description with no closing ref")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        data = json.loads(output)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "123" in reason

    def test_deny_reason_contains_trigger(self, monkeypatch, tmp_path):
        from autoskillit.hooks.guards.compose_pr_body_guard import (  # noqa: PLC0415
            COMPOSE_PR_BODY_DENY_TRIGGER,
        )

        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Some description with no closing ref")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        data = json.loads(output)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert COMPOSE_PR_BODY_DENY_TRIGGER in reason

    def test_deny_reason_is_corrective(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Some description with no closing ref")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        data = json.loads(output)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Closes #123" in reason


# ---------------------------------------------------------------------------
# T5: TestBodyFileExtraction
# ---------------------------------------------------------------------------


class TestBodyFileExtraction:
    def test_extracts_two_token_body_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Closes #123")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_extracts_fused_body_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Closes #123")
        cmd = f"gh pr create --body-file={body}"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_extracts_from_chained_command(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Closes #123")
        cmd = f"echo ok && gh pr create --body-file {body}"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_relative_body_path_resolved(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Closes #123")
        rel = body.relative_to(tmp_path)
        cmd = f"gh pr create --body-file {rel}"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_stops_at_shell_operator(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        # The --body-file here belongs to curl, not gh pr create. Guard should allow.
        event = _build_event('gh pr create --title "x" && curl --body-file /unrelated')
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_finds_second_gh_pr_create_with_body_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Closes #123")
        cmd = f"gh pr create --fill && gh pr create --body-file {body}"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""


# ---------------------------------------------------------------------------
# T6: TestPrepFileParsing
# ---------------------------------------------------------------------------


class TestPrepFileParsing:
    def test_parses_issue_from_metadata(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "456")
        body = _setup_body_file(tmp_path, "No closing reference here")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        data = json.loads(output)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "456" in reason

    def test_uses_most_recent_prep_file(self, monkeypatch, tmp_path):
        import os
        import time

        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        # Create two prep files with explicit mtimes — older one has 100, newer has 999
        prep_dir = tmp_path / ".autoskillit" / "temp" / "prepare-pr"
        prep_dir.mkdir(parents=True, exist_ok=True)
        old = prep_dir / "pr_prep_old.md"
        new = prep_dir / "pr_prep_new.md"
        old.write_text(
            "# PR Prep: Old\n\n## Metadata\n\n- closing_issue: 111\n\n## Title\n\nOld\n",
            encoding="utf-8",
        )
        new.write_text(
            "# PR Prep: New\n\n## Metadata\n\n- closing_issue: 222\n\n## Title\n\nNew\n",
            encoding="utf-8",
        )
        os.utime(old, (1000, 1000))
        time.sleep(0.05)
        os.utime(new, (2000, 2000))
        body = _setup_body_file(tmp_path, "No closing reference here")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        data = json.loads(output)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "222" in reason
        assert "111" not in reason

    def test_handles_missing_metadata_section(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        prep_dir = tmp_path / ".autoskillit" / "temp" / "prepare-pr"
        prep_dir.mkdir(parents=True, exist_ok=True)
        (prep_dir / "pr_prep_test.md").write_text(
            "# PR Prep: No metadata\n\n## Title\n\nSomething\n",
            encoding="utf-8",
        )
        body = _setup_body_file(tmp_path, "No closing reference here")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""


# ---------------------------------------------------------------------------
# T7: TestRegistration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_hook_registered_in_registry(self):
        """compose_pr_body_guard.py is registered in HOOK_REGISTRY with correct shape."""
        all_scripts = {s for h in HOOK_REGISTRY for s in h.scripts}
        assert "guards/compose_pr_body_guard.py" in all_scripts

        # Find the HookDef containing this script
        matching = [h for h in HOOK_REGISTRY if "guards/compose_pr_body_guard.py" in h.scripts]
        assert matching, "No HookDef found for compose_pr_body_guard.py"
        hookdef = matching[0]
        assert hookdef.event_type == "PreToolUse"
        assert hookdef.matcher == r"Bash|mcp__.*autoskillit.*__run_cmd"

    def test_in_new_subdir_basenames(self):
        """compose_pr_body_guard.py is in NEW_SUBDIR_BASENAMES."""
        assert "compose_pr_body_guard.py" in NEW_SUBDIR_BASENAMES


# ---------------------------------------------------------------------------
# T8: TestRunCmdVariant
# ---------------------------------------------------------------------------


class TestRunCmdVariant:
    def test_run_cmd_tool_name_detected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Some description with no closing ref")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd, tool_name="mcp__autoskillit__local__autoskillit__run_cmd")
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)


# ---------------------------------------------------------------------------
# T9: TestSessionScope
# ---------------------------------------------------------------------------


class TestSessionScope:
    def test_allows_when_not_headless(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Some description with no closing ref")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=False)
        assert output == ""

    def test_denies_when_headless(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "compose-pr")
        monkeypatch.chdir(tmp_path)
        _setup_prep_file(tmp_path, "123")
        body = _setup_body_file(tmp_path, "Some description with no closing ref")
        cmd = f"gh pr create --body-file {body} --base main"
        event = _build_event(cmd)
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)
