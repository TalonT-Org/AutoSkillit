"""Tests for the shared command classification primitive (hooks/_command_classification.py)."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

import autoskillit.hooks._command_classification as command_classification
from autoskillit.hooks._command_classification import (
    GitHubMutationAnalysis,
    GitHubMutationKind,
    GitHubMutationRecord,
    GitHubMutationStatus,
    analyze_github_mutations,
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


def test_shell_control_words_includes_closing_keywords():
    """_SHELL_CONTROL_WORDS must include the closing keywords added by this task.

    Pinning the shared primitive directly so boundary expansion is not only
    covered indirectly through compose_pr_body_guard integration tests.
    """
    from autoskillit.hooks._command_classification import _SHELL_CONTROL_WORDS  # noqa: PLC0415

    for word in ("esac", "fi", "done"):
        assert word in _SHELL_CONTROL_WORDS, f"_SHELL_CONTROL_WORDS is missing '{word}'"


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

    def test_adjacent_ampersand_ampersand(self):
        result = tokenize_command_segments("echo ok&&pip install -e .")
        assert len(result) == 2
        assert result[1] == ["pip", "install", "-e", "."]

    def test_adjacent_semicolon(self):
        result = tokenize_command_segments("echo ok;pip install -e .")
        assert len(result) == 2
        assert result[1] == ["pip", "install", "-e", "."]

    def test_adjacent_pipe(self):
        result = tokenize_command_segments("echo ok|pip install -e .")
        assert len(result) == 2

    def test_adjacent_double_pipe(self):
        result = tokenize_command_segments("echo ok||pip install -e .")
        assert len(result) == 2

    def test_adjacent_background_ampersand(self):
        result = tokenize_command_segments("echo ok&pip install -e .")
        assert len(result) == 2

    @pytest.mark.parametrize("redirect", ["2>&1", "1>&2", ">&1"])
    def test_fd_duplication_does_not_create_a_command_boundary(self, redirect: str) -> None:
        assert tokenize_command_segments(f"gh issue edit 23 --title x {redirect}") == [
            ["gh", "issue", "edit", "23", "--title", "x", redirect]
        ]

    def test_quoted_redirect_shape_remains_literal_argv(self) -> None:
        segments = command_classification._tokenize_command_segments_with_redirects(
            "printf '%s' '2>/tmp/out'"
        )

        assert segments[0].tokens == ["printf", "%s", "2>/tmp/out"]
        assert segments[0].redirect_syntax == [False, False, False]

    @pytest.mark.parametrize(
        ("command", "expected_tokens", "expected_targets", "expected_count"),
        [
            ("cmd > /tmp/out", ["cmd"], ["/tmp/out"], 1),
            ("cmd 2>/tmp/err", ["cmd"], ["/tmp/err"], 1),
            ("cmd >$OUT", ["cmd"], [], 1),
            ("cmd >", ["cmd"], [], 1),
            ("cmd 2>&1", ["cmd"], [], 0),
            ("curl --output /tmp/out URL", ["curl", "--output", "/tmp/out", "URL"], [], 0),
        ],
        ids=["separate", "merged", "dynamic", "missing", "fd-dup", "curl-option"],
    )
    def test_output_control_partition(
        self,
        command: str,
        expected_tokens: list[str],
        expected_targets: list[str],
        expected_count: int,
    ) -> None:
        segment = command_classification._tokenize_command_segments_with_redirects(command)[0]

        assert command_classification._partition_output_redirects(
            segment.tokens,
            cwd="/work",
            redirect_syntax=segment.redirect_syntax,
        ) == (expected_tokens, expected_targets, expected_count)

    def test_bare_newline_separates_segments(self):
        result = tokenize_command_segments("echo ok\npip install -e .")
        assert len(result) == 2
        assert result[1] == ["pip", "install", "-e", "."]

    def test_quoted_operator_remains_argument(self):
        result = tokenize_command_segments("echo 'pip && install -e .'")
        assert result == [["echo", "pip && install -e ."]]

    def test_double_quoted_operator_remains_argument(self):
        result = tokenize_command_segments('echo "a || b"')
        assert result == [["echo", "a || b"]]

    def test_heredoc_body_stripped_before_tokenize(self):
        result = tokenize_command_segments("cat <<'EOF'\nbody content with > symbols\nEOF")
        assert result == [["cat"]]

    def test_op_only_run_is_boundary(self):
        result = tokenize_command_segments("pip install -e . ; && uv pip install -e .")
        assert len(result) == 2
        assert result[1] == ["uv", "pip", "install", "-e", "."]

    def test_parenthesized_subshell(self):
        result = tokenize_command_segments("(echo hi; echo bye)")
        assert len(result) == 2


class TestCommandVerbAndArgs:
    def test_returns_verb_and_args(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_strips_env_assignments(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["env", "FOO=bar", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_strips_leading_posix_assignment(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["FOO=bar", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_env_with_value_taking_flag(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(
            ["env", "-u", "FOO", "--chdir", "/tmp", "-S", "x", "pip", "install", "-e", "."]
        )
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_env_split_string_consumes_value(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(
            ["env", "--split-string", "FOOBAR", "pip", "install", "-e", "."]
        )
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_env_attached_value_flag(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(
            ["env", "--chdir=/tmp", "--unset=FOO", "pip", "install", "-e", "."]
        )
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_env_double_dash_terminator(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["env", "--", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_sudo_wrapper(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["sudo", "-u", "root", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_nice_wrapper(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["nice", "-n", "5", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_timeout_wrapper_mandatory_duration(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["timeout", "30", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_stdbuf_wrapper_short_flag(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["stdbuf", "-o0", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_command_wrapper(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["command", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_nohup_wrapper(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["nohup", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_double_dash_terminator(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["pip", "--", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["--", "install", "-e", "."]

    def test_empty_segment_returns_empty(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args([])
        assert verb == ""
        assert args == []

    def test_wrapper_only_returns_empty(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["env"])
        assert verb == ""
        assert args == []

    def test_timeout_missing_duration_returns_empty(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["timeout"])
        assert verb == ""
        assert args == []

    def test_bare_env_wrapper(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["env"])
        assert verb == ""
        assert args == []

    def test_command_verb_delegates_to_command_verb_and_args(self):
        from autoskillit.hooks._command_classification import command_verb_and_args

        seg = ["env", "FOO=bar", "pip", "install", "-e", "."]
        verb_from_helper, _ = command_verb_and_args(seg)
        assert verb_from_helper == command_verb(seg)


class TestExtractShellCommandPayloads:
    def test_bash_c_payload(self):
        from autoskillit.hooks._command_classification import extract_shell_command_payloads

        assert extract_shell_command_payloads('bash -c "pip install -e ."') == ["pip install -e ."]

    def test_sh_c_payload(self):
        from autoskillit.hooks._command_classification import extract_shell_command_payloads

        assert extract_shell_command_payloads('sh -c "pip install -e ."') == ["pip install -e ."]

    def test_zsh_c_payload(self):
        from autoskillit.hooks._command_classification import extract_shell_command_payloads

        assert extract_shell_command_payloads('zsh -c "pip install -e ."') == ["pip install -e ."]

    def test_dash_c_payload(self):
        from autoskillit.hooks._command_classification import extract_shell_command_payloads

        assert extract_shell_command_payloads('dash -c "pip install -e ."') == ["pip install -e ."]

    def test_eval_payload(self):
        from autoskillit.hooks._command_classification import extract_shell_command_payloads

        assert extract_shell_command_payloads('eval "pip install -e ."') == ["pip install -e ."]

    def test_dollar_paren_payload(self):
        from autoskillit.hooks._command_classification import extract_shell_command_payloads

        payloads = extract_shell_command_payloads("echo $(pip install -e .)")
        assert payloads == ["pip install -e ."]

    def test_backtick_payload(self):
        from autoskillit.hooks._command_classification import extract_shell_command_payloads

        payloads = extract_shell_command_payloads("echo `pip install -e .`")
        assert payloads == ["pip install -e ."]

    def test_double_quoted_substitution_payload(self):
        from autoskillit.hooks._command_classification import extract_shell_command_payloads

        payloads = extract_shell_command_payloads('echo "$(pip install -e .)"')
        assert payloads == ["pip install -e ."]

    def test_single_quoted_substitution_inert(self):
        from autoskillit.hooks._command_classification import extract_shell_command_payloads

        assert extract_shell_command_payloads("echo '$(pip install -e .)'") == []

    def test_escaped_substitution_inert(self):
        from autoskillit.hooks._command_classification import extract_shell_command_payloads

        assert extract_shell_command_payloads('echo "\\$(pip install -e .)"') == []

    def test_nested_substitution(self):
        from autoskillit.hooks._command_classification import extract_shell_command_payloads

        payloads = extract_shell_command_payloads('bash -c "echo $(pip install -e .)"')
        assert "pip install -e ." in payloads

    def test_no_payloads_returns_empty_list(self):
        from autoskillit.hooks._command_classification import extract_shell_command_payloads

        assert extract_shell_command_payloads("echo hi") == []

    def test_absolute_bash_path_normalized(self):
        from autoskillit.hooks._command_classification import extract_shell_command_payloads

        assert extract_shell_command_payloads('/bin/bash -c "pip install -e ."') == [
            "pip install -e ."
        ]


class TestTokenizeShellPayloadSegments:
    def test_bash_c_direct_payload(self):
        from autoskillit.hooks._command_classification import tokenize_shell_payload_segments

        result = tokenize_shell_payload_segments('bash -c "gh pr create --fill"')
        assert result == [["gh", "pr", "create", "--fill"]]

    def test_absolute_bash_path(self):
        from autoskillit.hooks._command_classification import tokenize_shell_payload_segments

        result = tokenize_shell_payload_segments('/bin/bash -c "gh pr create --fill"')
        assert result == [["gh", "pr", "create", "--fill"]]

    def test_env_prefix_wrapper(self):
        from autoskillit.hooks._command_classification import tokenize_shell_payload_segments

        result = tokenize_shell_payload_segments('env FOO=1 bash -c "gh pr create --fill"')
        assert result == [["gh", "pr", "create", "--fill"]]

    def test_sudo_wrapper(self):
        from autoskillit.hooks._command_classification import tokenize_shell_payload_segments

        result = tokenize_shell_payload_segments('sudo /bin/bash -c "gh pr create --fill"')
        assert result == [["gh", "pr", "create", "--fill"]]

    def test_nested_bash_c_payload(self):
        from autoskillit.hooks._command_classification import tokenize_shell_payload_segments

        result = tokenize_shell_payload_segments("""bash -c 'bash -c "gh pr create --fill"'""")
        assert ["gh", "pr", "create", "--fill"] in result

    def test_operator_separated_commands_inside_payload(self):
        from autoskillit.hooks._command_classification import tokenize_shell_payload_segments

        result = tokenize_shell_payload_segments('bash -c "echo ready && gh pr create --fill"')
        assert ["echo", "ready"] in result
        assert ["gh", "pr", "create", "--fill"] in result

    def test_dedupes_repeated_payload_strings(self):
        from autoskillit.hooks._command_classification import tokenize_shell_payload_segments

        result = tokenize_shell_payload_segments(
            """bash -c 'gh pr create' && bash -c "gh pr create --fill\""""
        )
        flat = [tuple(s) for s in result]
        assert flat.count(("gh", "pr", "create")) == 1

    def test_malformed_inner_payload_returns_none(self):
        from autoskillit.hooks._command_classification import tokenize_shell_payload_segments

        result = tokenize_shell_payload_segments("bash -c \"echo 'unclosed")
        assert result is None

    def test_quoted_close_paren_does_not_truncate_substitution(self):
        from autoskillit.hooks._command_classification import tokenize_shell_payload_segments

        result = tokenize_shell_payload_segments('echo $(echo "a) b" && gh pr create --fill)')
        assert result is not None
        assert ["gh", "pr", "create", "--fill"] in result

    def test_no_evaluated_shell_payload_returns_empty_list(self):
        from autoskillit.hooks._command_classification import tokenize_shell_payload_segments

        assert tokenize_shell_payload_segments("gh pr create --fill") == []


