"""Tests for the shared command classification primitive (hooks/_command_classification.py)."""

from __future__ import annotations

import pytest

from autoskillit.hooks._command_classification import (
    command_verb,
    extract_interpreter_write_path,
    has_interpreter_wrapped_command,
    has_interpreter_write,
    has_nested_shell,
    is_gh_command,
    tokenize_command_segments,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def test_detects_python3_subprocess_run():
    cmd = "python3 -c \"import subprocess; subprocess.run('gh pr create', shell=True)\""
    assert has_interpreter_wrapped_command(cmd, target_commands=["gh pr create"])


def test_detects_bash_c_nesting():
    assert has_nested_shell('bash -c "gh pr create --fill"')


def test_no_false_positive_simple_command():
    assert not has_interpreter_wrapped_command(
        "gh pr create --fill",
        target_commands=["gh pr create"],
    )


def test_detects_os_system_wrapping():
    cmd = "python3 -c \"import os; os.system('gh issue list')\""
    assert has_interpreter_wrapped_command(cmd, target_commands=["gh issue list"])


def test_detects_python_write_text():
    cmd = "python3 -c \"Path('/tmp/x').write_text('data')\""
    assert has_interpreter_write(cmd)


def test_detects_python_open_write_mode():
    cmd = "python3 -c \"open('/tmp/x', 'w').write('data')\""
    assert has_interpreter_write(cmd)


def test_detects_python_heredoc_write():
    cmd = "python3 <<'EOF'\nopen('/tmp/x', 'w').write('hi')\nEOF"
    assert has_interpreter_write(cmd)


def test_no_false_positive_read_only_python():
    cmd = "python3 -c \"print(open('/tmp/x').read())\""
    assert not has_interpreter_write(cmd)


def test_no_false_positive_simple_gh_command():
    assert not has_nested_shell("gh pr create --fill")


def test_detects_sh_c_nesting():
    assert has_nested_shell('sh -c "gh issue list"')


def test_no_match_when_no_interpreter():
    assert not has_interpreter_wrapped_command(
        "git push origin main",
        target_commands=["git push"],
    )


def test_detects_python_os_popen():
    cmd = "python3 -c \"import os; os.popen('gh pr create')\""
    assert has_interpreter_wrapped_command(cmd, target_commands=["gh pr create"])


def test_interpreter_wrapped_command_case_insensitive():
    cmd = "python3 -c \"import os; os.system('GH PR CREATE')\""
    assert has_interpreter_wrapped_command(cmd, target_commands=["gh pr create"])


class TestTokenizeCommandSegments:
    def test_single_command(self):
        assert tokenize_command_segments("git status") == [["git", "status"]]

    def test_chained_commands(self):
        result = tokenize_command_segments("git add . && git commit -m msg")
        assert result == [["git", "add", "."], ["git", "commit", "-m", "msg"]]

    def test_quoted_args_not_split(self):
        result = tokenize_command_segments("echo 'hello world'")
        assert result == [["echo", "hello world"]]

    def test_shell_op_separates_segments(self):
        result = tokenize_command_segments("cmd1 || cmd2 ; cmd3")
        assert len(result) == 3

    def test_unclosed_quotes_returns_empty(self):
        assert tokenize_command_segments("echo 'unclosed") == []

    def test_pipe_separates_segments(self):
        result = tokenize_command_segments("cat file | tee /tmp/out")
        assert result == [["cat", "file"], ["tee", "/tmp/out"]]


class TestCommandVerb:
    def test_simple_verb(self):
        assert command_verb(["git", "status"]) == "git"

    def test_env_prefix_skipped(self):
        assert command_verb(["env", "python3", "-c", "..."]) == "python3"

    def test_env_with_key_val(self):
        assert command_verb(["env", "FOO=bar", "python3", "-c", "x"]) == "python3"

    def test_env_with_flag(self):
        assert command_verb(["env", "-i", "python3", "-c", "x"]) == "python3"

    def test_empty_segment(self):
        assert command_verb([]) == ""


class TestIsGhCommand:
    def test_gh_at_position_0(self):
        assert is_gh_command(["gh", "api", "/repos/foo"])

    def test_not_gh(self):
        assert not is_gh_command(["git", "push"])

    def test_gh_as_argument(self):
        assert not is_gh_command(["echo", "gh"])

    def test_env_gh(self):
        assert is_gh_command(["env", "gh", "pr", "view"])


class TestExtractInterpreterWritePath:
    def test_open_literal_path(self):
        cmd = "python3 -c \"open('/clone/.autoskillit/temp/out.json', 'w').write('x')\""
        assert extract_interpreter_write_path(cmd) == "/clone/.autoskillit/temp/out.json"

    def test_path_write_text(self):
        cmd = "python3 -c \"Path('/clone/temp/out.json').write_text('x')\""
        assert extract_interpreter_write_path(cmd) == "/clone/temp/out.json"

    def test_path_write_bytes(self):
        cmd = "python3 -c \"Path('/clone/temp/out.bin').write_bytes(b'x')\""
        assert extract_interpreter_write_path(cmd) == "/clone/temp/out.bin"

    def test_dynamic_path_returns_none(self):
        cmd = "python3 -c \"open(sys.argv[1], 'w').write('x')\""
        assert extract_interpreter_write_path(cmd) is None

    def test_no_interpreter_returns_none(self):
        cmd = "open('/tmp/x', 'w')"
        assert extract_interpreter_write_path(cmd) is None

    def test_shutil_returns_none(self):
        cmd = "python3 -c \"import shutil; shutil.copy('/tmp/a', '/clone/src/f.py')\""
        assert extract_interpreter_write_path(cmd) is None

    def test_has_interpreter_write_returns_path_when_literal(self):
        cmd = "python3 -c \"open('/some/literal/path.txt', 'w').write('x')\""
        result = extract_interpreter_write_path(cmd)
        assert result == "/some/literal/path.txt"

    def test_has_interpreter_write_returns_none_for_dynamic_path(self):
        cmd = "python3 -c \"open(some_var, 'w').write('x')\""
        assert extract_interpreter_write_path(cmd) is None
