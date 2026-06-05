"""Tests for write_guard.py PreToolUse hook."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _build_event(tool_name: str, file_path: str) -> dict:
    return {"tool_name": tool_name, "tool_input": {"file_path": file_path}}


def _run_hook(event: dict | str) -> str:
    from autoskillit.hooks.guards.write_guard import main

    stdin_text = json.dumps(event) if isinstance(event, dict) else event
    buf = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(stdin_text)),
        redirect_stdout(buf),
    ):
        try:
            main()
        except SystemExit:
            pass
    return buf.getvalue()


def _set_headless(monkeypatch: pytest.MonkeyPatch, *, headless: bool) -> None:
    if headless:
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    else:
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)


class TestWriteGuardNoHeadless:
    def test_no_headless_env_allows_all_writes(self, monkeypatch: pytest.MonkeyPatch):
        _set_headless(monkeypatch, headless=False)
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/clone/.autoskillit/temp/")
        result = _run_hook(_build_event("Write", "/clone/src/foo.py"))
        assert result == ""


class TestWriteGuardNoEnv:
    def test_no_env_var_allows_all_writes(self, monkeypatch: pytest.MonkeyPatch):
        _set_headless(monkeypatch, headless=True)
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", raising=False)
        result = _run_hook(_build_event("Write", "/src/foo.py"))
        assert result == ""

    def test_no_json_allows_when_no_prefix(self, monkeypatch: pytest.MonkeyPatch):
        _set_headless(monkeypatch, headless=True)
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", raising=False)
        result = _run_hook("not json at all")
        assert result == ""


class TestWriteGuardEnvIsolation:
    """Regression: leaked AUTOSKILLIT_ALLOWED_WRITE_PREFIXES must not affect
    tests that expect no write restriction."""

    def test_plural_prefix_does_not_leak(self, monkeypatch: pytest.MonkeyPatch):
        _set_headless(monkeypatch, headless=True)
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", raising=False)
        result = _run_hook(_build_event("Write", "/src/foo.py"))
        assert result == ""


class TestWriteGuardWithPrefix:
    PREFIX = "/clone/.autoskillit/temp/investigate/"

    @pytest.fixture(autouse=True)
    def _enable_headless(self, monkeypatch: pytest.MonkeyPatch):
        _set_headless(monkeypatch, headless=True)

    def test_write_within_prefix_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_event("Write", "/clone/.autoskillit/temp/investigate/report.md")
        result = _run_hook(event)
        assert result == ""

    def test_write_outside_prefix_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_event("Write", "/clone/src/autoskillit/foo.py")
        result = _run_hook(event)
        parsed = json.loads(result)
        decision = parsed["hookSpecificOutput"]["permissionDecision"]
        reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
        assert decision == "deny"
        assert "read-only skill session" in reason

    def test_edit_outside_prefix_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_event("Edit", "/clone/tests/test_foo.py")
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_malformed_json_denies_in_readonly_session(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        result = _run_hook("not valid json")
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "malformed" in parsed["hookSpecificOutput"]["permissionDecisionReason"]

    def test_missing_file_path_denies(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = {"tool_name": "Write", "tool_input": {}}
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "no file_path" in parsed["hookSpecificOutput"]["permissionDecisionReason"]

    def test_non_write_tool_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = {"tool_name": "Read", "tool_input": {"file_path": "/clone/src/foo.py"}}
        result = _run_hook(event)
        assert result == ""

    def test_symlink_resolved(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        target_file = real_dir / "secret.py"
        target_file.write_text("x")

        allowed = tmp_path / "allowed"
        allowed.mkdir()
        link = allowed / "link.py"
        link.symlink_to(target_file)

        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", str(allowed) + "/")
        event = _build_event("Write", str(link))
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"


def _build_bash_event(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def _build_apply_patch_event(patch_text: str) -> dict:
    return {"tool_name": "apply_patch", "tool_input": {"command": patch_text}}


class TestWriteGuardApplyPatch:
    """write_guard intercepts apply_patch tool calls with unified diff path extraction."""

    @pytest.fixture(autouse=True)
    def _enable_headless(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

    def test_apply_patch_within_prefix_allowed(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", str(allowed) + "/")
        patch_text = f"--- a/old.py\n+++ b/{allowed}/foo.py\n@@ -1 +1 @@\n-old\n+new"
        result = _run_hook(_build_apply_patch_event(patch_text))
        assert result == ""

    def test_apply_patch_outside_prefix_denied(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", str(allowed) + "/")
        patch_text = "--- a/old.py\n+++ b/outside/bar.py\n@@ -1 +1 @@\n-old\n+new"
        result = _run_hook(_build_apply_patch_event(patch_text))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert (
            "read-only skill session" in parsed["hookSpecificOutput"]["permissionDecisionReason"]
        )

    def test_apply_patch_multiple_files_one_outside_denied(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ):
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", str(allowed) + "/")
        patch_text = (
            f"--- a/a.py\n+++ b/{allowed}/a.py\n@@ -1 +1 @@\n-x\n+y\n"
            f"--- a/b.py\n+++ b/outside/b.py\n@@ -1 +1 @@\n-x\n+y"
        )
        result = _run_hook(_build_apply_patch_event(patch_text))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_apply_patch_no_target_paths_denies(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", str(allowed) + "/")
        patch_text = "some random text\nwithout any diff headers\n"
        result = _run_hook(_build_apply_patch_event(patch_text))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert (
            "no target paths found in patch"
            in parsed["hookSpecificOutput"]["permissionDecisionReason"]
        )

    def test_apply_patch_empty_command_denies(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        allowed = tmp_path / "workspace"
        allowed.mkdir()
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", str(allowed) + "/")
        result = _run_hook(_build_apply_patch_event(""))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestWriteGuardCodexPatchFormat:
    """Tests for Codex's *** Update/Add/Delete File: format."""

    def test_extract_paths_from_codex_update_file(self):
        from autoskillit.hooks.guards.write_guard import _extract_paths_from_patch

        patch = (
            "*** Begin Patch\n"
            "*** Update File: src/main.rs\n"
            "@@ -1,3 +1,4 @@\n"
            "+new line\n"
            "*** End Patch"
        )
        paths = _extract_paths_from_patch(patch)
        assert paths == ["src/main.rs"]

    def test_extract_paths_from_codex_add_file(self):
        from autoskillit.hooks.guards.write_guard import _extract_paths_from_patch

        patch = "*** Begin Patch\n*** Add File: src/new_module.rs\n+content\n*** End Patch"
        paths = _extract_paths_from_patch(patch)
        assert paths == ["src/new_module.rs"]

    def test_extract_paths_from_codex_delete_file(self):
        from autoskillit.hooks.guards.write_guard import _extract_paths_from_patch

        patch = "*** Begin Patch\n*** Delete File: src/old_module.rs\n*** End Patch"
        paths = _extract_paths_from_patch(patch)
        assert paths == ["src/old_module.rs"]

    def test_extract_paths_from_codex_multi_file_patch(self):
        from autoskillit.hooks.guards.write_guard import _extract_paths_from_patch

        patch = (
            "*** Begin Patch\n"
            "*** Update File: src/a.rs\n"
            "@@ ...\n"
            "+line\n"
            "*** Update File: src/b.rs\n"
            "@@ ...\n"
            "+line\n"
            "*** Add File: src/c.rs\n"
            "+new\n"
            "*** End Patch"
        )
        paths = _extract_paths_from_patch(patch)
        assert paths == ["src/a.rs", "src/b.rs", "src/c.rs"]

    def test_apply_patch_codex_format_allowed(self, monkeypatch, tmp_path):
        allowed = str(tmp_path / ".autoskillit" / "temp")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", allowed)

        patch = f"*** Begin Patch\n*** Update File: {allowed}/plan.md\n@@ ...\n+x\n*** End Patch"
        event = _build_apply_patch_event(patch)
        result = _run_hook(event)
        assert result == ""

    def test_apply_patch_codex_format_denied_outside_prefix(self, monkeypatch, tmp_path):
        allowed = str(tmp_path / ".autoskillit" / "temp")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", allowed)

        patch = "*** Begin Patch\n*** Update File: src/credentials.rs\n@@ ...\n+x\n*** End Patch"
        event = _build_apply_patch_event(patch)
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_extract_paths_handles_both_formats(self):
        from autoskillit.hooks.guards.write_guard import _extract_paths_from_patch

        patch = "+++ b/file_a.py\n*** Update File: file_b.rs\n"
        paths = _extract_paths_from_patch(patch)
        assert "file_a.py" in paths
        assert "file_b.rs" in paths