class TestExtractInterpreterCommandPayloads:
    def test_shell_string_pip_install(self):
        from autoskillit.hooks._command_classification import (
            extract_interpreter_command_payloads,
        )

        payloads, has_unresolved = extract_interpreter_command_payloads(
            "python -c \"import subprocess; subprocess.run('pip install -e .', shell=True)\""
        )
        assert has_unresolved is False
        assert payloads == ["pip install -e ."]

    def test_argv_list_pip(self):
        from autoskillit.hooks._command_classification import (
            extract_interpreter_command_payloads,
        )

        payloads, has_unresolved = extract_interpreter_command_payloads(
            "python3 -c \"import subprocess; subprocess.run(['pip','install','-e','.'])\""
        )
        assert has_unresolved is False
        assert payloads == [["pip", "install", "-e", "."]]

    def test_argv_tuple_pip(self):
        from autoskillit.hooks._command_classification import (
            extract_interpreter_command_payloads,
        )

        payloads, has_unresolved = extract_interpreter_command_payloads(
            "python3 -c \"import subprocess; subprocess.run(('pip','install','--editable','.'))\""
        )
        assert has_unresolved is False
        assert payloads == [["pip", "install", "--editable", "."]]

    def test_argv_list_rg_reader(self):
        from autoskillit.hooks._command_classification import (
            extract_interpreter_command_payloads,
        )

        payloads, has_unresolved = extract_interpreter_command_payloads(
            "python3 -c \"import subprocess; subprocess.run(['rg', 'pip install -e', 'docs/'])\""
        )
        assert has_unresolved is False
        assert payloads == [["rg", "pip install -e", "docs/"]]

    def test_unresolved_payload_returns_flag(self):
        from autoskillit.hooks._command_classification import (
            extract_interpreter_command_payloads,
        )

        cmd = "python3 -c \"import subprocess; subprocess.run(['pip', cmd, '-e', '.'])\""
        _payloads, has_unresolved = extract_interpreter_command_payloads(cmd)
        assert has_unresolved is True

    def test_no_subprocess_returns_empty(self):
        from autoskillit.hooks._command_classification import (
            extract_interpreter_command_payloads,
        )

        payloads, has_unresolved = extract_interpreter_command_payloads(
            "python3 -c \"print('hello')\""
        )
        assert payloads == []
        assert has_unresolved is False

    def test_non_python_returns_empty(self):
        from autoskillit.hooks._command_classification import (
            extract_interpreter_command_payloads,
        )

        payloads, has_unresolved = extract_interpreter_command_payloads("echo pip install -e .")
        assert payloads == []
        assert has_unresolved is False


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


