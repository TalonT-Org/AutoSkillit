"""Tests for the shared command classification primitive (hooks/_command_classification.py)."""

from __future__ import annotations

import pytest

from autoskillit.hooks._command_classification import (
    command_verb,
    extract_interpreter_write_paths,
    extract_redirect_targets,
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

    def test_gh_after_shell_op(self):
        segments = tokenize_command_segments("git status && gh pr view 123")
        gh_segments = [seg for seg in segments if is_gh_command(seg)]
        assert len(gh_segments) == 1
        assert gh_segments[0][0] == "gh"


class TestExtractInterpreterWritePaths:
    def test_open_literal_path(self):
        cmd = "python3 -c \"open('/clone/.autoskillit/temp/out.json', 'w').write('x')\""
        assert extract_interpreter_write_paths(cmd) == ["/clone/.autoskillit/temp/out.json"]

    def test_path_write_text(self):
        cmd = "python3 -c \"Path('/clone/temp/out.json').write_text('x')\""
        assert extract_interpreter_write_paths(cmd) == ["/clone/temp/out.json"]

    def test_path_write_bytes(self):
        cmd = "python3 -c \"Path('/clone/temp/out.bin').write_bytes(b'x')\""
        assert extract_interpreter_write_paths(cmd) == ["/clone/temp/out.bin"]

    def test_dynamic_path_returns_empty_list(self):
        cmd = "python3 -c \"open(sys.argv[1], 'w').write('x')\""
        assert extract_interpreter_write_paths(cmd) == []

    def test_no_interpreter_returns_none(self):
        cmd = "open('/tmp/x', 'w')"
        assert extract_interpreter_write_paths(cmd) is None

    def test_shutil_returns_empty_list(self):
        cmd = "python3 -c \"import shutil; shutil.copy('/tmp/a', '/clone/src/f.py')\""
        assert extract_interpreter_write_paths(cmd) == []

    def test_dynamic_path_with_non_literal_var_returns_empty_list(self):
        cmd = "python3 -c \"open(some_var, 'w').write('x')\""
        assert extract_interpreter_write_paths(cmd) == []


class TestExtractInterpreterWritePathsRelative:
    def test_relative_open_path(self):
        cmd = "python3 -c \"open('.autoskillit/temp/foo.txt', 'w').write('x')\""
        assert extract_interpreter_write_paths(cmd) == [".autoskillit/temp/foo.txt"]

    def test_relative_path_constructor(self):
        cmd = "python3 -c \"Path('temp/out.json').write_text('x')\""
        assert extract_interpreter_write_paths(cmd) == ["temp/out.json"]

    def test_dotslash_relative_path(self):
        cmd = "python3 -c \"open('./output.txt', 'w').write('x')\""
        assert extract_interpreter_write_paths(cmd) == ["./output.txt"]


class TestExtractInterpreterWritePathsMulti:
    def test_multiple_open_calls(self):
        cmd = (
            'python3 -c "'
            "open('/clone/.autoskillit/temp/a.txt', 'w').write('x'); "
            "open('/clone/.autoskillit/temp/b.txt', 'w').write('y'); "
            "open('/clone/.autoskillit/temp/c.txt', 'w').write('z')"
            '"'
        )
        result = extract_interpreter_write_paths(cmd)
        assert result is not None
        assert len(result) == 3
        assert "/clone/.autoskillit/temp/a.txt" in result
        assert "/clone/.autoskillit/temp/b.txt" in result
        assert "/clone/.autoskillit/temp/c.txt" in result

    def test_mixed_open_and_path_constructor(self):
        cmd = (
            'python3 -c "'
            "open('/clone/a.txt', 'w').write('x'); "
            "Path('/clone/b.txt').write_text('y')"
            '"'
        )
        result = extract_interpreter_write_paths(cmd)
        assert result is not None
        assert len(result) == 2
        assert "/clone/a.txt" in result
        assert "/clone/b.txt" in result

    def test_partial_dynamic_denies_all(self):
        cmd = "python3 -c \"open('/clone/a.txt', 'w').write('x'); open(var, 'w').write('y')\""
        assert extract_interpreter_write_paths(cmd) == []

    def test_chained_write_text_on_open_not_double_counted(self):
        cmd = "python3 -c \"open('/clone/temp/out.txt', 'w').write_text('data')\""
        assert extract_interpreter_write_paths(cmd) == ["/clone/temp/out.txt"]

    def test_chained_write_bytes_on_open_not_double_counted(self):
        cmd = "python3 -c \"open('/clone/temp/out.bin', 'wb').write_bytes(b'data')\""
        assert extract_interpreter_write_paths(cmd) == ["/clone/temp/out.bin"]


class TestExtractRedirectTargets:
    @pytest.mark.parametrize(
        "tokens,expected",
        [
            (["echo", "data", ">", "/tmp/out.txt"], ["/tmp/out.txt"]),
            (["cmd", "2>/dev/null"], ["/dev/null"]),
            (["2>/dev/null)"], []),
            (["cmd", "2>/dev/null`"], ["/dev/null"]),
            (["cmd", "2>/dev/null;"], ["/dev/null"]),
            (["cmd", ">>", "/tmp/log"], ["/tmp/log"]),
            (["cmd", ">", "relative.txt"], []),
            (["echo", "hello"], []),
            (["cmd", ">", "/tmp/a", "2>/tmp/b"], ["/tmp/a", "/tmp/b"]),
            (["cmd", "2>", "/tmp/err.log"], ["/tmp/err.log"]),
            (["x=$(cmd", "2>/tmp/err.log)"], []),
            (
                ["x=$(cmd", "2>/tmp/err.log)", "&&", "echo", "done", ">", "/tmp/out.txt"],
                ["/tmp/out.txt"],
            ),
            (["(", "cmd", ">", "/tmp/err.log", ")"], []),
            (["(cmd", ">", "/tmp/err.log", ")"], []),
            (["x=$(cmd", ">/tmp/err.log)"], []),
            (["cmd", ">", "/dev/null"], ["/dev/null"]),
            (["cmd", "2>/dev/null&"], ["/dev/null"]),
            (["cmd", "2>/tmp/out|"], ["/tmp/out"]),
            (["cmd", "2>&1"], []),
            (["cmd", ">", "&1"], []),
            (["cmd", "2>&1", ">", "/tmp/out"], ["/tmp/out"]),
        ],
        ids=[
            "separate_redirect",
            "merged_fd_redirect",
            "merged_with_trailing_paren_skipped",
            "merged_trailing_backtick",
            "merged_trailing_semicolon",
            "append_redirect",
            "non_absolute_path",
            "no_redirects",
            "multiple_redirects",
            "split_redirect",
            "subshell_fused_skipped",
            "subshell_with_top_level_redirect",
            "standalone_paren_nesting",
            "fused_paren_nesting",
            "only_subshell_redirect_fused",
            "pseudo_device_returned",
            "trailing_ampersand_stripped",
            "trailing_pipe_stripped",
            "fd_dup_2_to_1_no_cwd",
            "fd_dup_split_ampersand_no_cwd",
            "fd_dup_with_real_redirect",
        ],
    )
    def test_extract_redirect_targets(self, tokens, expected):
        assert extract_redirect_targets(tokens) == expected


class TestResolveWriteTarget:
    @pytest.mark.parametrize(
        "path,cwd,expected",
        [
            ("/tmp/out.txt", "/workspace", "/tmp/out.txt"),
            ("output.txt", "/workspace", "/workspace/output.txt"),
            ("output.txt", "", None),
            ("./output.txt", "/workspace", "/workspace/./output.txt"),
            ("/tmp/out.txt", "", "/tmp/out.txt"),
            ("", "/workspace", None),
            ("", "", None),
            ("&1", "/workspace", None),
            ("&2", "/workspace", None),
            ("&-", "/workspace", None),
            ("&1", "", None),
            ("2>&1", "/workspace", None),
            (">&2", "/workspace", None),
            ("1>&2", "/workspace", None),
            ("2>&-", "/workspace", None),
        ],
        ids=[
            "absolute_with_cwd",
            "relative_with_cwd",
            "relative_no_cwd",
            "dotslash_relative_with_cwd",
            "absolute_no_cwd",
            "empty_path_with_cwd",
            "empty_path_no_cwd",
            "fd_ampersand_1_with_cwd",
            "fd_ampersand_2_with_cwd",
            "fd_close_with_cwd",
            "fd_ampersand_1_no_cwd",
            "fd_fused_2_to_1_with_cwd",
            "fd_fused_stdout_to_stderr_with_cwd",
            "fd_fused_1_to_2_with_cwd",
            "fd_fused_2_close_with_cwd",
        ],
    )
    def test_resolve_write_target(self, path, cwd, expected):
        from autoskillit.hooks._command_classification import resolve_write_target

        assert resolve_write_target(path, cwd) == expected

    @pytest.mark.parametrize(
        "env_setup, path, cwd, expected",
        [
            (
                {"TEST_EXPAND_DIR": "/resolved/dir"},
                "$TEST_EXPAND_DIR/output.txt",
                "/workspace",
                "/resolved/dir/output.txt",
            ),
            ({}, "$REVIEW_OUTPUT_DIR/file.json", "/workspace", None),
            ({}, "${REVIEW_OUTPUT_DIR}/file.json", "/workspace", None),
            ({}, "$NONEXISTENT_VAR/path", "/workspace", None),
            (
                {"TEST_EXPAND_DIR": "/resolved/dir"},
                "$TEST_EXPAND_DIR/$NONEXISTENT/file",
                "/workspace",
                None,
            ),
            ({}, "report$2026.txt", "/workspace", "/workspace/report$2026.txt"),
        ],
    )
    def test_resolve_write_target_shell_vars(self, env_setup, path, cwd, expected, monkeypatch):
        from autoskillit.hooks._command_classification import resolve_write_target

        for var in ["TEST_EXPAND_DIR", "REVIEW_OUTPUT_DIR", "NONEXISTENT_VAR", "NONEXISTENT"]:
            monkeypatch.delenv(var, raising=False)
        for var, val in env_setup.items():
            monkeypatch.setenv(var, val)
        assert resolve_write_target(path, cwd) == expected


class TestExtractRedirectTargetsCwd:
    @pytest.mark.parametrize(
        "tokens,cwd,expected",
        [
            (["cmd", ">", "output.txt"], "/workspace", ["/workspace/output.txt"]),
            (
                ["cmd", ">>", ".autoskillit/temp/out.txt"],
                "/workspace",
                ["/workspace/.autoskillit/temp/out.txt"],
            ),
            (["cmd", "2>", "err.log"], "/workspace", ["/workspace/err.log"]),
            (["cmd", ">", "output.txt"], "", []),
            (["cmd", ">", "/abs/path"], "/workspace", ["/abs/path"]),
            (["cmd", "2>output.txt"], "/workspace", ["/workspace/output.txt"]),
            (["cmd", ">", "/dev/null", "2>&1"], "/workspace", ["/dev/null"]),
            (["cmd", "2>&1"], "/workspace", []),
            (["cmd", ">&2"], "/workspace", []),
            (["cmd", "1>&2"], "/workspace", []),
            (["cmd", "2>>&1"], "/workspace", []),
            (["cmd", ">&-"], "/workspace", []),
            (["cmd", "3>&1"], "/workspace", []),
            (["cmd", "2>", "&1"], "/workspace", []),
        ],
        ids=[
            "relative_with_cwd",
            "relative_append_with_cwd",
            "relative_fd_with_cwd",
            "relative_no_cwd",
            "absolute_ignores_cwd",
            "merged_relative_with_cwd",
            "combined_devnull_and_fd_dup_with_cwd",
            "fd_dup_2_to_1_with_cwd",
            "fd_dup_stdout_to_stderr_with_cwd",
            "fd_dup_1_to_2_with_cwd",
            "fd_dup_append_with_cwd",
            "fd_close_with_cwd",
            "fd_dup_3_to_1_with_cwd",
            "fd_dup_split_form_with_cwd",
        ],
    )
    def test_extract_redirect_targets_with_cwd(self, tokens, cwd, expected):
        from autoskillit.hooks._command_classification import extract_redirect_targets

        assert extract_redirect_targets(tokens, cwd) == expected
