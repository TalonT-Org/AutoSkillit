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