class TestWriteGuardToolNamesEnvVar:
    """AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES env var overrides the hardcoded tool set."""

    PREFIX = "/clone/.autoskillit/temp/"

    @pytest.fixture(autouse=True)
    def _enable_headless(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)

    def test_env_var_set_allows_non_listed_tool_passthrough(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES", "apply_patch,Bash")
        result = _run_hook(_build_event("Write", "/outside/foo.py"))
        assert result == ""

    def test_env_var_empty_string_falls_back_to_default_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES", "")
        result = _run_hook(_build_event("Write", "/outside/foo.py"))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_env_var_whitespace_only_falls_back_to_default_set(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES", "   ")
        result = _run_hook(_build_event("Write", "/outside/foo.py"))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_env_var_with_apply_patch_denies_codex_patch_outside_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES", "apply_patch,Bash")
        patch = (
            "*** Begin Patch\n"
            "*** Update File: /outside/credentials.rs\n"
            "@@ -1,3 +1,4 @@\n"
            "+new line\n"
            "*** End Patch"
        )
        result = _run_hook(_build_apply_patch_event(patch))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert (
            "read-only skill session" in parsed["hookSpecificOutput"]["permissionDecisionReason"]
        )


class TestWriteGuardBashBypass:
    """write_guard intercepts Bash tool calls containing file-modifying commands."""

    PREFIX = "/clone/.autoskillit/temp/planner/"

    @pytest.fixture(autouse=True)
    def _enable_headless(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

    def test_bash_sed_outside_prefix_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event("sed -i 's/foo/bar/' /clone/src/main.rs")
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert (
            "read-only skill session" in parsed["hookSpecificOutput"]["permissionDecisionReason"]
        )

    def test_bash_sed_inside_prefix_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event(
            "sed -i 's/foo/bar/' /clone/.autoskillit/temp/planner/context.json"
        )
        result = _run_hook(event)
        assert result == ""

    def test_bash_echo_redirect_outside_prefix_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event('echo "x" > /clone/src/lib.rs')
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_bash_tee_outside_prefix_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event("cat output.txt | tee /clone/src/config.rs")
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_bash_non_modifying_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event("grep foo /clone/src/main.rs")
        result = _run_hook(event)
        assert result == ""

    def test_bash_no_prefix_allows_all(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", raising=False)
        event = _build_bash_event("sed -i 's/x/y/' /clone/src/main.rs")
        result = _run_hook(event)
        assert result == ""


class TestExtractBashWriteTargets:
    """Unit tests for _extract_bash_write_targets -- the two-phase detect+extract logic."""

    def test_stderr_redirect_to_dev_null_not_blocked(self):
        """2>/dev/null should not produce a blocking target."""
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("gh auth status 2>/dev/null")
        assert result is None or result == []

    def test_stderr_redirect_with_space_not_blocked(self):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("gh auth status 2> /dev/null")
        assert result is None or result == []

    def test_stdout_to_dev_null_not_blocked(self):
        """>/dev/null is stdout redirect to a pseudo-device -- not a real file write."""
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("echo foo > /dev/null")
        assert result is None or result == []

    def test_combined_redirect_not_blocked(self):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("cmd > /dev/null 2>&1")
        assert result is None or result == []

    def test_fd3_redirect_to_dev_null_not_blocked(self):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("cmd 3>/dev/null")
        assert result is None or result == []

    def test_tee_dev_null_not_blocked(self):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("cmd | tee /dev/null")
        assert result is None or result == []

    def test_fd_redirect_to_real_path_detected(self):
        """2>/tmp/steal.log is a real file write -- must be detected and blocked."""
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("exec 2>/tmp/steal.log")
        assert result == ["/tmp/steal.log"]

    def test_fd_redirect_to_real_path_with_space_detected(self):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("cmd 2> /tmp/output.log")
        assert result == ["/tmp/output.log"]

    def test_real_file_write_detected(self):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("echo secret > /tmp/leak.txt")
        assert result == ["/tmp/leak.txt"]

    def test_sed_inplace_detected(self):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("sed -i 's/x/y/' /clone/src/main.py")
        assert result == ["/clone/src/main.py"]

    def test_non_write_command_returns_none(self):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("grep foo /clone/src/main.py")
        assert result is None

    def test_three_way_return_contract(self):
        """Verify the None / [] / [paths] contract."""
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        assert _extract_bash_write_targets("ls -la") is None
        result_filtered = _extract_bash_write_targets("echo x > /dev/null")
        assert result_filtered == []
        result_real = _extract_bash_write_targets("echo x > /tmp/out.txt")
        assert result_real == ["/tmp/out.txt"]

    @pytest.mark.parametrize(
        "command",
        [
            "x=$(grep foo 2>/dev/null)",
            'BRANCH=$(cat "${STORE_FILE}" 2>/dev/null)',
            "result=$(some_cmd 2>/dev/null) && echo $result",
            "x=`cmd 2>/dev/null`",
            "{ cmd 2>/dev/null; }",
        ],
        ids=["subshell_grep", "subshell_cat", "subshell_chain", "backtick", "brace_group"],
    )
    def test_subshell_redirect_to_dev_null_not_blocked(self, command):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets(command)
        assert result is None or result == [], f"Should not detect writes in: {command}"

    @pytest.mark.parametrize(
        "command,expected_targets,excluded_targets",
        [
            (
                "x=$(cmd 2>/tmp/err.log) && echo done > /tmp/out.txt",
                ["/tmp/out.txt"],
                ["/tmp/err.log"],
            ),
            (
                "x=$(grep errors 2>/tmp/debug.log)",
                [],
                ["/tmp/debug.log"],
            ),
        ],
        ids=["subshell_with_real_redirect", "subshell_only_real_path"],
    )
    def test_subshell_redirect_excludes_nested_real_paths(
        self, command, expected_targets, excluded_targets
    ):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets(command)
        if not expected_targets:
            assert result is None or result == [], f"Should not detect writes in: {command}"
        else:
            assert result is not None
            for expected in expected_targets:
                assert expected in result, f"Expected {expected} in result {result}"
        if result:
            for excluded in excluded_targets:
                assert excluded not in result, (
                    f"Subshell-internal redirect {excluded!r} must not appear in {result}"
                )
            for path in result:
                assert not path.endswith(")"), f"Path should not end with ')': {path}"
                assert not path.endswith("`"), f"Path should not end with backtick: {path}"
                assert not path.endswith("}"), f"Path should not end with '}}': {path}"

    @pytest.mark.parametrize(
        "cmd",
        [
            "sed -i 's/x/y/' /outside/file.txt",
            "tee /outside/file.txt",
            "mv /src /outside/dst",
            "cp /src /outside/dst",
            "patch /outside/file.txt",
            "rm /outside/file.txt",
            "unlink /outside/file.txt",
        ],
    )
    def test_all_write_cmd_families_have_deny_coverage(
        self, cmd: str, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/allowed/prefix/")
        result = _run_hook(_build_bash_event(cmd))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestWriteGuardRealisticCommands:
    """Integration tests: common agent-generated commands must not be blocked."""

    PREFIX = "/clone/.autoskillit/temp/compose-pr/"

    @pytest.fixture(autouse=True)
    def _enable_headless(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)

    def test_gh_auth_status_2_dev_null_allowed(self):
        """The exact command that caused pipeline 3 (#2864) failure."""
        result = _run_hook(_build_bash_event("gh auth status 2>/dev/null"))
        assert result == ""

    def test_curl_stderr_suppression_allowed(self):
        result = _run_hook(_build_bash_event("curl -s https://api.example.com 2>/dev/null"))
        assert result == ""

    def test_git_remote_2_dev_null_allowed(self):
        result = _run_hook(
            _build_bash_event(
                "git remote get-url upstream 2>/dev/null && echo upstream || echo origin"
            )
        )
        assert result == ""

    def test_which_2_dev_null_allowed(self):
        result = _run_hook(_build_bash_event("which gh 2>/dev/null || echo 'not found'"))
        assert result == ""

    def test_dev_null_combined_allowed(self):
        result = _run_hook(_build_bash_event("cmd > /dev/null 2>&1"))
        assert result == ""

    def test_tee_dev_null_allowed(self):
        result = _run_hook(_build_bash_event("cmd | tee /dev/null"))
        assert result == ""

    def test_combined_redirect_not_blocked_with_cwd(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _run_hook(_build_bash_event("cmd > /dev/null 2>&1"))
        assert result == ""

    def test_fd_redirect_only_not_blocked_with_cwd(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _run_hook(_build_bash_event("gh auth status 2>&1"))
        assert result == ""


class TestWriteGuardInterpreterBypass:
    """write_guard must detect interpreter-mediated writes (python3 heredocs, -c flag)."""

    PREFIX = "/clone/.autoskillit/temp/planner/"

    @pytest.fixture(autouse=True)
    def _enable_headless(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

    def test_python3_inline_write_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event("python3 -c \"open('/clone/src/foo.py','w').write('x')\"")
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_python3_heredoc_write_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        cmd = (
            "python3 - << 'PYEOF'\n"
            "from pathlib import Path\n"
            "Path('/clone/src/foo.py').write_text('x')\n"
            "PYEOF"
        )
        event = _build_bash_event(cmd)
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_env_python3_write_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event("env python3 -c \"open('/clone/src/foo.py','w').write('x')\"")
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_python3_read_only_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event("python3 -c \"print(open('/clone/src/foo.py').read())\"")
        result = _run_hook(event)
        assert result == ""

    def test_python3_explicit_read_mode_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event("python3 -c \"data = open('/clone/src/foo.py', 'r').read()\"")
        result = _run_hook(event)
        assert result == ""

    def test_python3_pytest_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event("python3 -m pytest tests/")
        result = _run_hook(event)
        assert result == ""

    def test_python3_write_text_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event(
            "python3 -c \"from pathlib import Path; Path('/clone/src/f.py').write_text('x')\""
        )
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_python3_write_bytes_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event(
            "python3 -c \"from pathlib import Path; Path('/clone/src/f.py').write_bytes(b'x')\""
        )
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_python3_heredoc_readonly_comparison_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        cmd = (
            "python3 - <<'EOF'\n"
            "if severity_rank[f['severity']] > severity_rank[deduped[key]['severity']]:\n"
            "    deduped[key] = f\n"
            "EOF"
        )
        event = _build_bash_event(cmd)
        result = _run_hook(event)
        assert result == ""  # empty = approve

    def test_heredoc_with_real_redirect_on_opening_line_denied(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        cmd = "cat <<'EOF' > /outside/prefix/output.txt\nsome content\nEOF"
        event = _build_bash_event(cmd)
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_python3_append_mode_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event("python3 -c \"open('/clone/src/f.py','a').write('x')\"")
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_python3_shutil_copy_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event(
            "python3 -c \"import shutil; shutil.copy('/tmp/a', '/clone/src/f.py')\""
        )
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_python3_mixed_read_write_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event(
            "python3 -c \"data=open('/clone/src/a.py').read();"
            " open('/clone/src/b.py','w').write(data)\""
        )
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_python_no_suffix_write_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event("python -c \"open('/clone/src/foo.py','w').write('x')\"")
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_python3_write_to_allowed_prefix_with_literal_path_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Interpreter writes with extractable literal paths are allowed within the prefix."""
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event(f"python3 -c \"open('{self.PREFIX}out.txt','w').write('x')\"")
        result = _run_hook(event)
        assert result == ""

    def test_python3_write_to_allowed_prefix_with_literal_open_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event(
            "python3 -c \"open('/clone/.autoskillit/temp/planner/out.json', 'w').write('x')\""
        )
        result = _run_hook(event)
        assert result == ""

    def test_python3_write_to_allowed_prefix_with_literal_write_text_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event(
            'python3 -c "from pathlib import Path; '
            "Path('/clone/.autoskillit/temp/planner/out.json').write_text('x')\""
        )
        result = _run_hook(event)
        assert result == ""

    def test_python3_write_to_allowed_prefix_with_literal_write_bytes_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event(
            'python3 -c "from pathlib import Path; '
            "Path('/clone/.autoskillit/temp/planner/out.bin').write_bytes(b'x')\""
        )
        result = _run_hook(event)
        assert result == ""

    def test_python3_write_outside_prefix_with_literal_path_denied(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event("python3 -c \"open('/clone/src/foo.py', 'w').write('x')\"")
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_python3_write_with_dynamic_path_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)
        event = _build_bash_event("python3 -c \"open(sys.argv[1], 'w').write('x')\"")
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestRelativePathResolution:
    """Tests for relative path resolution via AUTOSKILLIT_CWD."""

    def test_relative_sed_path_resolved_against_cwd(self, monkeypatch: pytest.MonkeyPatch):
        """sed -i with relative path should be resolved against AUTOSKILLIT_CWD and denied."""
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _extract_bash_write_targets("sed -i 's/x/y/' tests/foo.py")
        assert result is not None
        assert "/workspace/tests/foo.py" in result

    def test_relative_rm_path_resolved_against_cwd(self, monkeypatch: pytest.MonkeyPatch):
        """rm with relative path should be resolved against AUTOSKILLIT_CWD."""
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _extract_bash_write_targets("rm tests/foo.py")
        assert result is not None
        assert "/workspace/tests/foo.py" in result

    def test_relative_path_within_prefix_allowed(self, monkeypatch: pytest.MonkeyPatch):
        """Relative path within the allowed prefix should resolve and be allowed."""
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _extract_bash_write_targets("sed -i 's/x/y/' .autoskillit/temp/skill/output.txt")
        assert result is not None
        assert "/workspace/.autoskillit/temp/skill/output.txt" in result

    def test_no_cwd_env_skips_relative_resolution(self, monkeypatch: pytest.MonkeyPatch):
        """Without AUTOSKILLIT_CWD, relative paths are not resolved (fail-open)."""
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.delenv("AUTOSKILLIT_CWD", raising=False)
        result = _extract_bash_write_targets("sed -i 's/x/y/' tests/foo.py")
        assert result is not None
        assert result == []


class TestInterpreterRelativePathResolution:
    """Tests for interpreter write relative path resolution via AUTOSKILLIT_CWD."""

    def test_relative_open_resolved_against_cwd(self, monkeypatch: pytest.MonkeyPatch):
        """Relative open() path should be resolved and allowed when within prefix."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp")
        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        event = _build_bash_event(
            "python3 -c \"open('.autoskillit/temp/out.txt', 'w').write('x')\""
        )
        out = _run_hook(event)
        assert "blocked" not in out.lower()

    def test_relative_open_outside_prefix_denied(self, monkeypatch: pytest.MonkeyPatch):
        """Relative open() path outside prefix should be denied after resolution."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp")
        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        event = _build_bash_event("python3 -c \"open('src/main.py', 'w').write('x')\"")
        out = _run_hook(event)
        assert "blocked" in out.lower()

    def test_no_cwd_denies_relative_interpreter_write(self, monkeypatch: pytest.MonkeyPatch):
        """Without AUTOSKILLIT_CWD, relative interpreter writes are denied."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp")
        monkeypatch.delenv("AUTOSKILLIT_CWD", raising=False)
        event = _build_bash_event(
            "python3 -c \"open('.autoskillit/temp/out.txt', 'w').write('x')\""
        )
        out = _run_hook(event)
        assert "blocked" in out.lower()

    def test_non_absolute_cwd_denies_relative_interpreter_write(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Non-absolute AUTOSKILLIT_CWD denies relative interpreter writes."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp")
        monkeypatch.setenv("AUTOSKILLIT_CWD", "relative/cwd")
        event = _build_bash_event(
            "python3 -c \"open('.autoskillit/temp/out.txt', 'w').write('x')\""
        )
        out = _run_hook(event)
        assert "blocked" in out.lower()

    def test_multi_path_all_within_prefix_allowed(self, monkeypatch: pytest.MonkeyPatch):
        """Multiple open() calls all within prefix should be allowed."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp")
        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        event = _build_bash_event(
            'python3 -c "'
            "open('.autoskillit/temp/a.txt', 'w').write('x'); "
            "open('.autoskillit/temp/b.txt', 'w').write('y')\""
        )
        out = _run_hook(event)
        assert "blocked" not in out.lower()

    def test_multi_path_one_outside_prefix_denied(self, monkeypatch: pytest.MonkeyPatch):
        """If any of multiple open() paths is outside prefix, deny the command."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp")
        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        event = _build_bash_event(
            'python3 -c "'
            "open('.autoskillit/temp/a.txt', 'w').write('x'); "
            "open('src/main.py', 'w').write('y')\""
        )
        out = _run_hook(event)
        assert "blocked" in out.lower()


class TestWriteGuardMultiPrefix:
    """Tests for multi-prefix (AUTOSKILLIT_ALLOWED_WRITE_PREFIXES) support."""

    @pytest.fixture(autouse=True)
    def _enable_headless(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

    def test_multi_prefix_allows_path_under_any_prefix(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", "/a/:/b/")
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", raising=False)
        assert _run_hook(_build_event("Edit", "/a/file.py")) == ""
        assert _run_hook(_build_event("Edit", "/b/file.py")) == ""

    def test_multi_prefix_denies_path_outside_all_prefixes(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", "/a/:/b/")
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", raising=False)
        result = _run_hook(_build_event("Edit", "/c/file.py"))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_single_prefix_still_works(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/allowed/")
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", raising=False)
        assert _run_hook(_build_event("Edit", "/allowed/file.py")) == ""
        result = _run_hook(_build_event("Edit", "/other/file.py"))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_plural_takes_precedence_over_singular(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/a/")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", "/a/:/b/")
        assert _run_hook(_build_event("Edit", "/b/file.py")) == ""


class TestWriteGuardGhCommands:
    """gh CLI commands must never be blocked by the write guard."""

    PREFIX = "/clone/.autoskillit/temp/compose-pr/"

    @pytest.fixture(autouse=True)
    def _enable_headless(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", self.PREFIX)

    def test_gh_api_post_reviews_allowed(self):
        result = _run_hook(
            _build_bash_event("gh api /repos/Owner/Repo/pulls/123/reviews --method POST --input -")
        )
        assert result == ""

    def test_gh_api_method_patch_allowed(self):
        result = _run_hook(
            _build_bash_event("gh api --method patch /repos/Owner/Repo/pulls/123 --field body=foo")
        )
        assert result == ""

    def test_gh_pr_view_allowed(self):
        result = _run_hook(_build_bash_event("gh pr view 123 --json body"))
        assert result == ""

    def test_gh_issue_list_allowed(self):
        result = _run_hook(_build_bash_event("gh issue list --state open"))
        assert result == ""

    def test_gh_api_url_not_extracted_as_filesystem_path(self):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("gh api --method patch /repos/Owner/Repo/pulls/123")
        assert result is None or result == []


class TestExtractBashWriteTargetsNewFamilies:
    """Parameterized coverage for write-command families missing from original tests."""

    @pytest.mark.parametrize(
        "cmd,expected_targets",
        [
            ("mv /src/a.py /dst/b.py", ["/dst/b.py"]),
            ("cp /src/a.py /dst/b.py", ["/dst/b.py"]),
            ("patch /clone/src/main.py", ["/clone/src/main.py"]),
            ("rm /clone/src/old.py", ["/clone/src/old.py"]),
            ("unlink /clone/src/old.py", ["/clone/src/old.py"]),
            ("rm -rf /clone/src/dir/", ["/clone/src/dir/"]),
        ],
    )
    def test_mv_cp_rm_patch_write_cmd_family_extraction(
        self, cmd: str, expected_targets: list[str]
    ):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets(cmd)
        assert result == expected_targets

    @pytest.mark.parametrize(
        "cmd",
        [
            "gh api /repos/Owner/Repo/pulls/123/reviews --method POST --input -",
            "gh api --method patch /repos/Owner/Repo/pulls/123 --field body=...",
            "gh pr view 123 --json body",
            "gh issue list --state open",
            "gh api /repos/Owner/Repo/issues/42/comments --method POST -f body=test",
        ],
    )
    def test_gh_commands_not_extracted(self, cmd: str):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets(cmd)
        assert result is None or result == []

    def test_git_checkout_dash_dash_extracted(self):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("git checkout -- /clone/src/main.py")
        assert result is not None
        assert "/clone/src/main.py" in result

    def test_git_reset_hard_allowed_no_path(self):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("git reset --hard HEAD")
        assert result is None or result == []

    def test_git_with_flag_prefix_checkout_detected(self):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("git -C /repo checkout -- /clone/src/main.py")
        assert result is not None
        assert "/clone/src/main.py" in result

    def test_git_with_flag_prefix_reset_hard_detected(self):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        result = _extract_bash_write_targets("git -C /repo reset --hard")
        assert result is not None


class TestRedirectRelativePathResolution:
    """Tests for redirect relative path resolution via AUTOSKILLIT_CWD."""

    def test_relative_redirect_resolved_against_cwd(self, monkeypatch: pytest.MonkeyPatch):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _extract_bash_write_targets("echo foo > output.txt")
        assert result is not None
        assert "/workspace/output.txt" in result

    def test_relative_redirect_within_prefix_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp/")
        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _run_hook(_build_bash_event("echo foo > .autoskillit/temp/out.txt"))
        assert result == ""

    def test_relative_redirect_outside_prefix_denied(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp/")
        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _run_hook(_build_bash_event("echo foo > src/main.py"))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_no_cwd_relative_redirect_fails_open(self, monkeypatch: pytest.MonkeyPatch):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.delenv("AUTOSKILLIT_CWD", raising=False)
        result = _extract_bash_write_targets("echo foo > output.txt")
        assert result is None


class TestGhCommandRedirectChecking:
    """gh commands with redirects must have their redirect targets checked."""

    def test_gh_with_absolute_redirect_outside_prefix_denied(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp/")
        result = _run_hook(_build_bash_event("gh pr diff 123 > /outside/file.txt"))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_gh_with_redirect_to_dev_null_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp/")
        result = _run_hook(_build_bash_event("gh pr diff 123 > /dev/null"))
        assert result == ""

    def test_gh_with_relative_redirect_resolved(self, monkeypatch: pytest.MonkeyPatch):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _extract_bash_write_targets("gh pr diff 123 > output.txt")
        assert result is not None
        assert "/workspace/output.txt" in result

    def test_gh_with_redirect_within_prefix_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp/")
        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _run_hook(_build_bash_event("gh pr diff 123 > .autoskillit/temp/diff.txt"))
        assert result == ""


class TestWriteGuardFdRedirectImmunity:
    """Cross-product: every fd-redirect form x every CWD state must not produce false targets."""

    @pytest.mark.parametrize(
        "redirect_form",
        [
            "cmd 2>&1",
            "cmd > /dev/null 2>&1",
            "cmd >&2",
            "cmd 1>&2",
            "cmd 2>>&1",
            "cmd >&-",
            "cmd 3>&1",
        ],
    )
    @pytest.mark.parametrize(
        "cwd",
        ["/workspace", ""],
        ids=["cwd_set", "cwd_unset"],
    )
    def test_fd_redirect_never_produces_write_target(
        self, redirect_form, cwd, monkeypatch: pytest.MonkeyPatch
    ):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        if cwd:
            monkeypatch.setenv("AUTOSKILLIT_CWD", cwd)
        else:
            monkeypatch.delenv("AUTOSKILLIT_CWD", raising=False)
        result = _extract_bash_write_targets(redirect_form)
        assert result is None or result == [], (
            f"fd-redirect '{redirect_form}' with CWD='{cwd}' produced spurious targets: {result}"
        )


class TestWriteGuardVerbFdRedirect:
    """Verb-argument fd-redirect tests with CWD set."""

    def test_tee_with_fd_redirect_not_blocked(self, monkeypatch: pytest.MonkeyPatch):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _extract_bash_write_targets("tee 2>&1")
        assert result == []

    def test_sed_with_only_fd_redirect_not_blocked(self, monkeypatch: pytest.MonkeyPatch):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _extract_bash_write_targets("sed -i 2>&1")
        assert result == []

    def test_sed_with_sub_pattern_and_fd_redirect_treats_pattern_as_target(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _extract_bash_write_targets("sed -i 's/x/y/' 2>&1")
        assert result is not None and len(result) > 0, (
            "sed substitution pattern is indistinguishable from a filename — "
            "conservative guard should treat it as a write target"
        )

    def test_sed_with_real_path_and_fd_redirect_detects_real_path(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _extract_bash_write_targets("sed -i 's/x/y/' /outside/file.py 2>&1")
        assert result is not None
        assert "/outside/file.py" in result

    def test_mv_with_real_path_and_fd_redirect_detects_real_path(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        result = _extract_bash_write_targets("mv /src /dst 2>&1")
        assert result is not None
        assert "/dst" in result


class TestWriteGuardCrossProductMatrix:
    """Cross-product test: every write mechanism resolves every path type."""

    @pytest.mark.parametrize(
        "cmd_template",
        [
            "echo x > {path}",
            "echo x >> {path}",
            "sed -i 's/x/y/' {path}",
            "rm {path}",
            "tee {path}",
            "mv /src {path}",
            "cp /src {path}",
            "echo x > {path} 2>&1",
            "sed -i 's/x/y/' {path} 2>&1",
            "tee {path} 2>&1",
        ],
    )
    @pytest.mark.parametrize(
        "path_type,expected_resolved",
        [
            ("/workspace/src/main.py", "/workspace/src/main.py"),
            ("src/main.py", "/workspace/src/main.py"),
            ("./src/main.py", "/workspace/./src/main.py"),
            ("$MY_VAR/src/main.py", None),
        ],
    )
    def test_all_write_mechanisms_resolve_all_path_types(
        self, cmd_template, path_type, expected_resolved, monkeypatch: pytest.MonkeyPatch
    ):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        monkeypatch.delenv("MY_VAR", raising=False)
        cmd = cmd_template.format(path=path_type)
        result = _extract_bash_write_targets(cmd)
        if expected_resolved is None:
            assert result is None or result == [], f"Expected fail-open for: {cmd}, got: {result}"
        else:
            assert result is not None, f"Expected write detection for: {cmd}"
            assert expected_resolved in result, (
                f"Expected {expected_resolved} in {result} for: {cmd}"
            )


class TestShellVariableWriteGuardIntegration:
    def test_redirect_with_env_var_in_environ_allowed(self, monkeypatch):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp")
        monkeypatch.setenv("MY_DIR", "/workspace/.autoskillit/temp/review-pr")
        result = _extract_bash_write_targets('echo x > "$MY_DIR/out.txt"')
        assert result is not None
        assert "/workspace/.autoskillit/temp/review-pr/out.txt" in result

    def test_redirect_with_unknown_var_failopen(self, monkeypatch):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp")
        monkeypatch.delenv("UNKNOWN_DIR", raising=False)
        result = _extract_bash_write_targets('echo x > "$UNKNOWN_DIR/out.txt"')
        assert result is None or result == []

    def test_redirect_with_inline_assignment_failopen(self, monkeypatch):
        from autoskillit.hooks.guards.write_guard import _extract_bash_write_targets

        monkeypatch.setenv("AUTOSKILLIT_CWD", "/workspace")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/workspace/.autoskillit/temp")
        monkeypatch.delenv("REVIEW_OUTPUT_DIR", raising=False)
        cmd = (
            'REVIEW_OUTPUT_DIR=".autoskillit/temp/review-pr/iter_0"'
            ' && echo x > "$REVIEW_OUTPUT_DIR/out.txt"'
        )
        result = _extract_bash_write_targets(cmd)
        assert result is None or result == []


try:
    from autoskillit.hooks.guards.write_guard import _extract_paths_from_patch as _probe_fn

    _CODEX_FORMAT_SUPPORTED = bool(_probe_fn("*** Update File: /tmp/probe.py\n"))
except ImportError:
    _CODEX_FORMAT_SUPPORTED = False


class TestExtractPathsFromPatch:
    def test_empty_string_returns_empty_list(self):
        from autoskillit.hooks.guards.write_guard import _extract_paths_from_patch

        assert _extract_paths_from_patch("") == []

    def test_single_plus_plus_plus_b_line_returns_path(self):
        from autoskillit.hooks.guards.write_guard import _extract_paths_from_patch

        patch = "--- a/old.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new"
        assert _extract_paths_from_patch(patch) == ["foo.py"]

    def test_multi_file_patch_returns_all_paths_in_order(self):
        from autoskillit.hooks.guards.write_guard import _extract_paths_from_patch

        patch = (
            "--- a/alpha.py\n+++ b/alpha.py\n@@ -1 +1 @@\n-old\n+new\n"
            "--- a/beta.py\n+++ b/beta.py\n@@ -1 +1 @@\n-old\n+new"
        )
        assert _extract_paths_from_patch(patch) == ["alpha.py", "beta.py"]

    def test_non_plus_plus_plus_b_lines_are_excluded(self):
        from autoskillit.hooks.guards.write_guard import _extract_paths_from_patch

        patch = "--- a/foo.py\n@@ -1 +1 @@\n context line\n"
        assert _extract_paths_from_patch(patch) == []

    def test_subdirectory_path_is_extracted_correctly(self):
        from autoskillit.hooks.guards.write_guard import _extract_paths_from_patch

        assert _extract_paths_from_patch("+++ b/src/foo.py") == ["src/foo.py"]


class TestWriteGuardCodexPatchFormatXfail:
    """Xfail-guarded integration tests for Codex patch format via _run_hook."""

    @pytest.fixture(autouse=True)
    def _enable_headless(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        allowed = str(tmp_path / ".autoskillit" / "temp")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", allowed)
        self._allowed = allowed

    @pytest.mark.xfail(
        not _CODEX_FORMAT_SUPPORTED,
        reason="P2 Codex patch format support not yet merged",
        strict=False,
    )
    def test_codex_patch_within_prefix_allowed(self):
        patch = f"*** Update File: {self._allowed}/plan.md\n"
        result = _run_hook(_build_apply_patch_event(patch))
        assert result == ""

    @pytest.mark.xfail(
        not _CODEX_FORMAT_SUPPORTED,
        reason="P2 Codex patch format support not yet merged",
        strict=False,
    )
    def test_codex_patch_outside_prefix_denied(self):
        patch = "*** Update File: /outside/bar.py\n"
        result = _run_hook(_build_apply_patch_event(patch))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.xfail(
        not _CODEX_FORMAT_SUPPORTED,
        reason="P2 Codex patch format support not yet merged",
        strict=False,
    )
    def test_codex_mixed_format_patch_no_crash(self):
        patch = f"+++ b/{self._allowed}/a.py\n*** Update File: {self._allowed}/b.py\n"
        result = _run_hook(_build_apply_patch_event(patch))
        assert isinstance(result, str)

    @pytest.mark.xfail(
        not _CODEX_FORMAT_SUPPORTED,
        reason="P2 Codex patch format support not yet merged",
        strict=False,
    )
    def test_codex_empty_patch_triggers_no_paths_deny(self):
        patch = "*** Begin Patch\n*** End Patch\n"
        result = _run_hook(_build_apply_patch_event(patch))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert (
            "no target paths found in patch"
            in parsed["hookSpecificOutput"]["permissionDecisionReason"]
        )