@pytest.mark.parametrize(
    "command,expected_stripped",
    [
        (
            "python3 - <<'EOF'\nif x > 3:\n    pass\nEOF",
            "python3 - <<'EOF'\nEOF",
        ),
        (
            "cat <<EOF\nbody > content\nEOF",
            "cat <<EOF\nEOF",
        ),
        (
            "cat <<-DELIM\n\tbody\nDELIM",
            "cat <<-DELIM\nDELIM",
        ),
        (
            "cat <<-DELIM\n\tbody\n\tDELIM",
            "cat <<-DELIM\nDELIM",
        ),
        (
            "cat <<'EOF' > /real/file.txt\nbody content\nEOF",
            "cat <<'EOF' > /real/file.txt\nEOF",
        ),
        (
            "echo hello > /dev/null",
            "echo hello > /dev/null",
        ),
        (
            "cat <<'A'\nbody1\nA\ncat <<'B'\nbody2\nB",
            "cat <<'A'\nA\ncat <<'B'\nB",
        ),
    ],
)
def test_strip_heredoc_bodies(command: str, expected_stripped: str) -> None:
    from autoskillit.hooks._command_classification import strip_heredoc_bodies

    assert strip_heredoc_bodies(command) == expected_stripped


class TestAnalyzeGitHubMutations:
    def test_read_only_command_has_exact_empty_analysis(self) -> None:
        assert analyze_github_mutations("gh api /repos/o/r/pulls/7/reviews") == (
            GitHubMutationAnalysis(
                status=GitHubMutationStatus.NONE,
                mutations=(),
                request_count=0,
                review_comment_count=None,
                reason_code="",
                reason="",
            )
        )

    def test_simple_rest_review_has_exact_record(self) -> None:
        analysis = analyze_github_mutations(
            "gh api --method POST /repos/o/r/pulls/7/reviews -f event=COMMENT"
        )

        assert analysis == GitHubMutationAnalysis(
            status=GitHubMutationStatus.SINGLE_RESOLVED,
            mutations=(
                GitHubMutationRecord(
                    method="POST",
                    route="/repos/o/r/pulls/7/reviews",
                    kind=GitHubMutationKind.PULL_REVIEW,
                    request_count=1,
                    review_comment_count=None,
                ),
            ),
            request_count=1,
            review_comment_count=None,
            reason_code="",
            reason="",
        )

    def test_simple_non_review_mutation_has_exact_record(self) -> None:
        analysis = analyze_github_mutations(
            "gh api --method PATCH /repos/o/r/issues/7 -f title=updated"
        )

        assert analysis == GitHubMutationAnalysis(
            status=GitHubMutationStatus.SINGLE_RESOLVED,
            mutations=(
                GitHubMutationRecord(
                    method="PATCH",
                    route="/repos/o/r/issues/7",
                    kind=GitHubMutationKind.OTHER,
                    request_count=1,
                    review_comment_count=None,
                ),
            ),
            request_count=1,
            review_comment_count=None,
            reason_code="",
            reason="",
        )

    @pytest.mark.parametrize(
        ("baseline", "redirected"),
        [
            (
                "gh issue edit 4581 --repo TalonT-Org/AutoSkillit --body-file /tmp/body",
                "sleep 1 && gh issue edit 4581 --repo TalonT-Org/AutoSkillit "
                "--body-file /tmp/body 2>&1 | head -c 4000",
            ),
            (
                "gh issue edit 4581 --repo TalonT-Org/AutoSkillit --body-file /tmp/body",
                "gh issue edit 4581 --repo TalonT-Org/AutoSkillit "
                "--body-file /tmp/body > /tmp/out 2>&1",
            ),
            (
                "gh api --method PATCH repos/TalonT-Org/AutoSkillit/issues/4581 -f title=x",
                "gh api --method PATCH repos/TalonT-Org/AutoSkillit/issues/4581 "
                "-f title=x > /tmp/out 2>&1",
            ),
            (
                "curl -X PATCH https://api.github.com/repos/o/r/issues/4581 -d '{}';",
                "curl -X PATCH https://api.github.com/repos/o/r/issues/4581 "
                "-d '{}' > /tmp/out 2>&1",
            ),
        ],
        ids=["filing-pipeline", "filing-file", "gh-api", "curl"],
    )
    def test_output_redirection_does_not_change_mutation_identity(
        self,
        baseline: str,
        redirected: str,
    ) -> None:
        expected = analyze_github_mutations(baseline)
        actual = analyze_github_mutations(redirected)

        assert actual.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert actual.request_count == 1
        assert actual.mutations == expected.mutations

    @pytest.mark.parametrize(
        ("command", "expected_status"),
        [
            ("gh issue edit 23 24 --title x > /tmp/out", GitHubMutationStatus.MULTIPLE),
            (
                "gh api --method PATCH /repos/o/r/issues/23 /repos/o/r/issues/24 > /tmp/out",
                GitHubMutationStatus.UNRESOLVED,
            ),
            (
                "curl -X PATCH https://api.github.com/repos/o/r/issues/23 "
                "https://api.github.com/repos/o/r/issues/24 > /tmp/out",
                GitHubMutationStatus.UNRESOLVED,
            ),
            (
                "gh api --method PATCH /repos/o/r/issues/23 > >(tee /tmp/out)",
                GitHubMutationStatus.UNRESOLVED,
            ),
        ],
        ids=["issue-targets", "api-routes", "curl-urls", "process-substitution"],
    )
    def test_redirect_normalization_preserves_negative_controls(
        self,
        command: str,
        expected_status: GitHubMutationStatus,
    ) -> None:
        assert analyze_github_mutations(command).status is expected_status

    @pytest.mark.parametrize(
        "command,kind",
        [
            (
                "gh api --method POST /repos/o/r/pulls/7/comments -f body=x",
                GitHubMutationKind.PULL_REVIEW_COMMENT,
            ),
            (
                "gh api --method POST /repos/o/r/pulls/7/comments/99/replies -f body=x",
                GitHubMutationKind.PULL_REVIEW_REPLY,
            ),
            (
                "gh pr review 7 --comment --body x",
                GitHubMutationKind.PULL_REVIEW,
            ),
            (
                "/usr/bin/curl -X POST https://api.github.com/repos/o/r/pulls/7/reviews -d '{}'",
                GitHubMutationKind.PULL_REVIEW,
            ),
        ],
        ids=["review-comment", "review-reply", "gh-pr-review", "absolute-curl"],
    )
    def test_review_mutation_kinds_are_closed(
        self,
        command: str,
        kind: GitHubMutationKind,
    ) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.mutations[0].kind is kind
        assert analysis.request_count == 1

    @pytest.mark.parametrize(
        "command",
        [
            (
                "gh api --method PATCH /repos/o/r/issues/7 -f title=x && "
                "gh api --method DELETE /repos/o/r/issues/8"
            ),
            (
                "gh api --method PATCH /repos/o/r/issues/7 -f title=x\n"
                "gh api --method DELETE /repos/o/r/issues/8"
            ),
            ("for n in 1 2; do gh api --method PATCH /repos/o/r/issues/7 -f title=x; done"),
            ("post() { gh api --method PATCH /repos/o/r/issues/7 -f title=x; }; post"),
        ],
        ids=["and-chain", "newlines", "loop", "function"],
    )
    def test_multiple_or_repeatable_mutations_are_not_single(
        self,
        command: str,
    ) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status in {
            GitHubMutationStatus.MULTIPLE,
            GitHubMutationStatus.UNRESOLVED,
        }
        assert analysis.status is not GitHubMutationStatus.SINGLE_RESOLVED

    @pytest.mark.parametrize(
        "command",
        [
            'gh api --method "$METHOD" /repos/o/r/issues/7 -f title=x',
            'gh api --method POST "$ROUTE" -f title=x',
            "curl -X \"$METHOD\" https://api.github.com/repos/o/r/issues/7 -d '{}'",
            "eval 'gh api --method PATCH /repos/o/r/issues/7 -f title=x'",
            ("printf '%s\\n' /repos/o/r/issues/7 | xargs -n1 gh api --method PATCH"),
        ],
        ids=["dynamic-method", "dynamic-route", "curl-dynamic-method", "eval", "xargs"],
    )
    def test_unresolved_mutations_report_reason(self, command: str) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.request_count is None
        assert analysis.reason
        assert analysis.reason_code
        assert len(analysis.reason_code.encode("utf-8")) <= 64
        assert re.fullmatch(r"[a-z][a-z0-9_]*", analysis.reason_code)

    @pytest.mark.parametrize(
        "command",
        [
            ("bash -c 'gh api --method POST /repos/o/r/pulls/7/reviews -f event=COMMENT'"),
            (
                'python3 -c "import subprocess; subprocess.run('
                "['gh','api','--method','POST','/repos/o/r/pulls/7/reviews'])\""
            ),
            (
                'python3 -c "import os; os.system('
                "'curl -X POST https://api.github.com/repos/o/r/pulls/7/reviews')\""
            ),
        ],
        ids=["nested-shell", "python-subprocess", "python-system"],
    )
    def test_literal_wrappers_preserve_review_classification(self, command: str) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.mutations[0].kind is GitHubMutationKind.PULL_REVIEW

    @pytest.mark.parametrize(
        "mutation_name",
        [
            "addPullRequestReview",
            "submitPullRequestReview",
            "addPullRequestReviewComment",
        ],
    )
    def test_graphql_review_mutation_is_classified(
        self,
        mutation_name: str,
    ) -> None:
        document = (
            f'mutation {{ {mutation_name}(input:{{clientMutationId:"x"}}) '
            "{ clientMutationId } }"
        )
        analysis = analyze_github_mutations(f"gh api graphql -f query={json.dumps(document)}")

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.mutations == (
            GitHubMutationRecord(
                method="POST",
                route="/graphql",
                kind=GitHubMutationKind.GRAPHQL_REVIEW,
                request_count=1,
                review_comment_count=None,
            ),
        )

    @pytest.mark.parametrize("mutation_name", ["resolveReviewThread", "unresolveReviewThread"])
    def test_graphql_thread_resolution_is_not_review_publication(
        self,
        mutation_name: str,
    ) -> None:
        document = (
            f'mutation {{ {mutation_name}(input:{{threadId:"T"}}) {{ thread {{ isResolved }} }} }}'
        )

        analysis = analyze_github_mutations(f"gh api graphql -f query={json.dumps(document)}")

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.mutations[0].kind is GitHubMutationKind.OTHER

    @pytest.mark.parametrize("data_flag", ["-d{}", "-Fbody=x", "-Tpayload.json"])
    def test_attached_curl_write_flags_are_not_misclassified_as_get(
        self,
        data_flag: str,
    ) -> None:
        command = f"curl {data_flag} https://api.github.com/repos/o/r/pulls/7/reviews"

        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.mutations[0].kind is GitHubMutationKind.PULL_REVIEW

    def test_identical_nested_mutation_payloads_are_counted_per_occurrence(self) -> None:
        nested = "gh api --method POST /repos/o/r/pulls/7/reviews -f event=COMMENT"

        analysis = analyze_github_mutations(
            f"bash -c {shlex.quote(nested)} && bash -c {shlex.quote(nested)}"
        )

        assert analysis.status is GitHubMutationStatus.MULTIPLE
        assert analysis.request_count == 2
        assert len(analysis.mutations) == 2

    def test_identical_nested_payloads_keep_per_occurrence_cwd(self, tmp_path: Path) -> None:
        (tmp_path / "payload.json").write_text(json.dumps({"body": "x"}), encoding="utf-8")
        nested = "gh api --method POST /repos/o/r/issues/7/comments --input payload.json"
        command = (
            f"cd {shlex.quote(str(tmp_path))} && $({nested}) && "
            f"cd {shlex.quote(str(tmp_path / 'missing'))} && $({nested})"
        )

        analysis = analyze_github_mutations(command, cwd=str(tmp_path))

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "input_file_missing"

    def test_prior_command_that_can_rewrite_literal_input_is_unresolved(
        self,
        tmp_path: Path,
    ) -> None:
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps({"body": "before"}), encoding="utf-8")
        command = (
            "printf '%s' '{\"body\":\"after\"}' > payload.json && "
            "gh api --method POST /repos/o/r/issues/7/comments --input payload.json"
        )

        analysis = analyze_github_mutations(command, cwd=str(tmp_path))

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert "prior command may rewrite" in analysis.reason

    @pytest.mark.parametrize(
        "prefix",
        [
            "python3 -c 'print(1)' && ",
            "printf x > prior.out && ",
            "cd /tmp > /tmp/cd.out && ",
        ],
        ids=["non-allowlisted", "prior-writer", "cd-writer"],
    )
    def test_prior_command_provenance_remains_fail_closed(
        self,
        prefix: str,
        tmp_path: Path,
    ) -> None:
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps({"body": "x"}), encoding="utf-8")

        analysis = analyze_github_mutations(
            prefix + f"gh api --method POST /repos/o/r/issues/7/comments --input {payload}",
            cwd=str(tmp_path),
        )

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "unsafe_input_provenance"

    @pytest.mark.parametrize("redirect", ["2>&1", ">&1"])
    def test_fd_duplication_does_not_make_later_input_unsafe(
        self,
        redirect: str,
        tmp_path: Path,
    ) -> None:
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps({"body": "x"}), encoding="utf-8")

        analysis = analyze_github_mutations(
            f"printf ok {redirect} && gh api --method POST /repos/o/r/issues/7/comments "
            f"--input {payload}",
            cwd=str(tmp_path),
        )

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED

    @pytest.mark.parametrize(
        ("redirect", "expected_status"),
        [
            ("> different.out", GitHubMutationStatus.SINGLE_RESOLVED),
            ("> payload.json", GitHubMutationStatus.UNRESOLVED),
            ("> $OUT", GitHubMutationStatus.UNRESOLVED),
        ],
        ids=["distinct", "same-path", "unresolved-target"],
    )
    def test_current_input_redirect_alias_safety(
        self,
        redirect: str,
        expected_status: GitHubMutationStatus,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "payload.json").write_text(json.dumps({"body": "x"}), encoding="utf-8")
        command = (
            "env -C nested gh api --method POST /repos/o/r/issues/7/comments "
            f"--input ../payload.json {redirect}"
        )
        (tmp_path / "nested").mkdir()

        analysis = analyze_github_mutations(command, cwd=str(tmp_path))

        assert analysis.status is expected_status

    @pytest.mark.parametrize("wrapper", ["shell", "argv"])
    def test_parent_redirect_provenance_reaches_nested_mutation(
        self,
        wrapper: str,
        tmp_path: Path,
    ) -> None:
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps({"body": "x"}), encoding="utf-8")
        nested = f"gh api --method POST /repos/o/r/issues/7/comments --input {payload}"
        if wrapper == "shell":
            command = f"bash -c {shlex.quote(nested)} > {payload}"
        else:
            argv = [
                "gh",
                "api",
                "--method",
                "POST",
                "/repos/o/r/issues/7/comments",
                "--input",
                str(payload),
            ]
            command = (
                "python3 -c "
                + shlex.quote(f"import subprocess; subprocess.run({argv!r})")
                + f" > {payload}"
            )

        analysis = analyze_github_mutations(command, cwd=str(tmp_path))

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "unsafe_input_provenance"

    def test_unresolved_reason_codes_are_distinct_by_failure_family(self, tmp_path: Path) -> None:
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps({"body": "x"}), encoding="utf-8")
        analyses = {
            analyze_github_mutations("gh issue edit $ISSUE --title x").reason_code,
            analyze_github_mutations(
                "for x in 1 2; do gh issue edit 1 --title x; done"
            ).reason_code,
            analyze_github_mutations(
                f"python3 -c 'print(1)' && gh api --method POST /repos/o/r/issues/7/comments "
                f"--input {payload}"
            ).reason_code,
            analyze_github_mutations("cd $DIR && gh issue edit 1 --title x").reason_code,
        }

        assert len(analyses) == 4

    def test_literal_interpreter_cwd_is_used_for_input_resolution(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / "payload.json").write_text(json.dumps({"body": "x"}), encoding="utf-8")
        command = (
            'python3 -c "import subprocess; subprocess.run('
            "['gh','api','--method','POST','/repos/o/r/issues/7/comments',"
            "'--input','payload.json'], cwd='nested')\""
        )

        analysis = analyze_github_mutations(command, cwd=str(tmp_path))

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.mutations[0].kind is GitHubMutationKind.OTHER

    def test_dynamic_interpreter_cwd_is_unresolved(self, tmp_path: Path) -> None:
        command = (
            "python3 -c \"import subprocess; target = 'nested'; subprocess.run("
            "['gh','api','--method','POST','/repos/o/r/issues/7/comments'], cwd=target)\""
        )

        analysis = analyze_github_mutations(command, cwd=str(tmp_path))

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert "cwd is unresolved" in analysis.reason

    def test_non_review_graphql_mutation_remains_other(self) -> None:
        document = 'mutation { addComment(input:{subjectId:"I",body:"x"}) { clientMutationId } }'

        analysis = analyze_github_mutations(f"gh api graphql -f query={json.dumps(document)}")

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.mutations[0].kind is GitHubMutationKind.OTHER

    @pytest.mark.parametrize(
        ("document", "expected_status", "expected_kind"),
        [
            (
                "mutation Batch($ids: [ID!]!, $body: String!) { "
                "first: addComment(input: {subjectId: $ids, body: $body}) "
                "{ clientMutationId } second: addComment(input: "
                "{subjectId: $ids, body: $body}) { clientMutationId } }",
                GitHubMutationStatus.SINGLE_RESOLVED,
                GitHubMutationKind.OTHER,
            ),
            (
                "mutation Publish($id: ID!) { review: submitPullRequestReview("
                "input: {pullRequestReviewId: $id, event: COMMENT}) "
                "{ clientMutationId } }",
                GitHubMutationStatus.SINGLE_RESOLVED,
                GitHubMutationKind.GRAPHQL_REVIEW,
            ),
            (
                "query Nodes($ids: [ID!]!) { nodes(ids: $ids) { id } }",
                GitHubMutationStatus.NONE,
                None,
            ),
        ],
        ids=["aliased-mutation", "review-mutation", "read-only-query"],
    )
    def test_literal_graphql_input_preserves_document_provenance(
        self,
        document: str,
        expected_status: GitHubMutationStatus,
        expected_kind: GitHubMutationKind | None,
        tmp_path: Path,
    ) -> None:
        payload = tmp_path / "graphql.json"
        payload.write_text(
            json.dumps(
                {
                    "query": document,
                    "variables": {"ids": ["I_1", "I_2"], "body": "[literal] $value"},
                }
            ),
            encoding="utf-8",
        )

        analysis = analyze_github_mutations(
            "gh api graphql --input graphql.json",
            cwd=str(tmp_path),
        )

        assert analysis.status is expected_status
        if expected_kind is None:
            assert analysis.request_count == 0
        else:
            assert analysis.request_count == 1
            assert analysis.mutations[0].kind is expected_kind

    def test_input_without_query_does_not_authorize_inline_graphql(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "variables.json").write_text(
            json.dumps({"variables": {"id": "I_1"}}),
            encoding="utf-8",
        )

        analysis = analyze_github_mutations(
            "gh api graphql --input variables.json "
            "'-f' 'query=mutation($id: ID!) { deleteIssue(input: {issueId: $id}) "
            "{ clientMutationId } }'",
            cwd=str(tmp_path),
        )

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.request_count is None

    def test_fully_literal_inline_aliased_graphql_mutation_remains_resolved(self) -> None:
        document = (
            'mutation { one: deleteIssue(input:{issueId:"I1"}) { clientMutationId } '
            'two: deleteIssue(input:{issueId:"I2"}) { clientMutationId } }'
        )

        analysis = analyze_github_mutations(f"gh api graphql -f query={json.dumps(document)}")

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.request_count == 1

    def test_review_input_file_counts_comments_exactly(self, tmp_path: Path) -> None:
        payload = tmp_path / "review.json"
        payload.write_text(
            json.dumps(
                {
                    "event": "COMMENT",
                    "comments": [
                        {"path": "a.py", "line": 1, "body": "a"},
                        {"path": "b.py", "line": 2, "body": "b"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        analysis = analyze_github_mutations(
            "gh api --method POST /repos/o/r/pulls/7/reviews --input review.json",
            cwd=str(tmp_path),
        )

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.request_count == 1
        assert analysis.review_comment_count == 2
        assert analysis.mutations[0].review_comment_count == 2

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr merge 5",
            "gh pr close 5",
            "gh pr comment 5 --body x",
            "gh issue close 5",
            "gh issue edit 5 --add-label x",
            "gh gist create --public note.txt",
            "gh workflow run ci.yml",
            "gh run rerun 123",
            "gh cache delete key",
            "gh secret set TOKEN --body value",
            "gh release delete v1 --yes",
            "gh release upload v1 artifact.whl",
            "gh repo edit --visibility private",
            "gh repo sync owner/repo",
        ],
        ids=[
            "pr-merge",
            "pr-close",
            "pr-comment",
            "issue-close",
            "issue-edit-add-label",
            "gist-create",
            "workflow-run",
            "run-rerun",
            "cache-delete",
            "secret-set",
            "release-delete",
            "release-upload",
            "repo-edit-visibility",
            "repo-sync",
        ],
    )
    def test_widened_gh_subcommands_are_single_resolved_other(self, command: str) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.mutations[0].kind is GitHubMutationKind.OTHER
        assert analysis.request_count == 1

    @pytest.mark.parametrize(
        "command",
        [
            "gh issue edit 23 34 --add-label bug",
            (
                "gh issue edit https://github.com/o/r/issues/23 "
                "https://github.com/o/r/issues/34 --title fixed"
            ),
        ],
        ids=["numeric-targets", "url-targets"],
    )
    def test_issue_edit_counts_each_static_target(self, command: str) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.MULTIPLE
        assert analysis.request_count == 2
        assert analysis.mutations[0].request_count == 2
        assert analysis.reason_code == ""

    @pytest.mark.parametrize(
        "command",
        [
            "gh issue edit 23 --add-label bug --add-label urgent",
            "gh issue edit --repo o/r 23 --body text --body-file path --milestone v1",
            "gh issue edit -Ro/r 23 -bbody -Fpath -mv1 -ttitle",
            "gh issue edit 23 --title=fixed --remove-project=Roadmap",
            "gh issue edit --repo o/r -- 23",
        ],
        ids=["repeated", "separated", "attached-short", "equals", "terminator"],
    )
    def test_issue_edit_single_target_flag_grammar_is_one_request(self, command: str) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.request_count == 1

    @pytest.mark.parametrize(
        "command",
        [
            "gh issue edit --add-label bug",
            "gh issue edit 23 --title",
            "gh issue edit $ISSUE --title fixed",
            "gh issue edit 23 --unknown value",
            "gh issue edit -- --not-an-issue",
        ],
        ids=["zero-targets", "missing-value", "dynamic-target", "unknown-flag", "bad-target"],
    )
    def test_issue_edit_ambiguous_grammar_is_unresolved(self, command: str) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.request_count is None

    def test_pr_review_stays_pull_review_kind_alongside_widened_verbs(self) -> None:
        analysis = analyze_github_mutations("gh pr review 5 --approve")

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.mutations[0].kind is GitHubMutationKind.PULL_REVIEW

    def test_widened_verb_chain_is_multiple(self) -> None:
        analysis = analyze_github_mutations("gh pr merge 5 && gh issue close 6")

        assert analysis.status is GitHubMutationStatus.MULTIPLE
        assert analysis.request_count == 2

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr view",
            "gh issue list",
            "gh gist view 123",
            "gh workflow view ci.yml",
            "gh run view 123",
            "gh pr merge --help",
            "gh gist create --help",
        ],
        ids=[
            "pr-view",
            "issue-list",
            "gist-view",
            "workflow-view",
            "run-view",
            "pr-merge-help",
            "gist-create-help",
        ],
    )
    def test_read_verbs_and_bare_help_flag_are_none(self, command: str) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.NONE
        assert analysis.mutations == ()

    def test_pr_create_remains_owned_by_the_dedicated_guard(self) -> None:
        analysis = analyze_github_mutations("gh pr create --fill")

        assert analysis.status is GitHubMutationStatus.NONE
        assert analysis.mutations == ()

    @pytest.mark.parametrize(
        "command",
        ["gh workflow frobnicate", "gh repo deploy-key list"],
        ids=["unknown-mutation-capable-verb", "nested-mutation-capable-command"],
    )
    def test_unclassified_mutation_capable_subcommands_fail_closed(self, command: str) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert "mutation classification is unresolved" in analysis.reason

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr review 5 --approve --body=--help",
            "gh pr review 5 --approve --body --help",
        ],
        ids=["help-as-attached-value", "help-as-detached-value"],
    )
    def test_help_as_flag_value_does_not_exempt_review_mutation(self, command: str) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.mutations[0].kind is GitHubMutationKind.PULL_REVIEW

    def test_gh_mentioned_inside_a_quoted_loop_string_is_none(self) -> None:
        analysis = analyze_github_mutations('for f in *; do echo "see gh docs"; done')

        assert analysis.status is GitHubMutationStatus.NONE
        assert analysis.mutations == ()

    def test_bare_gh_token_as_argument_inside_a_loop_is_none(self) -> None:
        analysis = analyze_github_mutations('for i in 1; do echo "gh"; done')
        assert analysis.status is GitHubMutationStatus.NONE
        assert analysis.mutations == ()

    def test_gh_verb_reachable_through_a_loop_stays_deny_grade(self) -> None:
        analysis = analyze_github_mutations("for i in 1; do gh pr merge $i; done")

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert "loop" in analysis.reason

    def test_missing_read_only_noun_does_not_crash_mutation_classification(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delitem(command_classification._GH_READ_ONLY_SUBCOMMANDS, "pr")

        analysis = analyze_github_mutations("gh pr merge 7")

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED

    def test_gh_command_after_unrelated_source_is_not_hidden_behind_it(self) -> None:
        analysis = analyze_github_mutations("source .venv/bin/activate && gh pr view --json state")

        assert analysis.status is GitHubMutationStatus.NONE
        assert analysis.mutations == ()

    @pytest.mark.parametrize(
        "payload_kind",
        ["stdin", "malformed", "missing", "oversized", "symlink", "non-object"],
    )
    def test_untrusted_input_file_is_unresolved(
        self,
        payload_kind: str,
        tmp_path: Path,
    ) -> None:
        input_arg = "-"
        if payload_kind == "malformed":
            (tmp_path / "payload.json").write_text("{bad", encoding="utf-8")
            input_arg = "payload.json"
        elif payload_kind == "missing":
            input_arg = "missing.json"
        elif payload_kind == "oversized":
            (tmp_path / "payload.json").write_bytes(b"x" * (1024 * 1024 + 1))
            input_arg = "payload.json"
        elif payload_kind == "symlink":
            target = tmp_path / "target.json"
            target.write_text("{}", encoding="utf-8")
            (tmp_path / "payload.json").symlink_to(target)
            input_arg = "payload.json"
        elif payload_kind == "non-object":
            (tmp_path / "payload.json").write_text("[]", encoding="utf-8")
            input_arg = "payload.json"

        analysis = analyze_github_mutations(
            f"gh api --method POST /repos/o/r/issues/7/comments --input {input_arg}",
            cwd=str(tmp_path),
        )

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.request_count is None
        assert analysis.reason

    def test_relative_input_without_cwd_is_unresolved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "payload.json").write_text("{}", encoding="utf-8")

        analysis = analyze_github_mutations(
            "gh api --method POST /repos/o/r/issues/7/comments --input payload.json"
        )

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason

    @pytest.mark.parametrize(
        "command",
        [
            "echo 'gh api --method POST /repos/o/r/pulls/7/reviews'",
            "printf '%s\\n' 'curl -X POST https://api.github.com/repos/o/r/pulls/7/reviews'",
            "gh api /repos/o/r/pulls/7/reviews",
            "curl https://api.github.com/repos/o/r/pulls/7/reviews",
            "curl -X POST https://example.com/repos/o/r/pulls/7/reviews -d '{}'",
        ],
        ids=["echo", "printf", "gh-get", "curl-get", "non-github-curl"],
    )
    def test_inert_or_out_of_subset_commands_are_none(self, command: str) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.NONE
        assert analysis.request_count == 0
        assert analysis.mutations == ()
