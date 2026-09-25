"""Tests for the shared command classification primitive (hooks/_command_classification.py)."""

from __future__ import annotations

import dataclasses
import json
import os
import pwd
import re
import shlex
from pathlib import Path

import pytest

import autoskillit.hooks._runtime._command_classification as command_classification
import autoskillit.hooks._runtime._github_mutation_analysis as github_mutation_analysis
from autoskillit.hooks._classification._interpreters import (
    all_evaluated_segments_with_provenance,
)
from autoskillit.hooks._classification._tokenizer import ArgvToken
from autoskillit.hooks._runtime._command_classification import (
    _GIT_GLOBAL_FLAG_SPEC,
    _PYTHON_INVOCATION_FLAG_SPEC,
    _SHELL_INVOCATION_FLAG_SPEC,
    PROTECTED_SOURCE_PATH_PATTERNS,
    OutputRedirectPartition,
    StdinConsumer,
    _FlagArity,
    all_evaluated_segments,
    command_has_blocked_protected_path_read,
    command_verb,
    command_verb_and_args,
    evaluated_payloads,
    extract_git_subcommand_and_flags,
    extract_interpreter_write_paths,
    extract_redirect_targets_with_status,
    has_interpreter_write,
    interpreter_invokes,
    is_allowed_protected_path_metadata_command,
    is_gh_command,
    stdin_consumer,
    tokenize_command_segments,
)
from autoskillit.hooks._runtime._github_mutation_analysis import (
    _CURL_FLAG_SPEC,
    _GH_API_FLAG_SPEC,
    _GH_HELP_FLAGS,
    _GH_ISSUE_EDIT_FLAG_SPEC,
    _GH_ISSUE_EDIT_REFERENCE_FLAGS,
    _GH_ISSUE_EDIT_REFERENCE_LIST_FLAGS,
    GitHubMutationAnalysis,
    GitHubMutationKind,
    GitHubMutationRecord,
    GitHubMutationStatus,
    analyze_github_mutations,
)
from tests._evaluation_shape_matrix import (
    EVALUATION_SHAPE_MATRIX,
    wrap_git_op,
)
from tests.hooks._flag_form_matrix import (
    FLAG_FORM_MATRIX,
    GRAPHQL_CONTENT_MATRIX,
    GRAPHQL_DELIVERY_MATRIX,
    GRAPHQL_MATRIX_CONTENT_BODIES,
    deliver_graphql_document,
    graphql_delivery_is_inherently_safe,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_REQUIRED_EXPANSION_CLASSES: frozenset[str] = frozenset(
    {
        "literal_abs",
        "literal_rel",
        "env_set",
        "env_set_braced",
        "env_unset",
        "env_unset_braced",
        "param_operator",
        "param_operator_no_colon",
        "indirect",
        "command_subst",
        "backtick",
        "arithmetic",
        "arithmetic_legacy",
        "positional",
        "special_pid",
        "special_args",
        "digit_after_dollar",
        "ansi_c_quote",
        "tilde_home",
        "tilde_user",
        "tilde_unknown_user",
        "tilde_pwd",
        "tilde_oldpwd",
        "glob_literal",
        "brace_literal",
        "dollar_trailing",
    }
)

_RESOLUTION_MATRIX: tuple[tuple[str, str, str | None], ...] = (
    ("literal_abs", "/tmp/x", "/tmp/x"),
    ("literal_rel", "out/x", "{cwd}/out/x"),
    ("env_set", "$ASK_RWT_DIR/x", "{env}/x"),
    ("env_set_braced", "${ASK_RWT_DIR}/x", "{env}/x"),
    ("env_unset", "$ASK_RWT_UNSET/x", None),
    ("env_unset_braced", "${ASK_RWT_UNSET}/x", None),
    ("param_operator", "${ASK_RWT_DIR:-/tmp}/x", None),
    ("param_operator_no_colon", "${ASK_RWT_DIR-/tmp}/x", None),
    ("indirect", "${!ASK_RWT_REF}/x", None),
    ("command_subst", "$(echo /etc)/x", None),
    ("backtick", "`echo /etc`/x", None),
    ("arithmetic", "$((1+1))/x", None),
    ("arithmetic_legacy", "$[1+1]/x", None),
    ("positional", "$1/x", None),
    ("special_pid", "a$$", None),
    ("special_args", "$@/x", None),
    ("digit_after_dollar", "report$2026.txt", None),
    ("ansi_c_quote", r"$'out\x2fx'", None),
    ("tilde_home", "~/x", "{home}/x"),
    ("tilde_user", "~{user}/x", "{user_home}/x"),
    ("tilde_unknown_user", "~no_such_user_rwt9/x", None),
    ("tilde_pwd", "~+/x", None),
    ("tilde_oldpwd", "~-/x", None),
    ("glob_literal", "out/*.md", "{cwd}/out/*.md"),
    ("brace_literal", "out/{a,b}.md", "{cwd}/out/{a,b}.md"),
    ("dollar_trailing", "cost$", "{cwd}/cost$"),
)


class TestInterpreterInvokes:
    """interpreter_invokes: argv-aware successor to has_interpreter_wrapped_command.

    Preserves the distinction between os.system/shell=True (which reach an
    argv-splitting shell, tokenized and matched) and a plain string passed to
    subprocess.run without shell=True (never split into words by a shell).
    """

    def test_detects_literal_argv_subprocess_run(self):
        cmd = (
            "python3 -c \"import subprocess; subprocess.run(['gh','pr','create','--title','x'])\""
        )
        assert interpreter_invokes(cmd, target=("gh", "pr", "create"))

    def test_detects_os_system_string(self):
        cmd = "python3 -c \"import os; os.system('gh pr create --title x')\""
        assert interpreter_invokes(cmd, target=("gh", "pr", "create"))

    def test_detects_subprocess_run_shell_true_string(self):
        cmd = (
            'python3 -c "import subprocess; '
            "subprocess.run('gh pr create --title x', shell=True)\""
        )
        assert interpreter_invokes(cmd, target=("gh", "pr", "create"))

    def test_detects_python_dash_heredoc_form(self):
        cmd = (
            "python3 - <<'EOF'\n"
            "import subprocess; subprocess.run(['gh','pr','create','--title','x'])\n"
            "EOF"
        )
        assert interpreter_invokes(cmd, target=("gh", "pr", "create"))

    def test_no_match_plain_string_without_shell_true(self):
        cmd = "python3 -c \"import subprocess; subprocess.run('gh pr create --title x')\""
        assert not interpreter_invokes(cmd, target=("gh", "pr", "create"))

    def test_no_match_script_heredoc_data(self):
        cmd = (
            "python3 script.py <<'EOF'\n"
            "import subprocess; subprocess.run(['gh','pr','create','--title','x'])\n"
            "EOF"
        )
        assert not interpreter_invokes(cmd, target=("gh", "pr", "create"))

    def test_no_match_simple_direct_command(self):
        assert not interpreter_invokes("gh pr create --fill", target=("gh", "pr", "create"))

    def test_no_match_when_no_interpreter(self):
        assert not interpreter_invokes("git push origin main", target=("git", "push"))

    def test_detects_os_popen(self):
        cmd = "python3 -c \"import os; os.popen('gh pr create')\""
        assert interpreter_invokes(cmd, target=("gh", "pr", "create"))


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

    @pytest.mark.parametrize(
        "command",
        [
            "printf '%s' '2>/tmp/out'",
            'printf "%s" "2>/tmp/out"',
            r"printf %s 2\>/tmp/out",
        ],
        ids=["single-quoted", "double-quoted", "escaped"],
    )
    def test_quoted_or_escaped_redirect_shape_remains_literal_argv(self, command: str) -> None:
        segments = command_classification._tokenize_command_segments_with_redirects(command)

        assert segments[0].tokens == ["printf", "%s", "2>/tmp/out"]
        assert segments[0].redirect_syntax == [False, False, False]

    @pytest.mark.parametrize(
        ("command", "expected_tokens", "expected_targets", "expected_count"),
        [
            ("cmd > /tmp/out", ["cmd"], ["/tmp/out"], 1),
            ("cmd 2>/tmp/err", ["cmd"], ["/tmp/err"], 1),
            ("cmd >> /tmp/out", ["cmd"], ["/tmp/out"], 1),
            ("cmd 2>>/tmp/err", ["cmd"], ["/tmp/err"], 1),
            ("cmd >$OUT", ["cmd"], [], 1),
            ("cmd >", ["cmd"], [], 1),
            ("cmd 2>&1", ["cmd"], [], 0),
            ("curl --output /tmp/out URL", ["curl", "--output", "/tmp/out", "URL"], [], 0),
        ],
        ids=[
            "separate",
            "merged",
            "append-separate",
            "append-merged",
            "dynamic",
            "missing",
            "fd-dup",
            "curl-option",
        ],
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
            argv_tokens=segment.argv_tokens,
        ) == (expected_tokens, expected_targets, expected_count)

    def test_bare_newline_separates_segments(self):
        result = tokenize_command_segments("echo ok\npip install -e .")
        assert len(result) == 2
        assert result[1] == ["pip", "install", "-e", "."]

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("printf foo\\\nbar", [["printf", "foobar"]]),
            ('printf "foo\\\nbar"', [["printf", "foobar"]]),
            ("printf 'foo\\\nbar'", [["printf", "foo\\\nbar"]]),
            (r"printf foo\\bar", [["printf", r"foo\bar"]]),
            (r"printf \"quoted\"", [["printf", '"quoted"']]),
            (r"printf \$HOME", [["printf", "$HOME"]]),
            ("echo one\necho two", [["echo", "one"], ["echo", "two"]]),
            (
                "gh api repos/O/R/rulesets/13702255 \\\n--jq '.rules'",
                [["gh", "api", "repos/O/R/rulesets/13702255", "--jq", ".rules"]],
            ),
        ],
        ids=[
            "unquoted-continuation",
            "double-quoted-continuation",
            "single-quoted-literal",
            "escaped-backslash",
            "escaped-double-quote",
            "escaped-dollar",
            "bare-newline",
            "continued-gh-api-get",
        ],
    )
    def test_line_continuation_quote_and_escape_contexts(
        self,
        command: str,
        expected: list[list[str]],
    ) -> None:
        command_segments = command_classification._tokenize_command_segments_with_redirects(
            command
        )

        assert [segment.tokens for segment in command_segments] == expected

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


class TestBashWordBoundaries:
    @pytest.mark.parametrize(
        ("command", "expected_tokens", "expected_redirect_syntax"),
        [
            (
                "cp /tmp/s $(echo a /root)/probe.txt",
                ["cp", "/tmp/s", "$(echo a /root)/probe.txt"],
                [False, False, False],
            ),
            (
                "cp /tmp/s `echo a /root`/probe.txt",
                ["cp", "/tmp/s", "`echo a /root`/probe.txt"],
                [False, False, False],
            ),
            (
                "echo x > $(a $(b c))/f",
                ["echo", "x", ">", "$(a $(b c))/f"],
                [False, False, True, False],
            ),
            (
                "echo $((1 + 2)) > f.txt",
                ["echo", "$((1 + 2))", ">", "f.txt"],
                [False, False, True, False],
            ),
            (
                "echo x > >(tee /tmp/p.txt)",
                ["echo", "x", ">", ">(tee /tmp/p.txt)"],
                [False, False, True, False],
            ),
            (
                "diff <(sort a) <(sort b)",
                ["diff", "<(sort a)", "<(sort b)"],
                [False, False, False],
            ),
            (
                "echo x > $(cat > f)/g",
                ["echo", "x", ">", "$(cat > f)/g"],
                [False, False, True, False],
            ),
            (
                "echo x > $(pwd)",
                ["echo", "x", ">", "$(pwd)"],
                [False, False, True, False],
            ),
            (
                "echo '$(x y)' > f.txt",
                ["echo", "$(x y)", ">", "f.txt"],
                [False, False, True, False],
            ),
        ],
        ids=[
            "command-substitution-word",
            "backtick-word",
            "nested-substitution-word",
            "arithmetic-substitution-word",
            "output-process-substitution-word",
            "input-process-substitution-words",
            "inner-redirect-is-not-outer",
            "literal-command-substitution-target",
            "single-quoted-substitution-is-literal",
        ],
    )
    def test_substitutions_preserve_bash_word_boundaries(
        self,
        command: str,
        expected_tokens: list[str],
        expected_redirect_syntax: list[bool],
    ) -> None:
        segments = command_classification._tokenize_command_segments_with_redirects(command)

        assert segments is not None
        assert len(segments) == 1
        assert segments[0].tokens == expected_tokens
        assert segments[0].redirect_syntax == expected_redirect_syntax

    def test_command_substitution_redirect_target_is_unresolved(self) -> None:
        segments = command_classification._tokenize_command_segments_with_redirects(
            "echo x > $(pwd)"
        )

        assert segments is not None
        segment = segments[0]
        assert extract_redirect_targets_with_status(
            segment.tokens,
            "/work",
            redirect_syntax=segment.redirect_syntax,
            argv_tokens=segment.argv_tokens,
        ) == ([], True)

    @pytest.mark.parametrize(
        "command",
        [
            "echo $(echo x",
            "echo $((1 + 2)",
            "echo `echo x",
            "diff <(echo x",
            "echo > >(tee x",
        ],
        ids=["command", "arithmetic", "backtick", "process-input", "process-output"],
    )
    def test_unclosed_substitution_is_a_parse_failure(self, command: str) -> None:
        assert command_classification._tokenize_command_segments_with_redirects(command) is None

    def test_substitution_words_remain_evaluated_payloads(self) -> None:
        payloads = evaluated_payloads("cp a $(echo b c)/d")
        assert [(payload.origin, payload.text) for payload in payloads] == [
            ("substitution", "echo b c")
        ]

        segments = all_evaluated_segments(
            "echo x > >(tee /tmp/p.txt)", include_process_substitutions=True
        )
        assert segments is not None
        assert ["tee", "/tmp/p.txt"] in segments


class TestCommandGroupingStructure:
    @staticmethod
    def _segments(command: str):
        segments = command_classification._tokenize_command_segments_with_redirects(command)
        assert segments is not None
        return segments

    def test_subshell_and_brace_groups_expose_the_command_verb(self) -> None:
        subshell = self._segments("(cp a b)")[0]
        assert subshell.tokens == ["cp", "a", "b"]
        assert command_verb(subshell.tokens) == "cp"
        assert len(subshell.subshell_path) == 1

        brace_group = self._segments("{ cp a b; }")[0]
        assert brace_group.tokens == ["cp", "a", "b"]
        assert command_verb(brace_group.tokens) == "cp"
        assert brace_group.subshell_path == ()

    def test_commands_in_one_subshell_share_its_path(self) -> None:
        segments = self._segments("(cd /tmp; echo x > rel.txt)")

        assert [segment.tokens for segment in segments] == [
            ["cd", "/tmp"],
            ["echo", "x", ">", "rel.txt"],
        ]
        assert len(segments[0].subshell_path) == 1
        assert segments[0].subshell_path == segments[1].subshell_path

    def test_sibling_subshells_have_distinct_paths(self) -> None:
        segments = self._segments("(cd a); (cd b)")

        assert [segment.tokens for segment in segments] == [["cd", "a"], ["cd", "b"]]
        assert len(segments[0].subshell_path) == len(segments[1].subshell_path) == 1
        assert segments[0].subshell_path != segments[1].subshell_path

    def test_chained_subshell_segments_keep_their_enclosing_path(self) -> None:
        segments = self._segments("a && (b; c) || d")

        assert [segment.tokens for segment in segments] == [["a"], ["b"], ["c"], ["d"]]
        assert segments[0].subshell_path == segments[3].subshell_path == ()
        assert len(segments[1].subshell_path) == len(segments[2].subshell_path) == 1
        assert segments[1].subshell_path == segments[2].subshell_path

    def test_nested_subshells_record_both_group_ids(self) -> None:
        segment = self._segments("( (cp a b) )")[0]

        assert segment.tokens == ["cp", "a", "b"]
        assert command_verb(segment.tokens) == "cp"
        assert len(segment.subshell_path) == 2
        assert segment.subshell_path[0] != segment.subshell_path[1]

    def test_three_level_nested_subshells_record_all_group_ids(self) -> None:
        segment = self._segments("( ( (cp a b) ) )")[0]

        assert segment.tokens == ["cp", "a", "b"]
        assert command_verb(segment.tokens) == "cp"
        assert len(segment.subshell_path) == 3
        assert len(set(segment.subshell_path)) == 3  # all distinct

    def test_brace_groups_do_not_extend_subshell_path(self) -> None:
        subshell_in_brace = self._segments("{ (cp a b); }")[0]

        assert subshell_in_brace.tokens == ["cp", "a", "b"]
        # The brace is the outer group; the subshell is the inner one.
        assert len(subshell_in_brace.subshell_path) == 1

        brace_in_subshell = self._segments("( { cp a b; } )")[0]
        assert brace_in_subshell.tokens == ["cp", "a", "b"]
        assert len(brace_in_subshell.subshell_path) == 1

    def test_function_body_is_a_normal_command_segment(self) -> None:
        segments = self._segments("f() { cp a b; }")
        body = next(segment for segment in segments if command_verb(segment.tokens) == "cp")

        assert body.tokens == ["cp", "a", "b"]
        assert body.subshell_path == ()

    @pytest.mark.parametrize(
        "command",
        ["if (cp a b); then :; fi", "while (cp a b); do :; done"],
        ids=["if-subshell", "while-subshell"],
    )
    def test_control_construct_subshell_keeps_its_path(self, command: str) -> None:
        segments = self._segments(command)
        body = next(segment for segment in segments if command_verb(segment.tokens) == "cp")

        assert body.tokens == ["cp", "a", "b"]
        assert len(body.subshell_path) == 1

    def test_case_pattern_close_does_not_close_the_outer_subshell(self) -> None:
        segments = self._segments("(case x in a) cp a b;; esac)")
        body = next(segment for segment in segments if command_verb(segment.tokens) == "cp")

        assert body.tokens == ["cp", "a", "b"]
        assert len(body.subshell_path) == 1

    @pytest.mark.parametrize(
        ("command", "expected_path_length"),
        [
            ("(cat <<EOF > f.txt\nbody\nEOF\n)", 1),
            ("{ cat <<'EOF' > f.txt\nbody\nEOF\n}", 0),
        ],
        ids=["subshell-heredoc", "brace-heredoc"],
    )
    def test_heredoc_body_does_not_hide_group_close(
        self, command: str, expected_path_length: int
    ) -> None:
        segments = self._segments(command)
        cat = next(segment for segment in segments if command_verb(segment.tokens) == "cat")

        assert cat.tokens == ["cat", ">", "f.txt"]
        assert len(cat.subshell_path) == expected_path_length

    def test_grouped_gh_and_git_commands_are_classified_by_verb(self) -> None:
        gh = self._segments("(gh pr merge 1 --admin)")[0]
        git = self._segments("(git push --force)")[0]

        assert command_classification.is_gh_command(gh.tokens)
        assert command_classification.is_git_command(git.tokens)

    def test_non_groups_do_not_create_subshell_paths(self) -> None:
        case_echo = next(
            segment
            for segment in self._segments('case "$x" in a) echo hi;; esac')
            if command_verb(segment.tokens) == "echo"
        )
        arithmetic_cp = next(
            segment
            for segment in self._segments("(( i = 1 )) && cp a b")
            if command_verb(segment.tokens) == "cp"
        )
        quoted_parens = self._segments('echo "(x)"')[0]
        escaped_parens = self._segments(r"echo \(x\)")[0]

        assert case_echo.subshell_path == ()
        assert arithmetic_cp.subshell_path == ()
        assert quoted_parens.tokens == escaped_parens.tokens == ["echo", "(x)"]
        assert quoted_parens.subshell_path == escaped_parens.subshell_path == ()


class TestEmptyCommandClassification:
    def test_comment_only_commands_are_empty_and_parse_successfully(self) -> None:
        assert all_evaluated_segments("# note") == []
        assert all_evaluated_segments("  # a\n# b\n") == []
        assert all_evaluated_segments("# (unclosed group") == []
        assert all_evaluated_segments("true; # { unclosed group") == [["true"]]

    def test_empty_and_parse_failure_regressions_remain_distinct(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        assert all_evaluated_segments("echo 'unterminated") is None
        assert all_evaluated_segments("true # c") == [["true"]]
        assert tokenize_shell_payload_segments("# note") == []
        assert tokenize_shell_payload_segments("true # c") == []
        assert tokenize_command_segments("echo 'unterminated") == []


class TestStdinLiteralBinding:
    """Binds every heredoc/herestring to the segment that consumes it.

    Rectify #4941 Part A: the tokenizer is the only reader of the raw
    command; `StdinLiteral.outer_expansion` and `_CommandSegment.
    stdin_literals`/`piped_from_previous` are the tagged-at-tokenization
    provenance every downstream scanner consumes instead of re-deriving it.
    """

    def _segments(self, command: str):
        return command_classification._tokenize_command_segments_with_redirects(command)

    def test_quoted_heredoc_binds_to_consumer(self):
        segments = self._segments("bash <<'EOF'\nx\nEOF\n")
        assert len(segments) == 1
        assert segments[0].tokens == ["bash"]
        assert len(segments[0].stdin_literals) == 1
        literal = segments[0].stdin_literals[0]
        assert literal.text == "x"
        assert literal.kind == "heredoc"
        assert literal.outer_expansion is False
        assert literal.source_span is not None
        command = "bash <<'EOF'\nx\nEOF\n"
        assert command[literal.source_span[0] : literal.source_span[1]] == "x"

    def test_unquoted_heredoc_with_real_redirect(self):
        segments = self._segments("cat <<EOF > f\nx\nEOF\n")
        assert len(segments) == 1
        segment = segments[0]
        assert segment.tokens == ["cat", ">", "f"]
        assert segment.redirect_syntax == [False, True, False]
        assert len(segment.stdin_literals) == 1
        assert segment.stdin_literals[0].outer_expansion is True

    def test_heredoc_pipe_binds_to_first_segment(self):
        segments = self._segments("cat <<'EOF' | bash\nx\nEOF\n")
        assert len(segments) == 2
        assert segments[0].tokens == ["cat"]
        assert len(segments[0].stdin_literals) == 1
        assert segments[0].piped_from_previous is False
        assert segments[1].tokens == ["bash"]
        assert segments[1].stdin_literals == ()
        assert segments[1].piped_from_previous is True

    def test_tab_heredoc_preserves_existing_strip_behavior(self):
        segments = self._segments("cat <<-'EOF'\n\tx\n\tEOF\n")
        assert segments[0].tokens == ["cat"]
        assert segments[0].stdin_literals[0].text == "\tx"
        # Existing preservation assertion, unchanged (test_heredoc_body_stripped_before_tokenize).
        assert tokenize_command_segments("cat <<'EOF'\nbody content with > symbols\nEOF") == [
            ["cat"]
        ]

    @pytest.mark.parametrize(
        ("command", "expected_body", "expected_outer_expansion"),
        [
            ('bash <<< "git push"', "git push", True),
            ("bash <<<'git push'", "git push", False),
            ("bash <<< 'git push'", "git push", False),
        ],
        ids=["double-quoted", "fused-single-quoted", "spaced-single-quoted"],
    )
    def test_herestring_quoting_forms(self, command, expected_body, expected_outer_expansion):
        segments = self._segments(command)
        assert len(segments) == 1
        assert segments[0].tokens == ["bash"]
        literal = segments[0].stdin_literals[0]
        assert literal.kind == "herestring"
        assert literal.text == expected_body
        assert literal.outer_expansion is expected_outer_expansion

    def test_heredoc_placeholder_precedes_opener_remainder(self):
        segments = self._segments("cat > audit.log <<'EOF' | bash\nx\nEOF\n")
        assert len(segments) == 2
        assert segments[0].tokens == ["cat", ">", "audit.log"]
        assert len(segments[0].stdin_literals) == 1
        assert segments[1].tokens == ["bash"]
        assert segments[1].piped_from_previous is True

    def test_dash_c_program_alongside_heredoc(self):
        segments = self._segments("bash -c 'x' <<'EOF'\ny\nEOF\n")
        assert len(segments) == 1
        assert segments[0].tokens == ["bash", "-c", "x"]
        assert segments[0].stdin_literals[0].text == "y"

    def test_strip_heredoc_bodies_parity_preserved(self):
        """The parity-locked oracle (tests/core/test_bash_write_targets.py) is
        untouched; this only pins that this module's own strip_heredoc_bodies
        still matches its pre-rectify output shape."""
        assert (
            command_classification.strip_heredoc_bodies("cat <<'EOF'\nbody\nEOF\ncat file")
            == "cat <<'EOF'\nEOF\ncat file"
        )


class TestCommandVerbAndArgs:
    def test_returns_verb_and_args(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_strips_env_assignments(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["env", "FOO=bar", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_strips_leading_posix_assignment(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["FOO=bar", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_env_with_value_taking_flag(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(
            ["env", "-u", "FOO", "--chdir", "/tmp", "-S", "x", "pip", "install", "-e", "."]
        )
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_env_split_string_consumes_value(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(
            ["env", "--split-string", "FOOBAR", "pip", "install", "-e", "."]
        )
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_env_attached_value_flag(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(
            ["env", "--chdir=/tmp", "--unset=FOO", "pip", "install", "-e", "."]
        )
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_env_double_dash_terminator(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["env", "--", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_sudo_wrapper(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["sudo", "-u", "root", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_nice_wrapper(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["nice", "-n", "5", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_timeout_wrapper_mandatory_duration(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["timeout", "30", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_stdbuf_wrapper_short_flag(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["stdbuf", "-o0", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_command_wrapper(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["command", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_nohup_wrapper(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["nohup", "pip", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["install", "-e", "."]

    def test_double_dash_terminator(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["pip", "--", "install", "-e", "."])
        assert verb == "pip"
        assert args == ["--", "install", "-e", "."]

    def test_empty_segment_returns_empty(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args([])
        assert verb == ""
        assert args == []

    def test_wrapper_only_returns_empty(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["env"])
        assert verb == ""
        assert args == []

    def test_timeout_missing_duration_returns_empty(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["timeout"])
        assert verb == ""
        assert args == []

    def test_bare_env_wrapper(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        verb, args = command_verb_and_args(["env"])
        assert verb == ""
        assert args == []

    def test_command_verb_delegates_to_command_verb_and_args(self):
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args

        seg = ["env", "FOO=bar", "pip", "install", "-e", "."]
        verb_from_helper, _ = command_verb_and_args(seg)
        assert verb_from_helper == command_verb(seg)


class TestCommandPositionCandidateSpans:
    @pytest.mark.parametrize(
        ("tokens", "expected"),
        [
            (["env", "MODE=read", "gh", "pr", "view"], ((2, 5),)),
            (["while", "gh", "pr", "view"], ((1, 4),)),
            (["until", "gh", "pr", "view"], ((1, 4),)),
            (["if", "gh", "pr", "view"], ((1, 4),)),
            # Group bodies are independent tokenizer segments; candidate discovery
            # sees only a segment's direct command, with no brace-body fallback.
            (["inspect()", "{", "gh", "pr", "view"], ((0, 5),)),
            (["{", "gh", "pr", "view"], ((0, 4),)),
        ],
        ids=[
            "direct",
            "while-control",
            "until-control",
            "if-control",
            "no-function-body-fallback",
            "no-brace-body-fallback",
        ],
    )
    def test_returns_spans_in_the_supplied_token_index_domain(
        self,
        tokens: list[str],
        expected: tuple[tuple[int, int], ...],
    ) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _command_position_candidate_spans,
        )

        assert _command_position_candidate_spans(tokens) == expected


class TestExtractShellCommandPayloads:
    def test_bash_c_payload(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_shell_command_payloads,
        )

        assert extract_shell_command_payloads('bash -c "pip install -e ."') == ["pip install -e ."]

    def test_sh_c_payload(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_shell_command_payloads,
        )

        assert extract_shell_command_payloads('sh -c "pip install -e ."') == ["pip install -e ."]

    def test_zsh_c_payload(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_shell_command_payloads,
        )

        assert extract_shell_command_payloads('zsh -c "pip install -e ."') == ["pip install -e ."]

    def test_dash_c_payload(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_shell_command_payloads,
        )

        assert extract_shell_command_payloads('dash -c "pip install -e ."') == ["pip install -e ."]

    def test_eval_payload(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_shell_command_payloads,
        )

        assert extract_shell_command_payloads('eval "pip install -e ."') == ["pip install -e ."]

    def test_dollar_paren_payload(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_shell_command_payloads,
        )

        payloads = extract_shell_command_payloads("echo $(pip install -e .)")
        assert payloads == ["pip install -e ."]

    def test_backtick_payload(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_shell_command_payloads,
        )

        payloads = extract_shell_command_payloads("echo `pip install -e .`")
        assert payloads == ["pip install -e ."]

    def test_double_quoted_substitution_payload(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_shell_command_payloads,
        )

        payloads = extract_shell_command_payloads('echo "$(pip install -e .)"')
        assert payloads == ["pip install -e ."]

    def test_single_quoted_substitution_inert(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_shell_command_payloads,
        )

        assert extract_shell_command_payloads("echo '$(pip install -e .)'") == []

    def test_escaped_substitution_inert(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_shell_command_payloads,
        )

        assert extract_shell_command_payloads('echo "\\$(pip install -e .)"') == []

    def test_nested_substitution(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_shell_command_payloads,
        )

        payloads = extract_shell_command_payloads('bash -c "echo $(pip install -e .)"')
        assert "pip install -e ." in payloads

    def test_no_payloads_returns_empty_list(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_shell_command_payloads,
        )

        assert extract_shell_command_payloads("echo hi") == []

    def test_absolute_bash_path_normalized(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_shell_command_payloads,
        )

        assert extract_shell_command_payloads('/bin/bash -c "pip install -e ."') == [
            "pip install -e ."
        ]


_GIT_PUSH_FORCE_ARGV = ("git", ["push", "--force", "origin", "main"])


class TestEvaluatedPayloads:
    """The single authority for "what will be evaluated, and by whom".

    Parametrized from EVALUATION_SHAPE_MATRIX with inner text
    `git push --force origin main` (rectify #4941 Part A).
    """

    @pytest.mark.parametrize(
        "shape", [s for s in EVALUATION_SHAPE_MATRIX if s.executes], ids=lambda s: s.id
    )
    def test_executing_shapes_expose_the_git_segment(self, shape):
        cmd = wrap_git_op(shape, ("push", "--force"))
        segments = all_evaluated_segments(cmd)
        assert segments is not None, f"{shape.id}: all_evaluated_segments returned None"
        found = [command_verb_and_args(segment) for segment in segments]
        assert _GIT_PUSH_FORCE_ARGV in found, f"{shape.id}: git segment missing from {found}"

    @pytest.mark.parametrize(
        "shape", [s for s in EVALUATION_SHAPE_MATRIX if not s.executes], ids=lambda s: s.id
    )
    def test_inert_shapes_expose_no_git_segment(self, shape):
        cmd = wrap_git_op(shape, ("push", "--force"))
        evaluated_payloads(cmd)  # smoke: must not raise for an inert shape
        segments = all_evaluated_segments(cmd)
        found = [command_verb_and_args(segment) for segment in (segments or [])]
        assert _GIT_PUSH_FORCE_ARGV not in found, f"{shape.id}: git segment leaked into {found}"

    def test_dollar_substitution_inside_bash_heredoc_remains_evaluated(self):
        """Named regression guard: an A-first fix (inert-body carve-out) must not
        silently remove Defect B's coverage of a live substitution inside a
        heredoc that a shell actually executes."""
        cmd = "bash <<'EOF'\necho $(git push --force origin main)\nEOF\n"
        segments = all_evaluated_segments(cmd)
        assert segments is not None
        found = [command_verb_and_args(segment) for segment in segments]
        assert _GIT_PUSH_FORCE_ARGV in found

    @pytest.mark.parametrize(
        ("tokens", "expected"),
        [
            (["bash"], StdinConsumer.SHELL),
            (["sudo", "bash", "-e"], StdinConsumer.SHELL),
            (["bash", "-e", "-o", "pipefail"], StdinConsumer.SHELL),
            (["bash", "--definitely-unknown-flag"], StdinConsumer.SHELL),
            (["bash", "script.sh"], StdinConsumer.INERT),
            (["bash", "-s", "arg"], StdinConsumer.SHELL),
            (["bash", "-c", "x"], StdinConsumer.INERT),
            (["python3", "-"], StdinConsumer.PYTHON),
            (["python3", "-u"], StdinConsumer.PYTHON),
            (["python3", "-W", "ignore", "-"], StdinConsumer.PYTHON),
            (["python3", "script.py"], StdinConsumer.INERT),
            (["python3", "-m", "mod"], StdinConsumer.INERT),
            (["python3", "-c", "x"], StdinConsumer.INERT),
            (["cat"], StdinConsumer.INERT),
            (["tee", "f"], StdinConsumer.INERT),
            (["gh", "pr", "create", "--body-file", "-"], StdinConsumer.INERT),
            (["perl"], StdinConsumer.TEXT),
            (["node", "-"], StdinConsumer.TEXT),
        ],
    )
    def test_stdin_consumer_classification(self, tokens, expected):
        assert stdin_consumer(tokens) == expected

    # `-c` is also in the spec's VALUE-arity map, but `_stdin_consumer_for_verb`
    # checks its own `inert_flags` bucket first, so `-c` never reaches the
    # spec lookup -- it always resolves INERT rather than SHELL.
    _SHELL_INERT_FLAGS = frozenset({"-c"})

    @pytest.mark.parametrize("flag", sorted(_SHELL_INVOCATION_FLAG_SPEC))
    def test_shell_invocation_value_flags_consume_one_token(self, flag):
        tokens = ["bash", flag, "VALUE"]
        expected = StdinConsumer.INERT if flag in self._SHELL_INERT_FLAGS else StdinConsumer.SHELL
        assert stdin_consumer(tokens) == expected

    # `-c`/`-m` are also in the spec's VALUE-arity map, but they are in
    # `_stdin_consumer_for_verb`'s `inert_flags` bucket, checked first --
    # they always resolve INERT rather than PYTHON.
    _PYTHON_INERT_FLAGS = frozenset({"-c", "-m"})

    @pytest.mark.parametrize("flag", sorted(_PYTHON_INVOCATION_FLAG_SPEC))
    def test_python_invocation_value_flags_consume_one_token(self, flag):
        tokens = ["python3", flag, "VALUE"]
        expected = (
            StdinConsumer.INERT if flag in self._PYTHON_INERT_FLAGS else StdinConsumer.PYTHON
        )
        assert stdin_consumer(tokens) == expected

    @pytest.mark.parametrize(
        "flag", ["-e", "-x", "-u", "-l", "--norc", "--posix", "+x", "--totally-unknown-flag"]
    )
    def test_shell_boolean_bucket_skips_unrecognized_flags(self, flag):
        assert stdin_consumer(["bash", flag]) == StdinConsumer.SHELL

    @pytest.mark.parametrize("flag", ["-u", "-B", "-E", "-I", "-S", "--totally-unknown-flag"])
    def test_python_boolean_bucket_skips_unrecognized_flags(self, flag):
        assert stdin_consumer(["python3", flag]) == StdinConsumer.PYTHON


class TestTokenizeShellPayloadSegments:
    def test_bash_c_direct_payload(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        result = tokenize_shell_payload_segments('bash -c "gh pr create --fill"')
        assert result == [["gh", "pr", "create", "--fill"]]

    def test_absolute_bash_path(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        result = tokenize_shell_payload_segments('/bin/bash -c "gh pr create --fill"')
        assert result == [["gh", "pr", "create", "--fill"]]

    def test_env_prefix_wrapper(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        result = tokenize_shell_payload_segments('env FOO=1 bash -c "gh pr create --fill"')
        assert result == [["gh", "pr", "create", "--fill"]]

    def test_sudo_wrapper(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        result = tokenize_shell_payload_segments('sudo /bin/bash -c "gh pr create --fill"')
        assert result == [["gh", "pr", "create", "--fill"]]

    def test_nested_bash_c_payload(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        result = tokenize_shell_payload_segments("""bash -c 'bash -c "gh pr create --fill"'""")
        assert ["gh", "pr", "create", "--fill"] in result

    def test_operator_separated_commands_inside_payload(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        result = tokenize_shell_payload_segments('bash -c "echo ready && gh pr create --fill"')
        assert ["echo", "ready"] in result
        assert ["gh", "pr", "create", "--fill"] in result

    def test_dedupes_repeated_payload_strings(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        result = tokenize_shell_payload_segments(
            """bash -c 'gh pr create' && bash -c "gh pr create --fill\""""
        )
        flat = [tuple(s) for s in result]
        assert flat.count(("gh", "pr", "create")) == 1

    def test_malformed_inner_payload_returns_none(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        result = tokenize_shell_payload_segments("""bash -c 'bash -c "echo \\"unclosed"'""")
        assert result is None

    def test_quoted_close_paren_does_not_truncate_substitution(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        result = tokenize_shell_payload_segments('echo $(echo "a) b" && gh pr create --fill)')
        assert result is not None
        assert ["gh", "pr", "create", "--fill"] in result

    def test_no_evaluated_shell_payload_returns_empty_list(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        assert tokenize_shell_payload_segments("gh pr create --fill") == []

    def test_empty_command_returns_empty_list(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        # Empty/whitespace outer is excluded by the wrapper's include_outer=False
        # contract; an outer-only command (no nested payload) yields no segments.
        assert tokenize_shell_payload_segments("") == []
        assert tokenize_shell_payload_segments("   ") == []

    def test_iterator_yields_empty_outer_for_include_outer_true(self):
        # Direct coverage of the iterator's `include_outer=True` empty-outer
        # branch: an empty/whitespace command must yield `[]` as the first
        # entry so consumers relying on "outer is always the first yield"
        # (e.g. `_iter_evaluated_payload_segments`) see one entry, not zero.
        from autoskillit.hooks._classification._interpreters import (
            _iter_shell_payload_segment_groups,
        )

        assert list(_iter_shell_payload_segment_groups("")) == [[]]
        assert list(_iter_shell_payload_segment_groups("   ")) == [[]]
        # With include_outer=False, the empty outer is processed for nested
        # discovery but not yielded.
        assert list(_iter_shell_payload_segment_groups("", include_outer=False)) == []
        assert list(_iter_shell_payload_segment_groups("   ", include_outer=False)) == []

    def test_process_substitution_traversal_is_opt_in(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        command = "cat <(gh pr view 7 --json number)"

        assert tokenize_shell_payload_segments(command) == []
        assert tokenize_shell_payload_segments(
            command,
            include_process_substitutions=True,
        ) == [["gh", "pr", "view", "7", "--json", "number"]]

    def test_identical_process_substitution_occurrences_are_preserved(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        result = tokenize_shell_payload_segments(
            "cat <(gh pr view 7) <(gh pr view 7)",
            include_process_substitutions=True,
        )

        assert result == [["gh", "pr", "view", "7"], ["gh", "pr", "view", "7"]]

    def test_unbalanced_process_substitution_returns_none(self):
        from autoskillit.hooks._runtime._command_classification import (
            tokenize_shell_payload_segments,
        )

        result = tokenize_shell_payload_segments(
            "cat <(gh pr view 7",
            include_process_substitutions=True,
        )

        assert result is None


class TestProcessSubstitutionExtraction:
    def test_active_input_and_output_occurrences_preserve_source_order(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _extract_process_substitution_occurrences,
        )

        command = "cat <(gh pr view 7) >(tee result.txt)"
        occurrences = _extract_process_substitution_occurrences(command)

        assert [(kind, body, balanced) for kind, _, _, body, balanced in occurrences] == [
            ("<(", "gh pr view 7", True),
            (">(", "tee result.txt", True),
        ]
        for kind, start, end, body, _balanced in occurrences:
            assert command[start:end] == f"{kind}{body})"

    @pytest.mark.parametrize(
        "command",
        [
            "echo '<(gh pr view 7)'",
            'echo "<(gh pr view 7)"',
            r"echo \<(gh pr view 7)",
        ],
        ids=["single-quoted", "double-quoted", "escaped"],
    )
    def test_quoted_and_escaped_process_substitutions_are_inert(self, command: str) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _extract_process_substitution_occurrences,
        )

        assert _extract_process_substitution_occurrences(command) == ()

    def test_balanced_parentheses_respect_quoted_close_parens(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _extract_process_substitution_occurrences,
        )

        command = "cat <(printf '%s' 'a) b' && gh pr view 7)"
        ((kind, start, end, body, balanced),) = _extract_process_substitution_occurrences(command)

        assert (kind, body, balanced) == ("<(", "printf '%s' 'a) b' && gh pr view 7", True)
        assert command[start:end] == f"{kind}{body})"

    def test_malformed_process_substitution_retains_its_unbalanced_span(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _extract_process_substitution_occurrences,
        )

        command = "cat <(gh pr view 7"

        assert _extract_process_substitution_occurrences(command) == (
            ("<(", command.index("<("), len(command), "gh pr view 7", False),
        )

    def test_command_classification_exposes_the_lazy_interpreter_gateway(self) -> None:
        from autoskillit.hooks._classification._interpreters import (
            _extract_process_substitution_occurrences as implementation,
        )
        from autoskillit.hooks._runtime._command_classification import (
            _extract_process_substitution_occurrences as gateway,
        )

        command = "cat <(gh pr view 7)"
        assert gateway(command) == implementation(command)


class TestExtractInterpreterCommandPayloads:
    def test_shell_string_pip_install(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_interpreter_command_payloads,
        )

        payloads, has_unresolved = extract_interpreter_command_payloads(
            "python -c \"import subprocess; subprocess.run('pip install -e .', shell=True)\""
        )
        assert has_unresolved is False
        assert payloads == ["pip install -e ."]

    def test_argv_list_pip(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_interpreter_command_payloads,
        )

        payloads, has_unresolved = extract_interpreter_command_payloads(
            "python3 -c \"import subprocess; subprocess.run(['pip','install','-e','.'])\""
        )
        assert has_unresolved is False
        assert payloads == [["pip", "install", "-e", "."]]

    def test_argv_tuple_pip(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_interpreter_command_payloads,
        )

        payloads, has_unresolved = extract_interpreter_command_payloads(
            "python3 -c \"import subprocess; subprocess.run(('pip','install','--editable','.'))\""
        )
        assert has_unresolved is False
        assert payloads == [["pip", "install", "--editable", "."]]

    def test_argv_list_rg_reader(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_interpreter_command_payloads,
        )

        payloads, has_unresolved = extract_interpreter_command_payloads(
            "python3 -c \"import subprocess; subprocess.run(['rg', 'pip install -e', 'docs/'])\""
        )
        assert has_unresolved is False
        assert payloads == [["rg", "pip install -e", "docs/"]]

    def test_unresolved_payload_returns_flag(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_interpreter_command_payloads,
        )

        cmd = "python3 -c \"import subprocess; subprocess.run(['pip', cmd, '-e', '.'])\""
        _payloads, has_unresolved = extract_interpreter_command_payloads(cmd)
        assert has_unresolved is True

    def test_no_subprocess_returns_empty(self):
        from autoskillit.hooks._runtime._command_classification import (
            extract_interpreter_command_payloads,
        )

        payloads, has_unresolved = extract_interpreter_command_payloads(
            "python3 -c \"print('hello')\""
        )
        assert payloads == []
        assert has_unresolved is False

    def test_non_python_returns_empty(self):
        from autoskillit.hooks._runtime._command_classification import (
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


class TestScanWriteTargets:
    @pytest.mark.parametrize(
        ("command", "targets", "unresolved", "parseable", "has_write"),
        [
            ("echo hi", (), False, True, False),
            ("echo x > out.txt", ("{cwd}/out.txt",), False, True, True),
            ("cp a out.txt", ("{cwd}/out.txt",), False, True, True),
            ("echo x > /dev/null", (), False, True, True),
            ("echo x > >(tee /tmp/p.txt)", ("/tmp/p.txt",), False, True, True),
            ("sed --in-place=bak 's/a/b/' /tmp/f", ("/tmp/f",), False, True, True),
            ("git checkout main -- src/x.py", ("{cwd}/src/x.py",), False, True, True),
            ("git reset --hard", (), False, True, True),
            ("gh issue edit 1 --body-file b.md", (), False, True, False),
            ("cd sub && echo x > out.txt", ("{cwd}/sub/out.txt",), False, True, True),
            ('echo x > "$ASK_UNSET_RWT"', (), True, True, True),
            ('cp a "$ASK_UNSET_RWT"', (), True, True, True),
            ("echo 'unterminated", (), False, False, False),
            ('cd "$ASK_UNSET_RWT" && echo x > rel.txt', (), True, True, True),
            (
                'cd "$ASK_UNSET_RWT" && echo x > /tmp/abs.txt',
                ("/tmp/abs.txt",),
                False,
                True,
                True,
            ),
            ("timeout 30 tee /tmp/o.txt", ("/tmp/o.txt",), False, True, True),
            ("nice -n 5 tee /tmp/o.txt", ("/tmp/o.txt",), False, True, True),
            ("env CACHE_DIR=/tmp tee /tmp/o.txt", ("/tmp/o.txt",), False, True, True),
            ("sudo --user=root rm /tmp/f", ("/tmp/f",), False, True, True),
            ("timeout 30 patch /tmp/f p.diff", ("/tmp/f",), False, True, True),
            ("sudo git checkout -- /tmp/f", ("/tmp/f",), False, True, True),
            ("FOO=bar git checkout -- ../out/f", ("{cwd}/../out/f",), False, True, True),
            ("git -C /tmp/d checkout -- rel.py", ("/tmp/d/rel.py",), False, True, True),
            ("git -C /tmp/d -C sub checkout -- rel.py", ("/tmp/d/sub/rel.py",), False, True, True),
            ("git --work-tree=/tmp/w checkout -- rel.py", (), True, True, True),
            ("env --chdir /tmp/d tee rel.txt", ("/tmp/d/rel.txt",), False, True, True),
        ],
    )
    def test_scan_contract(
        self,
        command: str,
        targets: tuple[str, ...],
        unresolved: bool,
        parseable: bool,
        has_write: bool,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ASK_UNSET_RWT", raising=False)
        cwd = str(tmp_path)
        expected = command_classification.WriteTargetScan(
            targets=tuple(target.format(cwd=cwd) for target in targets),
            unresolved=unresolved,
            parseable=parseable,
            has_write=has_write,
        )

        assert command_classification.scan_write_targets(command, cwd) == expected

    def test_env_chdir_applies_to_verb_but_not_shell_redirect(self, tmp_path: Path) -> None:
        cwd = str(tmp_path)
        scan = command_classification.scan_write_targets(
            "env --chdir /tmp/d tee x.txt > out.txt", cwd
        )

        assert set(scan.targets) == {"/tmp/d/x.txt", str(tmp_path / "out.txt")}
        assert scan.unresolved is False
        assert scan.parseable is True
        assert scan.has_write is True

    @pytest.mark.parametrize(
        "command",
        [
            "printf '%s' '>/tmp/out'",
            'printf "%s" "2>/tmp/out"',
            r"printf %s 2\>/tmp/out",
        ],
        ids=["single-quoted", "double-quoted", "escaped"],
    )
    def test_quoted_redirect_glyph_is_not_a_write(self, command: str) -> None:
        assert command_classification.scan_write_targets(command, "/work") == (
            command_classification.WriteTargetScan(
                targets=(), unresolved=False, parseable=True, has_write=False
            )
        )

    @pytest.mark.parametrize(
        ("command", "targets", "unresolved"),
        [
            pytest.param(
                "echo x > '$ASK_WRITE_TARGET_DIR/out'",
                ("/work/$ASK_WRITE_TARGET_DIR/out",),
                False,
                id="single-quoted-redirect-variable",
            ),
            pytest.param(
                'echo x > "$ASK_WRITE_TARGET_DIR/out"',
                ("/resolved/dir/out",),
                False,
                id="double-quoted-redirect-variable",
            ),
            pytest.param(
                r"echo x > \$ASK_WRITE_TARGET_DIR/out",
                ("/work/$ASK_WRITE_TARGET_DIR/out",),
                False,
                id="escaped-redirect-variable",
            ),
            pytest.param(
                "echo x > '$[1+2]/out'",
                ("/work/$[1+2]/out",),
                False,
                id="single-quoted-legacy-arithmetic",
            ),
            pytest.param("echo x > $[1+2]/out", (), True, id="active-legacy-arithmetic"),
            pytest.param(
                "cp a '$(pwd)/out'",
                ("/work/$(pwd)/out",),
                False,
                id="single-quoted-copy-target",
            ),
            pytest.param('cp a "$(pwd)/out"', (), True, id="active-copy-target"),
            pytest.param("echo x > '~/out'", ("/work/~/out",), False, id="quoted-tilde"),
            pytest.param("echo x > ~/out", ("/home/write-target/out",), False, id="active-tilde"),
        ],
    )
    def test_shell_source_reaches_write_target_scan(
        self,
        command: str,
        targets: tuple[str, ...],
        unresolved: bool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ASK_WRITE_TARGET_DIR", "/resolved/dir")
        monkeypatch.setenv("HOME", "/home/write-target")

        assert command_classification.scan_write_targets(command, "/work") == (
            command_classification.WriteTargetScan(
                targets=targets, unresolved=unresolved, parseable=True, has_write=True
            )
        )

    @pytest.mark.parametrize(
        ("command", "targets", "unresolved"),
        [
            pytest.param(
                "echo x > before; cd /tmp/d; echo x > after",
                ("{cwd}/before", "/tmp/d/after"),
                False,
                id="cd-applies-in-source-order",
            ),
            pytest.param(
                "cd -P /tmp/d && echo x > rel.txt",
                ("/tmp/d/rel.txt",),
                False,
                id="cd-physical-option",
            ),
            pytest.param(
                "cd -L -- sub && echo x > rel.txt",
                ("{cwd}/sub/rel.txt",),
                False,
                id="cd-logical-option-and-separator",
            ),
            pytest.param("cd && echo x > rel.txt", ("{home}/rel.txt",), False, id="bare-cd"),
            pytest.param("cd - && echo x > rel.txt", (), True, id="cd-oldpwd-unknown"),
            pytest.param("cd a b && echo x > rel.txt", (), True, id="cd-extra-operands"),
            pytest.param(
                'cd "$(git rev-parse --show-toplevel)" && echo x > out.txt',
                (),
                True,
                id="dynamic-cd-relative-write",
            ),
            pytest.param(
                'cd "$(git rev-parse --show-toplevel)" && echo x > /tmp/abs.txt',
                ("/tmp/abs.txt",),
                False,
                id="dynamic-cd-absolute-write",
            ),
            pytest.param(
                "pushd /tmp/d && echo x > rel.txt",
                ("/tmp/d/rel.txt",),
                False,
                id="pushd-ordinary-operand",
            ),
            pytest.param(
                "pushd /tmp/d; echo x > inside; popd; echo x > after",
                ("/tmp/d/inside",),
                True,
                id="popd-leaves-cwd-unresolved",
            ),
            pytest.param(
                "pushd /tmp/d && popd && echo x > rel.txt",
                (),
                True,
                id="pushd-then-popd-stack-unknown",
            ),
            pytest.param("pushd && echo x > rel.txt", (), True, id="bare-pushd-stack-unknown"),
            pytest.param("pushd +1 && echo x > rel.txt", (), True, id="pushd-stack-plus"),
            pytest.param("pushd -1 && echo x > rel.txt", (), True, id="pushd-stack-minus"),
            pytest.param("pushd -n && echo x > rel.txt", (), True, id="pushd-no-cd"),
            pytest.param(
                "CDPATH=/tmp/other; cd sub; echo x > rel.txt",
                (),
                True,
                id="cdpath-prior-assignment",
            ),
            pytest.param(
                "CDPATH=/tmp/other cd sub && echo x > rel.txt",
                (),
                True,
                id="cdpath-inline-assignment",
            ),
            pytest.param(
                "(cd /tmp/d; echo x > rel.txt)",
                ("/tmp/d/rel.txt",),
                False,
                id="subshell-cd-internal-write",
            ),
            pytest.param(
                "(cd /tmp/d); echo x > rel.txt",
                ("{cwd}/rel.txt",),
                False,
                id="subshell-cd-does-not-leak",
            ),
            pytest.param(
                "{ cd /tmp/d; echo x > a; }; echo y > b",
                ("/tmp/d/a", "/tmp/d/b"),
                False,
                id="brace-group-shares-cwd",
            ),
            pytest.param(
                "(cd /tmp/d; (cd sub; echo x > a); echo y > b)",
                ("/tmp/d/sub/a", "/tmp/d/b"),
                False,
                id="nested-subshell-cwd",
            ),
            pytest.param(
                "cd /tmp/d && (cd /; echo x > a); (echo y > b)",
                ("/a", "/tmp/d/b"),
                False,
                id="sibling-subshells-fork-outer-cwd",
            ),
            pytest.param(
                "cd /tmp/d; (cd /tmp/e; echo x > inside); echo x > after",
                ("/tmp/e/inside", "/tmp/d/after"),
                False,
                id="subshell-cwd-is-isolated",
            ),
            pytest.param("cd /tmp/d; echo x > /abs/f", ("/abs/f",), False, id="absolute-after-cd"),
            pytest.param(
                "cd /tmp/d; bash -c 'cd /; echo x > child'; echo y > outer",
                ("/child", "/tmp/d/outer"),
                False,
                id="child-shell-cd-does-not-leak",
            ),
            pytest.param(
                'cd /tmp/d; echo "$(cd /; echo x > child)"; echo y > outer',
                ("/child", "/tmp/d/outer"),
                False,
                id="command-substitution-cd-does-not-leak",
            ),
            pytest.param(
                'echo "$(echo x > before)"; cd /tmp/d; echo y > after',
                ("{cwd}/before", "/tmp/d/after"),
                False,
                id="substitution-uses-prior-cwd",
            ),
            pytest.param(
                "eval 'cd /tmp/d; echo x > inside'; echo x > after",
                ("/tmp/d/inside", "/tmp/d/after"),
                False,
                id="eval-updates-owner-cwd-before-next-command",
            ),
            pytest.param(
                "eval 'cd /tmp/d'; echo x > rel.txt",
                ("/tmp/d/rel.txt",),
                False,
                id="eval-cd-updates-owner-cwd",
            ),
            pytest.param(
                "bash -c 'cd /tmp/d; echo x > inside'; echo x > after",
                ("/tmp/d/inside", "{cwd}/after"),
                False,
                id="child-shell-payload-does-not-update-owner-cwd",
            ),
        ],
    )
    def test_cwd_and_payload_order(
        self,
        command: str,
        targets: tuple[str, ...],
        unresolved: bool,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cwd = str(tmp_path)
        home = str(tmp_path / "home")
        monkeypatch.setenv("HOME", home)
        expected_targets = tuple(
            target.replace("{cwd}", cwd).replace("{home}", home) for target in targets
        )
        assert command_classification.scan_write_targets(command, cwd) == (
            command_classification.WriteTargetScan(
                targets=expected_targets, unresolved=unresolved, parseable=True, has_write=True
            )
        )

    def test_nonempty_process_cdpath_leaves_relative_cd_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CDPATH", str(tmp_path / "other"))
        assert command_classification.scan_write_targets(
            "cd sub && echo x > rel.txt", str(tmp_path)
        ) == command_classification.WriteTargetScan((), True, True, True)

    @pytest.mark.parametrize(
        ("command", "targets", "unresolved"),
        [
            pytest.param(
                "git -C '/tmp/d' checkout -- 'report$2026.txt'",
                ("/tmp/d/report$2026.txt",),
                False,
                id="single-quoted-c-and-pathspec",
            ),
            pytest.param(
                'git -C "$ASK_WRITE_TARGET_DIR" checkout -- "rel.py"',
                ("/resolved/dir/rel.py",),
                False,
                id="double-quoted-c-expands",
            ),
            pytest.param(
                "git -C '$(pwd)' checkout -- rel.py",
                ("/work/$(pwd)/rel.py",),
                False,
                id="single-quoted-c-command-spelling",
            ),
            pytest.param(
                'git -C "$(pwd)" checkout -- rel.py',
                (),
                True,
                id="active-c-command-substitution",
            ),
            pytest.param(
                'git -C /tmp/d checkout -- "report$2026.txt"',
                (),
                True,
                id="active-pathspec-positional-expansion",
            ),
        ],
    )
    def test_git_c_and_pathspec_source_quoting(
        self,
        command: str,
        targets: tuple[str, ...],
        unresolved: bool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ASK_WRITE_TARGET_DIR", "/resolved/dir")

        assert command_classification.scan_write_targets(command, "/work") == (
            command_classification.WriteTargetScan(
                targets=targets, unresolved=unresolved, parseable=True, has_write=True
            )
        )


class TestExtractRedirectTargetsWithStatus:
    @pytest.mark.parametrize(
        ("command", "expected_targets"),
        [
            pytest.param(
                "(cmd > /tmp/err.log)",
                {"/tmp/err.log"},
                id="subshell-redirect",
            ),
            pytest.param(
                "x=$(cmd 2>/tmp/err.log) && echo done > /tmp/out.txt",
                {"/tmp/err.log", "/tmp/out.txt"},
                id="substitution-and-outer-redirects",
            ),
        ],
    )
    def test_real_command_redirect_targets(self, command: str, expected_targets: set[str]) -> None:
        scan = command_classification.scan_write_targets(command, "/tmp")

        assert set(scan.targets) == expected_targets
        assert scan.parseable is True
        assert scan.unresolved is False

    @pytest.mark.parametrize(
        "tokens,expected",
        [
            (["echo", "data", ">", "/tmp/out.txt"], ["/tmp/out.txt"]),
            (["cmd", "2>/dev/null"], ["/dev/null"]),
            (["cmd", ">>", "/tmp/log"], ["/tmp/log"]),
            (["cmd", ">", "relative.txt"], []),
            (["echo", "hello"], []),
            (["cmd", ">", "/tmp/a", "2>/tmp/b"], ["/tmp/a", "/tmp/b"]),
            (["cmd", "2>", "/tmp/err.log"], ["/tmp/err.log"]),
            (["cmd", ">", "/dev/null"], ["/dev/null"]),
            (["cmd", "2>&1"], []),
            (["cmd", ">", "&1"], []),
            (["cmd", "2>&1", ">", "/tmp/out"], ["/tmp/out"]),
        ],
        ids=[
            "separate_redirect",
            "merged_fd_redirect",
            "append_redirect",
            "non_absolute_path",
            "no_redirects",
            "multiple_redirects",
            "split_redirect",
            "pseudo_device_returned",
            "fd_dup_2_to_1_no_cwd",
            "fd_dup_split_ampersand_no_cwd",
            "fd_dup_with_real_redirect",
        ],
    )
    def test_extract_redirect_targets_with_status(self, tokens, expected):
        assert (
            extract_redirect_targets_with_status(tokens, redirect_syntax=[True] * len(tokens))[0]
            == expected
        )


class TestWriteTargetResolutionMatrix:
    @pytest.mark.parametrize(
        ("expansion_class", "raw_target", "expected"),
        _RESOLUTION_MATRIX,
        ids=[row[0] for row in _RESOLUTION_MATRIX],
    )
    def test_resolution_class(
        self,
        expansion_class: str,
        raw_target: str,
        expected: str | None,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = pwd.getpwuid(os.getuid())
        paths = {
            "{cwd}": str(tmp_path / "cwd"),
            "{env}": str(tmp_path / "env"),
            "{home}": str(tmp_path / "home"),
            "{user_home}": user.pw_dir,
        }
        monkeypatch.setenv("HOME", paths["{home}"])
        monkeypatch.setenv("ASK_RWT_DIR", paths["{env}"])
        monkeypatch.setenv("ASK_RWT_REF", "ASK_RWT_DIR")
        monkeypatch.delenv("ASK_RWT_UNSET", raising=False)
        raw_target = raw_target.replace("{user}", user.pw_name)
        if expected is not None:
            for marker, value in paths.items():
                expected = expected.replace(marker, value)

        assert (
            command_classification.resolve_write_target(
                raw_target, paths["{cwd}"], shell_source=raw_target
            )
            == expected
        ), expansion_class

    def test_matrix_covers_every_expansion_class(self) -> None:
        classes = {row[0] for row in _RESOLUTION_MATRIX}
        assert classes == _REQUIRED_EXPANSION_CLASSES
        assert len(_RESOLUTION_MATRIX) == len(classes)

    @pytest.mark.parametrize("value", ["$(echo/etc)", "`echo/etc`"])
    def test_environment_value_with_residual_expansion_is_unresolved(
        self, value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ASK_RWT_DIR", value)
        assert (
            command_classification.resolve_write_target(
                "$ASK_RWT_DIR/x", "/work", shell_source='"$ASK_RWT_DIR/x"'
            )
            is None
        )


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
            ("&1", "/workspace", "/workspace/&1"),
            ("&2", "/workspace", "/workspace/&2"),
            ("&-", "/workspace", "/workspace/&-"),
            ("&1", "", None),
            ("2>&1", "/workspace", "/workspace/2>&1"),
            (">&2", "/workspace", "/workspace/>&2"),
            ("1>&2", "/workspace", "/workspace/1>&2"),
            ("2>&-", "/workspace", "/workspace/2>&-"),
        ],
        ids=[
            "absolute_with_cwd",
            "relative_with_cwd",
            "relative_no_cwd",
            "dotslash_relative_with_cwd",
            "absolute_no_cwd",
            "empty_path_with_cwd",
            "empty_path_no_cwd",
            "literal_ampersand_1_with_cwd",
            "literal_ampersand_2_with_cwd",
            "literal_ampersand_close_with_cwd",
            "literal_ampersand_1_no_cwd",
            "literal_fd_spelling_2_to_1_with_cwd",
            "literal_fd_spelling_stdout_to_stderr_with_cwd",
            "literal_fd_spelling_1_to_2_with_cwd",
            "literal_fd_spelling_2_close_with_cwd",
        ],
    )
    def test_resolve_write_target(self, path, cwd, expected):
        from autoskillit.hooks._runtime._command_classification import resolve_write_target

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
            ({}, "report$2026.txt", "/workspace", None),
        ],
    )
    def test_resolve_write_target_shell_vars(self, env_setup, path, cwd, expected, monkeypatch):
        from autoskillit.hooks._runtime._command_classification import resolve_write_target

        for var in ["TEST_EXPAND_DIR", "REVIEW_OUTPUT_DIR", "NONEXISTENT_VAR", "NONEXISTENT"]:
            monkeypatch.delenv(var, raising=False)
        for var, val in env_setup.items():
            monkeypatch.setenv(var, val)
        assert resolve_write_target(path, cwd, shell_source=path) == expected

    @pytest.mark.parametrize(
        "path",
        ["report$2026.txt", "$(pwd)/out.txt", "$[1+2].txt", "${NAME:-fallback}.txt"],
    )
    def test_literal_api_path_preserves_expansion_spelling(self, path: str) -> None:
        from autoskillit.hooks._runtime._command_classification import resolve_write_target

        assert resolve_write_target(path, "/workspace") == f"/workspace/{path}"

    @pytest.mark.parametrize(
        ("dequoted_target", "source_span", "expected"),
        [
            pytest.param("$HOME/x", "'$HOME/x'", "/work/$HOME/x", id="single-quoted-var"),
            pytest.param("~/x", "'~/x'", "/work/~/x", id="single-quoted-tilde"),
            pytest.param("$HOME/x", '"$HOME/x"', "/home/write-target/x", id="double-quoted-var"),
            pytest.param("~/x", '"~/x"', "/work/~/x", id="double-quoted-tilde"),
            pytest.param("~/x", "~/x", "/home/write-target/x", id="unquoted-tilde"),
            pytest.param(
                "$(pwd)/out", "'$(pwd)/out'", "/work/$(pwd)/out", id="single-quoted-command"
            ),
            pytest.param("$(pwd)/out", r"\$(pwd)/out", None, id="escaped-command"),
            pytest.param("~/x", r"\~/x", "/work/~/x", id="escaped-tilde"),
        ],
    )
    def test_source_quote_provenance(
        self,
        dequoted_target: str,
        source_span: str,
        expected: str | None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autoskillit.hooks._runtime._command_classification import resolve_write_target

        monkeypatch.setenv("HOME", "/home/write-target")
        assert resolve_write_target(dequoted_target, "/work", shell_source=source_span) == expected


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
        assert (
            extract_redirect_targets_with_status(
                tokens, cwd, redirect_syntax=[True] * len(tokens)
            )[0]
            == expected
        )


class TestOutputRedirectPartition:
    def test_partition_indices_returns_dataclass_with_expected_fields(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _partition_output_redirect_indices,
        )

        tokens = ["cmd", ">/tmp/out"]
        result = _partition_output_redirect_indices(
            tokens, cwd="/work", redirect_syntax=[True] * len(tokens)
        )

        assert isinstance(result, OutputRedirectPartition)
        # Attribute access by field name (the dataclass's contract).
        assert result.segments == [0]
        assert result.targets == ["/tmp/out"]
        assert result.file_redirect_count == 1
        assert result.unresolved is False
        # Frozen + replace: a non-mutating replace produces a new instance
        # with identical field values (equality preserved, identity not).
        replaced = dataclasses.replace(result, segments=[0])
        assert replaced is not result
        assert replaced == result

    def test_partition_indices_unresolved_for_dynamic_shell_var_target(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _partition_output_redirect_indices,
        )

        # An unresolved shell variable keeps the redirect target unresolved.
        tokens = ["cmd", ">$OUT"]
        argv_tokens = [
            ArgvToken(text=token, fully_single_quoted=False, raw_span=token) for token in tokens
        ]
        result = _partition_output_redirect_indices(
            tokens,
            cwd="/work",
            redirect_syntax=[True] * len(tokens),
            argv_tokens=argv_tokens,
        )

        assert result.unresolved is True
        assert result.targets == []
        assert result.file_redirect_count == 1

    def test_partition_indices_unresolved_for_redirect_op_only_no_target(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _partition_output_redirect_indices,
        )

        # Bare op-only redirect with no following token: `_REDIRECT_OP_ONLY_RE`
        # matches `>`, the inner guard finds no next token, so `_consume_output_redirect`
        # returns `(next_index, None, 1)` — the caller flips `unresolved_target = True`
        # via the `elif file_redirect_delta:` branch.
        tokens = ["cmd", ">"]
        result = _partition_output_redirect_indices(
            tokens, cwd="/work", redirect_syntax=[True] * len(tokens)
        )

        assert result.unresolved is True
        assert result.targets == []
        assert result.file_redirect_count == 1

    def test_partition_redirects_external_signature_unchanged(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _partition_output_redirects,
        )

        # The public return shape is still a 3-tuple
        # `(executable_tokens, targets, file_redirect_count)` — the dataclass
        # migration is invisible to existing callers.
        tokens = ["cmd", ">/tmp/out"]
        result = _partition_output_redirects(
            tokens, cwd="/work", redirect_syntax=[True] * len(tokens)
        )

        assert isinstance(result, tuple)
        assert len(result) == 3
        executable_tokens, targets, file_redirect_count = result
        assert executable_tokens == ["cmd"]
        assert targets == ["/tmp/out"]
        assert file_redirect_count == 1

    def test_select_executable_argv_tokens_uses_dataclass(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _select_executable_argv_tokens,
        )

        tokens = ["cmd", ">/tmp/out"]
        argv_tokens = [ArgvToken(text=t, fully_single_quoted=False, raw_span=t) for t in tokens]
        result = _select_executable_argv_tokens(
            tokens, argv_tokens, cwd="/work", redirect_syntax=[True] * len(tokens)
        )

        # The redirect target is dropped from the projected argv even though
        # `_select_executable_argv_tokens` only consumes `partition.segments`.
        assert [t.text for t in result] == ["cmd"]

    def test_output_redirect_partition_is_frozen(self) -> None:
        partition = OutputRedirectPartition(
            segments=[0], targets=["/tmp/out"], file_redirect_count=1, unresolved=False
        )

        # Frozen: any field assignment raises.
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            partition.segments = [99]  # type: ignore[misc]

        # slots=True contract: no instance __dict__ — the per-instance dict cost
        # that motivated freezing.
        assert not hasattr(
            OutputRedirectPartition(
                segments=[], targets=[], file_redirect_count=0, unresolved=False
            ),
            "__dict__",
        )

    def test_extract_redirect_targets_with_status_uses_dataclass(self) -> None:
        # Resolved-target branch: `>/tmp/out` resolves cleanly.
        tokens = ["cmd", ">/tmp/out"]
        assert extract_redirect_targets_with_status(
            tokens, cwd="/work", redirect_syntax=[True] * len(tokens)
        ) == (
            ["/tmp/out"],
            False,
        )
        # Unresolved-target branch: `>$OUT` cannot resolve; empty targets,
        # `unresolved=True` propagates through the shim.
        tokens = ["cmd", ">$OUT"]
        argv_tokens = [
            ArgvToken(text=token, fully_single_quoted=False, raw_span=token) for token in tokens
        ]
        assert extract_redirect_targets_with_status(
            tokens,
            cwd="/work",
            redirect_syntax=[True] * len(tokens),
            argv_tokens=argv_tokens,
        ) == (
            [],
            True,
        )

    def test_partition_indices_empty_input(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _partition_output_redirect_indices,
        )

        result = _partition_output_redirect_indices([], cwd="/work", redirect_syntax=[])

        assert result == OutputRedirectPartition(
            segments=[], targets=[], file_redirect_count=0, unresolved=False
        )


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
    from autoskillit.hooks._runtime._command_classification import strip_heredoc_bodies

    assert strip_heredoc_bodies(command) == expected_stripped


class TestIsAllowedProtectedPathMetadataCommand:
    @staticmethod
    def _segment(command: str):
        segments = all_evaluated_segments_with_provenance(command)
        assert segments is not None
        assert len(segments) == 1
        return segments[0]

    def test_recognized_git_status_metadata_command_is_allowed(self) -> None:
        segment = self._segment("git status")
        assert is_allowed_protected_path_metadata_command(segment) is True

    def test_content_reading_git_subcommand_is_not_allowed(self) -> None:
        segment = self._segment("git show HEAD")
        assert is_allowed_protected_path_metadata_command(segment) is False

    def test_non_git_command_is_not_allowed(self) -> None:
        segment = self._segment("cat file.py")
        assert is_allowed_protected_path_metadata_command(segment) is False

    def test_unrecognized_global_git_flag_shift_is_not_allowed(self) -> None:
        """Git subcommand shift (Part C, wiring check for the first of

        extract_git_subcommand_and_flags's three callers): a real git
        global flag absent from the old allowlist, space-separated, must
        not shift subcommand detection onto the flag's value -- the
        "<unresolved>" sentinel must be treated identically to None
        (-> False), not accidentally matched against
        _PROTECTED_PATH_METADATA_GIT_SUBCOMMANDS.
        """
        known = self._segment("git --namespace refs/foo status")
        assert is_allowed_protected_path_metadata_command(known) is False
        # A genuinely unrecognized flag (not just one this allowlist predates)
        # must fail closed to False, not be silently treated as safe metadata.
        unknown = self._segment("git --this-flag-does-not-exist val status")
        assert is_allowed_protected_path_metadata_command(unknown) is False


class TestCommandHasBlockedProtectedPathRead:
    """Rectify #4941 Part B: reads through live_command_text/all_evaluated_segments.

    An inert heredoc/herestring body's mention of a protected path (prose,
    a fenced example) must not be blocked; a live SHELL/PYTHON/TEXT stdin
    body's genuine read of one must still be, even though a PYTHON/TEXT
    body's content is never its own argv segment.
    """

    _PATTERNS = [re.compile(r"src/autoskillit/recipes/foo\.yaml")]

    def test_inert_heredoc_body_mention_is_allowed(self) -> None:
        cmd = "cat > out.md <<'EOF'\nsee src/autoskillit/recipes/foo.yaml\nEOF"
        assert command_has_blocked_protected_path_read(cmd, self._PATTERNS) is False

    def test_bash_heredoc_stdin_read_is_blocked(self) -> None:
        cmd = "bash <<'EOF'\ncat src/autoskillit/recipes/foo.yaml\nEOF"
        assert command_has_blocked_protected_path_read(cmd, self._PATTERNS) is True

    def test_direct_cat_read_is_blocked(self) -> None:
        cmd = "cat src/autoskillit/recipes/foo.yaml"
        assert command_has_blocked_protected_path_read(cmd, self._PATTERNS) is True

    def test_python_stdin_open_read_is_blocked(self) -> None:
        cmd = "python3 - <<'EOF'\nopen('src/autoskillit/recipes/foo.yaml').read()\nEOF"
        assert command_has_blocked_protected_path_read(cmd, self._PATTERNS) is True

    def test_text_stdin_consumer_live_read_is_blocked(self) -> None:
        cmd = 'perl <<\'EOF\'\nopen(FH, "<", "src/autoskillit/recipes/foo.yaml")\nEOF'
        assert command_has_blocked_protected_path_read(cmd, self._PATTERNS) is True


_PROTECTED_RECIPE = "src/autoskillit/recipes/foo.yaml"
_PROTECTED_SKILL = "src/autoskillit/skills/foo/SKILL.md"
_PROTECTED_AGENT = "src/autoskillit/agents/foo.md"
_PROTECTED_SKILL_RESOURCE = "src/autoskillit/skill_resources/foo.md"


@pytest.mark.parametrize(
    "protected_path",
    [
        pytest.param(_PROTECTED_RECIPE, id="recipe"),
        pytest.param(_PROTECTED_SKILL, id="skill"),
        pytest.param(_PROTECTED_AGENT, id="agent"),
        pytest.param(_PROTECTED_SKILL_RESOURCE, id="skill-resource"),
    ],
)
def test_check_ignore_verbose_is_admitted_for_every_protected_path_category(
    protected_path: str,
) -> None:
    command = f"git check-ignore -v {protected_path}"
    assert not command_has_blocked_protected_path_read(command, PROTECTED_SOURCE_PATH_PATTERNS)


@pytest.mark.parametrize(
    "command",
    [
        f"git check-ignore --verbose --no-index -- {_PROTECTED_SKILL}",
        f"git check-ignore -v {_PROTECTED_RECIPE} {_PROTECTED_SKILL}",
        f"git check-ignore -v {_PROTECTED_RECIPE} || true",
        (
            f"git check-ignore -v {_PROTECTED_RECIPE} && "
            f"git check-ignore --verbose {_PROTECTED_SKILL}"
        ),
    ],
)
def test_check_ignore_metadata_forms_are_admitted(command: str) -> None:
    assert not command_has_blocked_protected_path_read(command, PROTECTED_SOURCE_PATH_PATTERNS)


_GIT_METADATA_ARMS = (
    f"add -- {_PROTECTED_RECIPE}",
    f"diff --stat -- {_PROTECTED_RECIPE}",
    f"diff --name-only -- {_PROTECTED_RECIPE}",
    f"status -- {_PROTECTED_RECIPE}",
    f"check-ignore -v {_PROTECTED_RECIPE}",
)

_GIT_GLOBAL_INJECTIONS = (
    "-c core.fsmonitor=./evil.sh",
    "-c core.excludesFile=./ignore",
    "-c include.path=./config",
    "--config-env=core.fsmonitor=EVIL",
    "--git-dir=./repo.git",
    "--work-tree=./tree",
    "--bare",
    "--namespace=evil",
    "--exec-path=./bin",
)


@pytest.mark.parametrize("arm", _GIT_METADATA_ARMS)
@pytest.mark.parametrize("global_option", _GIT_GLOBAL_INJECTIONS)
def test_git_metadata_arms_reject_unsafe_globals(arm: str, global_option: str) -> None:
    command = f"git {global_option} {arm}"
    assert command_has_blocked_protected_path_read(command, PROTECTED_SOURCE_PATH_PATTERNS)


@pytest.mark.parametrize("arm", _GIT_METADATA_ARMS)
@pytest.mark.parametrize(
    "prefix",
    [
        "env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=./evil.sh",
        f"env GIT_CONFIG_GLOBAL={_PROTECTED_RECIPE}",
        "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=./evil.sh",
        "env X=1",
        "sudo",
        "timeout 5",
    ],
)
def test_git_metadata_arms_reject_command_prefixes(arm: str, prefix: str) -> None:
    command = f"{prefix} git {arm}"
    assert command_has_blocked_protected_path_read(command, PROTECTED_SOURCE_PATH_PATTERNS)


@pytest.mark.parametrize("prefix", ["X=1", "env X=1", "sudo", "timeout 5"])
def test_wc_metadata_arm_rejects_command_prefixes(prefix: str) -> None:
    command = f"{prefix} wc -l {_PROTECTED_RECIPE}"
    assert command_has_blocked_protected_path_read(command, PROTECTED_SOURCE_PATH_PATTERNS)


@pytest.mark.parametrize(
    ("command", "blocked"),
    [
        (f"git -C /repo status -- {_PROTECTED_RECIPE}", False),
        (f"git -C /repo check-ignore -v {_PROTECTED_RECIPE}", True),
        (f"git -C /repo -c core.fsmonitor=./evil status -- {_PROTECTED_RECIPE}", True),
        (f"git --no-pager status -- {_PROTECTED_RECIPE}", False),
    ],
)
def test_git_metadata_global_controls(command: str, blocked: bool) -> None:
    assert (
        command_has_blocked_protected_path_read(command, PROTECTED_SOURCE_PATH_PATTERNS) is blocked
    )


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(
            f"git check-ignore -v {_PROTECTED_RECIPE} > out.txt",
            id="redirect provenance",
        ),
        pytest.param(
            f"git status -- {_PROTECTED_RECIPE} 2>&1",
            id="redirect provenance stderr",
        ),
        pytest.param(
            f"git add -- {_PROTECTED_RECIPE} 2>/dev/null",
            id="redirect provenance stderr-file",
        ),
        pytest.param(
            f"git status -- ${{P:-{_PROTECTED_RECIPE}}}",
            id="dynamic argv provenance",
        ),
        pytest.param(
            f'git status -- "${{P:-{_PROTECTED_RECIPE}}}"',
            id="dynamic argv provenance quoted",
        ),
        pytest.param(
            f"git status -- $(cat {_PROTECTED_RECIPE})",
            id="shell substitution",
        ),
        pytest.param(
            f"git status -- {_PROTECTED_RECIPE} && true $PROTECTED_VAR",
            id="shell state var chained",
        ),
        pytest.param(
            f"git check-ignore -v {_PROTECTED_RECIPE} --stdin",
            id="check-ignore stdin",
        ),
        pytest.param(
            f"git check-ignore -v -z {_PROTECTED_RECIPE}",
            id="check-ignore -z",
        ),
        pytest.param(
            f"git check-ignore -v -q {_PROTECTED_RECIPE}",
            id="check-ignore -q",
        ),
        pytest.param(
            f"git check-ignore -v -n {_PROTECTED_RECIPE}",
            id="check-ignore -n",
        ),
        pytest.param(
            f"git check-ignore -v --non-matching {_PROTECTED_RECIPE}",
            id="check-ignore --non-matching",
        ),
        pytest.param(
            f"git check-ignore -v --index {_PROTECTED_RECIPE}",
            id="check-ignore --index",
        ),
        pytest.param(
            f"git check-ignore -v {_PROTECTED_RECIPE} && cat {_PROTECTED_RECIPE}",
            id="mixed chain check-ignore then cat",
        ),
        pytest.param(
            f"git status -- {_PROTECTED_RECIPE} ; git show HEAD:{_PROTECTED_RECIPE}",
            id="mixed chain status then show",
        ),
        pytest.param(
            f"git check-ignore -v '{_PROTECTED_RECIPE}",
            id="malformed quoting",
        ),
        pytest.param(
            "python - <<'EOF'\n"
            f'import os; os.system("git check-ignore -v {_PROTECTED_RECIPE}")\n'
            "EOF",
            id="provenance-free evaluated payload",
        ),
        pytest.param(
            f"bash -c 'git check-ignore -v {_PROTECTED_RECIPE}'",
            id="bash -c check-ignore",
        ),
        pytest.param(
            f"python -c \"import os; os.system('git status -- {_PROTECTED_RECIPE}')\"",
            id="python -c status",
        ),
        pytest.param(
            f"git status -- \\${{P:-{_PROTECTED_RECIPE}}}",
            id="escaped dynamic argv",
        ),
    ],
)
def test_protected_path_admission_rejects_unproven_forms(command: str) -> None:
    assert command_has_blocked_protected_path_read(command, PROTECTED_SOURCE_PATH_PATTERNS)


def test_single_quoted_literal_protected_path_is_admitted() -> None:
    command = f"git status -- '{_PROTECTED_RECIPE}'"
    assert not command_has_blocked_protected_path_read(command, PROTECTED_SOURCE_PATH_PATTERNS)


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

    def test_continued_read_only_gh_api_has_exact_empty_analysis(self) -> None:
        command = "gh api repos/O/R/rulesets/13702255 \\\n--jq '.rules'"

        assert analyze_github_mutations(command) == GitHubMutationAnalysis(
            status=GitHubMutationStatus.NONE,
            mutations=(),
            request_count=0,
            review_comment_count=None,
            reason_code="",
            reason="",
        )

    @pytest.mark.parametrize(
        "command",
        [
            'for term in release; do gh search code "$term"; done',
            'while read -r sha; do gh search commits "$sha"; done',
            'until false; do gh search issues "is:open"; done',
            'if gh search prs "is:open"; then :; fi',
            'find_repos() { gh search repos "topic:cli"; }; find_repos',
            "cat <(gh pr view 7 --json number)",
            "for path in issues; do curl https://api.github.com/repos/o/r/issues; done",
        ],
        ids=[
            "for-search-code",
            "while-search-commits",
            "until-search-issues",
            "condition-search-prs",
            "function-search-repos",
            "process-substitution-pr-view",
            "for-curl-get",
        ],
    )
    def test_repeatable_read_only_commands_have_exact_empty_analysis(self, command: str) -> None:
        assert analyze_github_mutations(command) == GitHubMutationAnalysis(
            status=GitHubMutationStatus.NONE,
            mutations=(),
            request_count=0,
            review_comment_count=None,
            reason_code="",
            reason="",
        )

    @pytest.mark.parametrize(
        ("command", "expected_status"),
        [
            (
                "bash -c 'for number in 1 2; do gh pr view $number --json number; done'",
                GitHubMutationStatus.NONE,
            ),
            (
                "sh -c 'while read -r ref; do curl https://api.github.com/repos/o/r/issues; done'",
                GitHubMutationStatus.NONE,
            ),
            (
                "python3 -c \"import subprocess; subprocess.run(['gh','pr','view','7'])\"",
                GitHubMutationStatus.NONE,
            ),
            (
                "bash -c 'for number in 1 2; do gh pr merge $number; done'",
                GitHubMutationStatus.UNRESOLVED,
            ),
            (
                "sh -c 'until false; do curl -X PATCH "
                'https://api.github.com/repos/o/r/issues/7 -d "{}"; done\'',
                GitHubMutationStatus.UNRESOLVED,
            ),
            (
                "python3 -c \"import subprocess; subprocess.run(['gh','pr','merge','7'])\"",
                GitHubMutationStatus.SINGLE_RESOLVED,
            ),
        ],
        ids=[
            "bash-string-read-loop",
            "sh-string-read-loop",
            "literal-argv-read",
            "bash-string-write-loop",
            "sh-string-write-loop",
            "literal-argv-write",
        ],
    )
    def test_nested_and_literal_argv_commands_preserve_read_write_classification(
        self,
        command: str,
        expected_status: GitHubMutationStatus,
    ) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is expected_status
        if expected_status is GitHubMutationStatus.SINGLE_RESOLVED:
            assert analysis.request_count == 1
            assert [mutation.route for mutation in analysis.mutations] == ["/gh/pr/merge"]

    @pytest.mark.parametrize(
        ("command", "expected_status"),
        [
            (
                'for number in 1 2; do python3 -c "import subprocess; '
                "subprocess.run(['gh','pr','view','7'])\"; done",
                GitHubMutationStatus.NONE,
            ),
            (
                'for number in 1 2; do python3 -c "import subprocess; '
                "subprocess.run(['gh','pr','merge','7'])\"; done",
                GitHubMutationStatus.UNRESOLVED,
            ),
        ],
        ids=["read-only", "mutation"],
    )
    def test_repeatable_literal_argv_preserves_explicit_read_proof(
        self,
        command: str,
        expected_status: GitHubMutationStatus,
    ) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is expected_status
        if expected_status is GitHubMutationStatus.NONE:
            assert analysis.mutations == ()
        else:
            assert [mutation.route for mutation in analysis.mutations] == ["/gh/pr/merge"]

    @pytest.mark.parametrize(
        "command",
        [
            "for number in 1 2; do gh pr merge $number; done",
            "while read -r ref; do gh workflow frobnicate $ref; done",
            "until false; do curl -X PATCH https://api.github.com/repos/o/r/issues/7 "
            "-d '{}'; done",
            'if gh api --method "$METHOD" /repos/o/r/issues/7 -f title=x; then :; fi',
            "for number in 1 2; do printf '%s\\n' $number | xargs -n1 gh pr merge; done",
            "for term in release; do gh search issues $term; done; "
            "gh issue edit $ISSUE --title updated",
            "cat <(gh issue edit 7 --title updated)",
        ],
        ids=[
            "repeatable-mutation",
            "repeatable-unsupported-verb",
            "repeatable-curl-mutation",
            "condition-dynamic-method",
            "repeatable-delegated-command",
            "mixed-read-and-dynamic-mutation",
            "process-substitution-mutation",
        ],
    )
    def test_repeatable_or_ambiguous_github_commands_fail_closed(self, command: str) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.request_count is None
        assert analysis.reason_code

    @pytest.mark.parametrize(
        ("command", "expected_status"),
        [
            ("cat <(gh pr view 7", GitHubMutationStatus.UNRESOLVED),
            ("cat <(gh issue edit 7 --title updated", GitHubMutationStatus.UNRESOLVED),
            ("cat <(printf '%s' value", GitHubMutationStatus.NONE),
        ],
        ids=["read-only-github", "mutation-github", "unrelated-command"],
    )
    def test_malformed_process_substitution_only_fails_closed_for_github(
        self,
        command: str,
        expected_status: GitHubMutationStatus,
    ) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is expected_status
        if expected_status is GitHubMutationStatus.UNRESOLVED:
            assert analysis.reason_code == "shell_parse_unresolved"

    def test_process_substitution_uses_its_owning_segment_context(self, tmp_path: Path) -> None:
        (tmp_path / "payload.json").write_text("{}", encoding="utf-8")
        command = (
            f"cd {shlex.quote(str(tmp_path))} && "
            "cat <(gh api --method GET /repos/o/r/issues --input payload.json)"
        )

        assert analyze_github_mutations(command).status is GitHubMutationStatus.NONE

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
                # The process substitution is one word, so it does not obscure the mutation.
                GitHubMutationStatus.SINGLE_RESOLVED,
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

    def test_identical_command_substitution_payloads_are_counted_per_occurrence(self) -> None:
        nested = "gh api --method POST /repos/o/r/pulls/7/reviews -f event=COMMENT"

        analysis = analyze_github_mutations(f"echo $({nested}) && echo $({nested})")

        assert analysis.status is GitHubMutationStatus.MULTIPLE
        assert analysis.request_count == 2
        assert len(analysis.mutations) == 2

    def test_read_only_loop_does_not_make_adjacent_mutation_unresolved(self) -> None:
        command = (
            "for term in release; do gh search issues $term; done; gh issue edit 7 --title updated"
        )

        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.request_count == 1
        assert len(analysis.mutations) == 1
        assert analysis.mutations[0].kind is GitHubMutationKind.OTHER
        assert analysis.mutations[0].route == "/gh/issue/edit"

    def test_identical_nested_payloads_keep_per_occurrence_cwd(self, tmp_path: Path) -> None:
        (tmp_path / "payload.json").write_text(json.dumps({"body": "x"}), encoding="utf-8")
        nested = "gh api --method POST /repos/o/r/issues/7/comments --input payload.json"
        command = (
            f"cd {shlex.quote(str(tmp_path))} && $({nested}) && "
            f"cd {shlex.quote(str(tmp_path / 'missing'))} && $({nested})"
        )

        analysis = analyze_github_mutations(command, cwd=str(tmp_path))

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "unsafe_input_provenance"

    def test_nested_payload_uses_its_structural_segment_context(self, tmp_path: Path) -> None:
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps({"body": "x"}), encoding="utf-8")
        nested = f"gh api --method POST /repos/o/r/issues/7/comments --input {payload}"
        command = (
            f"echo {shlex.quote(nested)} && printf x > {payload} && bash -c {shlex.quote(nested)}"
        )

        analysis = analyze_github_mutations(command, cwd=str(tmp_path))

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "unsafe_input_provenance"

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
        ("redirect", "expected_status", "expected_reason_code"),
        [
            ("> different.out", GitHubMutationStatus.SINGLE_RESOLVED, ""),
            ("> payload.json", GitHubMutationStatus.UNRESOLVED, "unsafe_input_provenance"),
            ("> $OUT", GitHubMutationStatus.UNRESOLVED, "unsafe_input_provenance"),
        ],
        ids=["distinct", "same-path", "unresolved-target"],
    )
    def test_current_input_redirect_alias_safety(
        self,
        redirect: str,
        expected_status: GitHubMutationStatus,
        expected_reason_code: str,
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
        assert analysis.reason_code == expected_reason_code

    def test_current_input_redirect_rejects_hard_link_alias(self, tmp_path: Path) -> None:
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps({"body": "x"}), encoding="utf-8")
        alias = tmp_path / "alias.json"
        alias.hardlink_to(payload)

        analysis = analyze_github_mutations(
            f"gh api --method POST /repos/o/r/issues/7/comments --input {payload} > {alias}",
            cwd=str(tmp_path),
        )

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "unsafe_input_provenance"

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

    def test_parent_redirect_provenance_reaches_command_substitution_mutation(
        self,
        tmp_path: Path,
    ) -> None:
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps({"body": "x"}), encoding="utf-8")
        nested = f"gh api --method POST /repos/o/r/issues/7/comments --input {payload}"

        analysis = analyze_github_mutations(f"printf '%s' $({nested}) > {payload}")

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "unsafe_input_provenance"

    def test_unresolved_reason_codes_are_distinct_by_failure_family(self, tmp_path: Path) -> None:
        payload = tmp_path / "payload.json"
        payload.write_text(json.dumps({"body": "x"}), encoding="utf-8")
        analyses = {
            "dynamic target": analyze_github_mutations(
                "gh issue edit $ISSUE --title x"
            ).reason_code,
            "shell structure": analyze_github_mutations(
                "for x in 1 2; do gh issue edit 1 --title x; done"
            ).reason_code,
            "unsafe input provenance": analyze_github_mutations(
                f"python3 -c 'print(1)' && gh api --method POST /repos/o/r/issues/7/comments "
                f"--input {payload}"
            ).reason_code,
            "cwd": analyze_github_mutations("cd $DIR && gh issue edit 1 --title x").reason_code,
        }

        assert analyses == {
            "dynamic target": "dynamic_target",
            "shell structure": "shell_structure_unresolved",
            "unsafe input provenance": "unsafe_input_provenance",
            "cwd": "cwd_unresolved",
        }

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

    @pytest.mark.parametrize("flag", sorted(_GH_ISSUE_EDIT_REFERENCE_LIST_FLAGS))
    @pytest.mark.parametrize("form", ["separated", "equals", "csv", "repeated"])
    def test_issue_edit_relationship_flags_are_one_request(self, flag: str, form: str) -> None:
        if form == "separated":
            arguments = f"{flag} 4726"
        elif form == "equals":
            arguments = f"{flag}=4726"
        elif form == "csv":
            arguments = (
                f'{flag} "https://github.com/o/r/issues/4726,https://github.com/o/r/issues/4727"'
            )
        else:
            arguments = f"{flag} 4726 {flag} 4727"

        analysis = analyze_github_mutations(f"gh issue edit 4734 {arguments}")

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.request_count == 1
        assert len(analysis.mutations) == 1
        assert analysis.mutations[0].kind is GitHubMutationKind.OTHER

    @pytest.mark.parametrize(
        "command",
        [
            "gh issue edit 4734 --parent 4726",
            "gh issue edit 4734 --parent=https://github.com/o/r/issues/4726",
            "gh issue edit 4734 --type Bug",
            'gh issue edit 4734 --type=Bug --type="Bug, feature"',
        ],
        ids=["parent-number", "parent-url", "type-separated", "type-repeated-comma"],
    )
    def test_issue_edit_parent_and_type_flags_are_one_request(self, command: str) -> None:
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.request_count == 1

    @pytest.mark.parametrize("flag", ["--remove-milestone", "--remove-parent", "--remove-type"])
    @pytest.mark.parametrize("position", ["before", "after"])
    @pytest.mark.parametrize("target_count", [1, 2])
    def test_issue_edit_boolean_removal_flags_do_not_consume_target(
        self, flag: str, position: str, target_count: int
    ) -> None:
        targets = " ".join(str(4700 + offset) for offset in range(target_count))
        command = (
            f"gh issue edit {flag} {targets}"
            if position == "before"
            else f"gh issue edit {targets} {flag}"
        )

        analysis = analyze_github_mutations(command)

        if target_count == 1:
            assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
            assert analysis.request_count == 1
        else:
            assert analysis.status is GitHubMutationStatus.MULTIPLE
            assert analysis.request_count == target_count

    @pytest.mark.parametrize("flag", sorted(_GH_ISSUE_EDIT_REFERENCE_FLAGS))
    @pytest.mark.parametrize(
        "value",
        ["$RELATED", "${RELATED}", "$(echo 4726)"],
        ids=["bare-variable", "braced-variable", "command-substitution"],
    )
    @pytest.mark.parametrize("form", ["separated", "equals"])
    def test_issue_edit_reference_flags_reject_dynamic_values(
        self, flag: str, value: str, form: str
    ) -> None:
        argument = f"{flag} {value}" if form == "separated" else f"{flag}={value}"

        analysis = analyze_github_mutations(f"gh issue edit 4734 {argument}")

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.request_count is None
        assert analysis.reason_code == "dynamic_target"

    @pytest.mark.parametrize("flag", sorted(_GH_ISSUE_EDIT_REFERENCE_FLAGS))
    @pytest.mark.parametrize(
        "value",
        [
            "4726,",  # trailing empty CSV field
            ",4726",  # leading empty CSV field
            "foo",  # single non-numeric reference
            "4726,foo",  # mixed valid + invalid
            "12.5",  # decimal-looking but not isdecimal()
        ],
        ids=[
            "trailing-empty-field",
            "leading-empty-field",
            "single-non-numeric",
            "mixed-valid-and-invalid",
            "decimal-looking",
        ],
    )
    def test_issue_edit_reference_flags_reject_malformed_csv(self, flag: str, value: str) -> None:
        analysis = analyze_github_mutations(f"gh issue edit 4734 {flag}={value}")

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.request_count is None
        assert analysis.reason_code == "dynamic_target"

    @pytest.mark.parametrize("flag", sorted(_GH_ISSUE_EDIT_REFERENCE_FLAGS))
    @pytest.mark.parametrize(
        "command_template",
        [
            "gh issue edit 4734 {flag}",
            "gh issue edit 4734 {flag}=",
            "gh issue edit 4734 {flag} --title updated",
        ],
        ids=["missing", "empty-equals", "next-flag-is-not-a-value"],
    )
    def test_issue_edit_reference_flags_require_values(
        self, flag: str, command_template: str
    ) -> None:
        analysis = analyze_github_mutations(command_template.format(flag=flag))

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.request_count is None
        assert analysis.reason_code == "missing_required_value"

    @pytest.mark.parametrize("flag", sorted(_GH_ISSUE_EDIT_FLAG_SPEC))
    def test_every_issue_edit_spec_flag_is_recognized(self, flag: str) -> None:
        value = ""
        if _GH_ISSUE_EDIT_FLAG_SPEC[flag] == _FlagArity.VALUE:
            value = " 4726" if flag in _GH_ISSUE_EDIT_REFERENCE_FLAGS else " value"

        analysis = analyze_github_mutations(f"gh issue edit 4734 {flag}{value}")

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

    @pytest.mark.parametrize("selector", ["code", "commits", "issues", "prs", "repos"])
    def test_every_curated_search_selector_is_proven_non_mutating(self, selector: str) -> None:
        found, reason_code, reason, proven_non_mutating = (
            github_mutation_analysis._analyze_github_segment(
                ["gh", "search", selector, "release"],
                cwd="",
            )
        )

        assert found == []
        assert (reason_code, reason) == ("", "")
        assert proven_non_mutating is True

    @pytest.mark.parametrize(
        "tokens",
        [["gh", "search"], ["gh", "search", "pulls"]],
        ids=["missing-selector", "unknown-selector"],
    )
    def test_missing_or_unknown_search_selector_is_not_proven_non_mutating(
        self,
        tokens: list[str],
    ) -> None:
        found, reason_code, _reason, proven_non_mutating = (
            github_mutation_analysis._analyze_github_segment(tokens, cwd="")
        )

        assert found == []
        assert reason_code == "unsupported_grammar"
        assert proven_non_mutating is False
        assert analyze_github_mutations(" ".join(tokens)).status is GitHubMutationStatus.UNRESOLVED

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
        monkeypatch.delitem(github_mutation_analysis._GH_READ_ONLY_SUBCOMMANDS, "pr")

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

    @pytest.mark.parametrize(
        "command",
        [
            (
                "gh api graphql -f query='mutation { addReaction("
                'input: {subjectId: "abc", content: THUMBS_UP}) '
                "{ clientMutationId } }' --jq '.data.addReaction.clientMutationId'"
            ),
            "gh api /repos/o/r/issues -f title=Bug --template '{{.number}}'",
            "gh api /repos/o/r/issues -f title=Bug -p custom-preview",
        ],
        ids=["jq", "template", "preview"],
    )
    def test_previously_unrecognized_gh_api_flags_no_longer_misparse_route(
        self, command: str
    ) -> None:
        """AC1 reproduction (Issue #4655 Defect 1): --jq/--template/-p used to shift
        the flag's own value into the route slot, tripping a false
        request_cardinality_unresolved deny. The query bodies above deliberately
        avoid `$`/`[` so this test isolates Defect 1 from Part B's Defect 2 fix.
        """
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.request_count == 1
        assert analysis.reason_code == ""

    def test_unrecognized_gh_api_flag_has_a_distinguishable_reason_code(self) -> None:
        """An unrecognized gh api flag must fail closed with its own reason code,

        not the same request_cardinality_unresolved code a genuinely-ambiguous
        route triggers -- proving the fix is architectural (unknown flags get an
        honest, distinguishable outcome) rather than three more special-cased
        branches under the same misleading reason code.
        """
        analysis = analyze_github_mutations(
            "gh api /repos/o/r/issues --this-flag-does-not-exist zzz"
        )

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "unrecognized_gh_api_flag"
        assert analysis.reason_code != "request_cardinality_unresolved"

    @pytest.mark.parametrize(
        "command",
        [
            (
                "gh api /repos/o/r/issues -X POST -F count=1 -f note=hi "
                "--jq .id --template {{.id}} -p custom"
            ),
            (
                "gh api /repos/o/r/issues --method=POST --field=count=1 "
                "--raw-field=note=hi --jq=.id --template={{.id}} --preview=custom"
            ),
            "gh api /repos/o/r/issues -XPOST -Fcount=1 -fnote=hi -q.id -t{{.id}} -pcustom",
        ],
        ids=FLAG_FORM_MATRIX,
    )
    def test_gh_api_value_flag_grammar_resolves_identically_across_forms(
        self, command: str
    ) -> None:
        """Extends the gh-issue-edit 5-form precedent to gh api's value-taking

        flags. --method/-X, --field/-F, and --raw-field/-f already supported all
        three forms via _consume_argv_flag (untested before this); --jq/-q, --template/-t,
        and -p/--preview are newly recognized by this part's spec-driven engine.
        """
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.request_count == 1
        assert analysis.mutations[0].method == "POST"

    @pytest.mark.parametrize("form", ["space", "equals"])
    def test_gh_api_input_flag_supports_space_and_equals_forms(
        self, form: str, tmp_path: Path
    ) -> None:
        (tmp_path / "body.json").write_text('{"title": "Bug"}', encoding="utf-8")
        flag = "--input body.json" if form == "space" else "--input=body.json"
        analysis = analyze_github_mutations(
            f"gh api /repos/o/r/issues --method POST {flag}",
            cwd=str(tmp_path),
        )

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.request_count == 1

    def test_fully_single_quoted_graphql_document_resolves(self) -> None:
        """AC2/AC3 reproduction (Issue #4655 Defect 2): a GraphQL mutation document

        delivered inline via `-f query='...'`, fully single-quoted end-to-end,
        legitimately contains `$`/`[` (GraphQL variables, list literals) --
        this must resolve, not be misclassified as a dynamic shell value.
        """
        command = (
            "gh api graphql -f query='mutation($id: ID!) "
            "{ addLabels(labelIds: [$id]) { clientMutationId } }'"
        )
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.request_count == 1
        assert analysis.reason_code == ""

    @pytest.mark.parametrize(
        "suffix",
        [
            ";echo done",
            "|tee /tmp/x",
            "&echo done",
        ],
        ids=["semicolon", "pipe", "ampersand"],
    )
    def test_single_quoted_graphql_followed_by_unseparated_shell_operator_resolves(
        self, suffix: str
    ) -> None:
        """Regression pin for _SHELL_OPERATOR_CHARS strip introduced for the

        AC2/AC3 fix above: a fully single-quoted GraphQL document immediately
        followed by an unseparated shell operator (`, |`, `&`) must still
        resolve. shlex.shlex(punctuation_chars=True) leaves the operator in
        the previous token's source range; without the operator-chars strip
        `fully_single_quoted` would mismatch `'<token>'` and the legitimate
        mutation would be wrongly denied as dynamic_target.
        """
        command = (
            "gh api graphql -f query='mutation($id: ID!) "
            "{ addLabels(labelIds: [$id]) { clientMutationId } }'" + suffix
        )
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.request_count == 1
        assert analysis.reason_code == ""

    def test_unquoted_command_substitution_in_graphql_document_remains_denied(self) -> None:
        """AC4 regression pin: the prior investigation explicitly warned a blanket

        `$`/`[` relaxation would let a real command-substitution fragment through
        -- this must remain denied as a first-class test, not an afterthought.
        """
        command = (
            'gh api graphql -f query="mutation($id: ID!) '
            '{ addLabels(labelIds: [`whoami`]) { clientMutationId } }"'
        )
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "dynamic_target"

    def test_identical_graphql_content_denied_when_not_single_quoted(self) -> None:
        """Provenance-not-content proof: the exact same `$`/`[` GraphQL content,

        delivered double-quoted instead of single-quoted, must remain denied --
        proving the fix is provenance-based (quote type of the token), not a
        character-set relaxation applied to GraphQL content generally.
        """
        command = (
            'gh api graphql -f query="mutation($id: ID!) '
            '{ addLabels(labelIds: [$id]) { clientMutationId } }"'
        )
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "dynamic_target"

    def test_partial_single_quote_run_is_not_fully_quoted(self) -> None:
        """A single-quote run that closes and reopens concatenated with a

        double-quoted segment must remain denied, proving "fully single-quoted
        end-to-end" is enforced precisely -- not "starts with a quote character".
        """
        command = "gh api graphql -f query='mutation'\"($id)\""
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "dynamic_target"

    @pytest.mark.parametrize("content", GRAPHQL_CONTENT_MATRIX)
    @pytest.mark.parametrize("delivery", GRAPHQL_DELIVERY_MATRIX)
    def test_graphql_delivery_content_matrix(
        self, delivery: str, content: str, tmp_path: Path
    ) -> None:
        """REQ-065: the full delivery x content cross product the four

        individually-named tests above (single-quoted-resolves,
        command-substitution-denied, double-quoted-denied,
        partial-quote-denied) each only sampled one cell of. Resolution
        depends only on whether *delivery* is inherently safe (fully
        single-quoted, or file content) or *content* has no dynamic-shell
        trigger character at all -- see
        graphql_delivery_is_inherently_safe's own docstring for the exact
        rule. Every other cell must deny with reason_code ==
        "dynamic_target".
        """
        command = deliver_graphql_document(
            delivery, GRAPHQL_MATRIX_CONTENT_BODIES[content], tmp_path
        )
        analysis = analyze_github_mutations(command, cwd=str(tmp_path))

        if graphql_delivery_is_inherently_safe(delivery, content):
            assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED, (
                f"{delivery}/{content} unexpectedly unresolved: {analysis.reason_code}"
            )
            assert analysis.reason_code == ""
        else:
            assert analysis.status is GitHubMutationStatus.UNRESOLVED
            assert analysis.reason_code == "dynamic_target"

    @pytest.mark.parametrize(
        "command",
        [
            "gh api /repos/o/r/issues -f title=Bug -X 'POST'",
            "gh api /repos/o/r/issues -f title=Bug --method='POST'",
            "gh api /repos/o/r/issues -f title=Bug -X'POST'",
        ],
        ids=FLAG_FORM_MATRIX,
    )
    def test_method_provenance_proof_single_quoted_resolves(self, command: str) -> None:
        """--method/-X provenance proof (not GraphQL content): a genuinely

        single-quoted method value must resolve -- proving the
        _consume_argv_flag retyping is real provenance tracing, not
        a fabricated ArgvToken(value, True) default, since none of the
        GraphQL-focused tests above exercise the --method/-X extraction path
        at all.
        """
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED
        assert analysis.mutations[0].method == "POST"

    @pytest.mark.parametrize(
        "command",
        [
            "gh api /repos/o/r/issues -f title=Bug -X POST$(whoami)",
            "gh api /repos/o/r/issues -f title=Bug --method=POST$(whoami)",
            "gh api /repos/o/r/issues -f title=Bug -XPOST$(whoami)",
        ],
        ids=FLAG_FORM_MATRIX,
    )
    def test_method_provenance_proof_unquoted_substitution_denied(self, command: str) -> None:
        """Companion to the single-quoted proof above: the same forms, but with

        a real command-substitution fragment appended inside the same
        quoting (none here), must remain denied.
        """
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "dynamic_target"

    @pytest.mark.parametrize(
        "command",
        [
            "gh api /repos/o/r/issues -f title=Bug -X 'PO$ST'",
            "gh api /repos/o/r/issues -f title=Bug --method='PO$ST'",
            "gh api /repos/o/r/issues -f title=Bug -X'PO$ST'",
        ],
        ids=FLAG_FORM_MATRIX,
    )
    def test_method_provenance_proof_quoted_dynamic_looking_content_not_denied(
        self, command: str
    ) -> None:
        """Deep provenance proof: a `$` appearing *inside* a fully single-quoted

        method value must not trigger the dynamic-value check in any form --
        the equals-form and bundled-form retyping must re-derive the value's
        own quote provenance (see _argv_token_after_prefix), not inherit the
        whole token's coarser flag, or a `--method=`/`-X` prefix sitting
        outside the quotes would wrongly disqualify an otherwise safely
        quoted value.
        """
        analysis = analyze_github_mutations(command)

        assert analysis.reason_code != "dynamic_target"

    @pytest.mark.parametrize(
        "command",
        [
            "gh api /repos/o/r/issues -f title=Bug -X PO$ST",
            "gh api /repos/o/r/issues -f title=Bug --method=PO$ST",
            "gh api /repos/o/r/issues -f title=Bug -XPO$ST",
        ],
        ids=FLAG_FORM_MATRIX,
    )
    def test_method_provenance_proof_unquoted_dynamic_content_denied(self, command: str) -> None:
        """Companion regression pin: the identical `$`-bearing content, not

        quoted at all, must still deny in every form.
        """
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "dynamic_target"

    def test_gh_release_notes_help_spoof_value_does_not_exempt_mutation(self) -> None:
        """Help-spoof bypass (Part C): --notes is not a curated value-taking

        flag in _GH_KNOWN_VALUE_FLAGS, so under the old "assume boolean,
        advance 1" default, its own value '--help' would be misread as a
        bare help flag on the next iteration -- incorrectly exempting the
        whole command from mutation classification (the code's own comment
        already named this spoofing risk as the reason the table exists;
        the table was simply incomplete).
        """
        analysis = analyze_github_mutations("gh release create v1 --notes '--help'")

        assert analysis.status is GitHubMutationStatus.SINGLE_RESOLVED

    def test_unrecognized_boolean_flag_before_real_help_still_exempts(self) -> None:
        """The default-arity flip's trade-off is one-directional and safe: a

        genuinely-boolean unrecognized flag immediately followed by a real
        --help still classifies as non-mutating (reached via normal
        classification rather than the help-fast-path -- a missed
        shortcut, never a bypass).
        """
        analysis = analyze_github_mutations("gh pr view --web --help")

        assert analysis.status is GitHubMutationStatus.NONE

    def test_curl_user_agent_flag_does_not_cause_false_cardinality_unresolved(self) -> None:
        """curl overblock regression pin (Part C): -A/--user-agent is outside

        curl's previously-covered value_flags tuple; its value must not be
        misread as a second URL and denied as request_cardinality_unresolved.
        """
        analysis = analyze_github_mutations(
            'curl -A "custom-agent/1.0" https://api.github.com/repos/x/y'
        )

        assert analysis.reason_code != "request_cardinality_unresolved"

    def test_unrecognized_curl_flag_has_a_distinguishable_reason_code(self) -> None:
        """curl's generalized rewire fails closed on a truly unrecognized flag,

        matching gh api's Part A precedent -- distinguishable from
        request_cardinality_unresolved.
        """
        analysis = analyze_github_mutations(
            "curl --this-curl-flag-does-not-exist zzz https://api.github.com/repos/x/y"
        )

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "unrecognized_curl_flag"
        assert analysis.reason_code != "request_cardinality_unresolved"

    @pytest.mark.parametrize("flag", sorted(_GH_API_FLAG_SPEC))
    def test_every_gh_api_spec_flag_is_recognized(self, flag: str) -> None:
        """Generative coverage (Part D): every flag in _GH_API_FLAG_SPEC must

        be recognized by _analyze_gh_api's engine, not fall through to
        unrecognized_gh_api_flag. Parametrized directly from the spec
        table's own keys (not a hand-maintained flag list) so this test can
        never silently miss a flag added to the table later -- see
        tests/arch/test_guard_flag_spec_coverage.py, which verifies this
        parametrize genuinely iterates the full table.

        -h/--help are routed through a command construction that places them
        after an unrecognized `-`-prefixed token so _gh_args_have_bare_help_flag
        treats them as that token's value and does NOT short-circuit
        analyze_github_mutations before the spec engine sees them. Otherwise
        the assertion below would vacuously pass for -h/--help because the
        help exemption returns reason_code="" rather than exercising the spec
        table at all.
        """
        if flag in _GH_HELP_FLAGS:
            # Construct a command where -h/--help is preceded by an
            # unrecognized `-`-prefixed token so _gh_args_have_bare_help_flag
            # classifies -h as that token's value (not a bare help), forcing
            # analyze_github_mutations down the spec-engine path this test
            # is meant to cover.
            command = f"gh api /repos/o/r/issues --header=val {flag}"
        else:
            command = f"gh api /repos/o/r/issues -f title=Bug {flag}"
            if _GH_API_FLAG_SPEC[flag] == _FlagArity.VALUE:
                command += " someval"
        analysis = analyze_github_mutations(command)

        assert analysis.reason_code != "unrecognized_gh_api_flag"

    @pytest.mark.parametrize("flag", sorted(_CURL_FLAG_SPEC))
    def test_every_curl_spec_flag_is_recognized(self, flag: str) -> None:
        """Generative coverage (Part D): every flag in _CURL_FLAG_SPEC must be

        recognized by _analyze_curl_segment's engine, not fall through to
        unrecognized_curl_flag.
        """
        command = f"curl {flag}"
        if _CURL_FLAG_SPEC[flag] == _FlagArity.VALUE:
            command += " someval"
        command += " https://api.github.com/repos/o/r"
        analysis = analyze_github_mutations(command)

        assert analysis.reason_code != "unrecognized_curl_flag"

    @pytest.mark.parametrize(
        "command",
        [
            "gh api -H",
            "gh api /route -H",
            "gh api /route --header",
            "gh api /route --hostname",
            "gh api /route --cache",
        ],
    )
    def test_gh_api_value_flag_without_value_returns_missing_required_value(
        self, command: str
    ) -> None:
        """Regression pin for the spec-engine dedup (post-validator follow-up):

        dropping the gh api hand-rolled -H/--header/--hostname/--cache skip
        logic in favor of the spec table engine lost the explicit
        missing_required_value return -- _consume_argv_flag returns (None,
        i+1, True) for VALUE-arity with no next token, indistinguishable from
        BOOLEAN. The call site now re-checks the spec table to surface the
        distinct missing-value error rather than silently passing.
        """
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "missing_required_value"

    @pytest.mark.parametrize(
        "command",
        [
            "curl --user-agent",
            "curl --proxy",
            "curl --cacert",
            "curl --connect-timeout",
        ],
    )
    def test_curl_value_flag_without_value_returns_missing_required_value(
        self, command: str
    ) -> None:
        """Regression pin for the spec-engine dedup (post-validator follow-up):

        the curl call site must surface missing-value for VALUE-arity flags
        similarly to gh api, otherwise `curl --proxy` (no value) silently
        passes _analyze_curl_segment's unrecognized-flag check and escapes
        as a missing-required-value smell.
        """
        analysis = analyze_github_mutations(command)

        assert analysis.status is GitHubMutationStatus.UNRESOLVED
        assert analysis.reason_code == "missing_required_value"


@pytest.mark.parametrize("flag", sorted(_GIT_GLOBAL_FLAG_SPEC))
def test_every_git_global_spec_flag_is_recognized(flag: str) -> None:
    """Generative coverage (Part D): every flag in _GIT_GLOBAL_FLAG_SPEC must

    be recognized by extract_git_subcommand_and_flags, not fall through to
    the "<unresolved>" fail-closed sentinel.
    """
    segment = ["git", flag]
    if _GIT_GLOBAL_FLAG_SPEC[flag] == _FlagArity.VALUE:
        segment.append("someval")
    segment.append("status")

    result = extract_git_subcommand_and_flags(segment)

    assert result is not None
    assert result.subcommand != "<unresolved>"


class TestStructuralHeredocShapes:
    """Regressions for structural heredoc delimiter and body handling."""

    def _assert_single_inert_cat_literal(self, command: str, expected_body: str) -> None:
        segments = command_classification._tokenize_command_segments_with_redirects(command)
        assert len(segments) == 1
        assert segments[0].tokens == ["cat"]
        assert [literal.text for literal in segments[0].stdin_literals] == [expected_body]

    def test_two_heredocs_one_line(self) -> None:
        inner = "git push --force origin main"
        cmd = f"cat <<'A' <<'B'\nignored\nA\n{inner}\nB\n"
        segments = command_classification._tokenize_command_segments_with_redirects(cmd)
        assert len(segments) == 1
        assert segments[0].tokens == ["cat"]
        assert [literal.text for literal in segments[0].stdin_literals] == ["ignored", inner]
        assert [literal.feeds_stdin for literal in segments[0].stdin_literals] == [False, True]
        assert all(literal.kind == "heredoc" for literal in segments[0].stdin_literals)

    def test_backslash_quoted_delimiter(self) -> None:
        inner = "git push --force origin main"
        cmd = f"cat <<\\EOF\n{inner}\nEOF\n"
        self._assert_single_inert_cat_literal(cmd, inner)
        assert (
            command_classification._tokenize_command_segments_with_redirects(cmd)[0]
            .stdin_literals[0]
            .outer_expansion
            is False
        )

    def test_partially_quoted_delimiter(self) -> None:
        inner = "git push --force origin main"
        cmd = f'cat <<E"OF"\n{inner}\nEOF\n'
        self._assert_single_inert_cat_literal(cmd, inner)
        assert (
            command_classification._tokenize_command_segments_with_redirects(cmd)[0]
            .stdin_literals[0]
            .outer_expansion
            is False
        )

    def test_unterminated_heredoc(self) -> None:
        inner = "git push --force origin main"
        cmd = f"cat <<'EOF'\n{inner}\n"
        self._assert_single_inert_cat_literal(cmd, inner)
        literal = command_classification._tokenize_command_segments_with_redirects(cmd)[
            0
        ].stdin_literals[0]
        assert literal.source_span is not None
        assert cmd[literal.source_span[0] : literal.source_span[1]] == inner

    def test_non_word_delimiter(self) -> None:
        inner = "git push --force origin main"
        cmd = f"cat <<'END-DOC'\n{inner}\nEND-DOC\n"
        self._assert_single_inert_cat_literal(cmd, inner)
        assert (
            command_classification._tokenize_command_segments_with_redirects(cmd)[0]
            .stdin_literals[0]
            .outer_expansion
            is False
        )

    def test_same_line_heredocs_bind_to_their_pipeline_segments(self) -> None:
        command = "cat <<A | bash <<B\nleft\nA\nright\nB\n"
        segments = command_classification._tokenize_command_segments_with_redirects(command)
        assert [segment.tokens for segment in segments] == [["cat"], ["bash"]]
        assert [literal.text for literal in segments[0].stdin_literals] == ["left"]
        assert [literal.text for literal in segments[1].stdin_literals] == ["right"]

    def test_only_final_heredoc_feeds_cat_pipeline(self) -> None:
        command = "cat <<A <<B | bash\nignored\nA\ngit push --force origin main\nB\n"
        segments = command_classification._tokenize_command_segments_with_redirects(command)
        assert [segment.tokens for segment in segments] == [["cat"], ["bash"]]
        assert [literal.text for literal in segments[0].stdin_literals] == [
            "ignored",
            "git push --force origin main",
        ]
        assert [literal.feeds_stdin for literal in segments[0].stdin_literals] == [False, True]
        payloads = evaluated_payloads(command)
        assert [
            (payload.origin, payload.text) for payload in payloads if payload.origin == "pipe"
        ] == [("pipe", "git push --force origin main")]
        assert _GIT_PUSH_FORCE_ARGV in [
            command_verb_and_args(segment) for segment in all_evaluated_segments(command) or []
        ]

    def test_only_final_heredoc_feeds_python(self) -> None:
        command = (
            "python3 - <<A <<B\n"
            "import subprocess; subprocess.run(['git', 'push'])\n"
            "A\n"
            "print('safe')\n"
            "B\n"
        )
        segments = command_classification._tokenize_command_segments_with_redirects(command)
        assert [segment.tokens for segment in segments] == [["python3", "-"]]
        assert [literal.text for literal in segments[0].stdin_literals] == [
            "import subprocess; subprocess.run(['git', 'push'])",
            "print('safe')",
        ]
        assert [literal.feeds_stdin for literal in segments[0].stdin_literals] == [False, True]
        assert interpreter_invokes(command, target=("git", "push")) is False

    def test_herestring_overrides_heredoc_but_keeps_outer_expansion(self) -> None:
        command = "bash <<A <<< 'echo safe'\n$(git push --force origin main)\nA\n"
        segments = command_classification._tokenize_command_segments_with_redirects(command)
        assert [literal.feeds_stdin for literal in segments[0].stdin_literals] == [False, True]
        substitutions = [
            payload.text
            for payload in evaluated_payloads(command)
            if payload.origin == "substitution"
        ]
        assert substitutions == ["git push --force origin main"]

    @pytest.mark.parametrize(
        "command",
        [
            "echo ok # <<EOF\nbody\nEOF\necho after\n",
            "echo $(( 1 << 2 ))\necho after\n",
        ],
        ids=["comment", "arithmetic"],
    )
    def test_non_redirection_double_less_than_does_not_capture_tail(self, command: str) -> None:
        assert command_classification.strip_heredoc_bodies(command) == command


class TestSiblingWrappersDelegate:
    """Smoke tests for the sibling wrappers in _github_mutation_analysis.

    Each wrapper should produce the same result as its _command_classification
    counterpart, since the wrappers exist only to defer the import past the
    module-load circular boundary.
    """

    def test_command_verb_and_args_delegates(self) -> None:
        from autoskillit.hooks._runtime._command_classification import command_verb_and_args
        from autoskillit.hooks._runtime._github_mutation_analysis import _command_verb_and_args

        seg = ["env", "FOO=bar", "gh", "pr", "view"]
        assert _command_verb_and_args(seg) == command_verb_and_args(list(seg))

    def test_tokenize_with_redirects_delegates(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _tokenize_command_segments_with_redirects,
        )
        from autoskillit.hooks._runtime._github_mutation_analysis import _tokenize_with_redirects

        # _CommandSegment instances from the bare-name-loaded copy and the
        # package-loaded copy do not share class identity, so compare token
        # lists and redirect-syntax flags element-wise.
        command = "gh pr view --json state"
        wrapper_result = _tokenize_with_redirects(command)
        direct_result = _tokenize_command_segments_with_redirects(command)
        assert len(wrapper_result) == len(direct_result)
        for wrapper_seg, direct_seg in zip(wrapper_result, direct_result, strict=True):
            assert wrapper_seg.tokens == direct_seg.tokens
            assert wrapper_seg.redirect_syntax == direct_seg.redirect_syntax

    def test_normalize_executable_call_delegates(self) -> None:
        from autoskillit.hooks._runtime._command_classification import _normalize_executable
        from autoskillit.hooks._runtime._github_mutation_analysis import _normalize_executable_call

        assert _normalize_executable_call("/usr/bin/gh") == _normalize_executable("/usr/bin/gh")

    def test_partition_output_redirects_call_delegates(self) -> None:
        from autoskillit.hooks._runtime._command_classification import _partition_output_redirects
        from autoskillit.hooks._runtime._github_mutation_analysis import (
            _partition_output_redirects_call,
        )

        tokens = ["gh", "pr", "view", ">", "/tmp/out"]
        assert _partition_output_redirects_call(
            tokens, cwd="/work", redirect_syntax=[True] * len(tokens)
        ) == _partition_output_redirects(tokens, cwd="/work", redirect_syntax=[True] * len(tokens))

    @staticmethod
    def _spec_tuples(specs):
        # _InterpreterCommandSpec is a plain dataclass (not a StrEnum), so its
        # auto-generated __eq__ checks class identity first; the module-boundary
        # (bare-name vs. dotted) dual-load this delegation test crosses can bind
        # two distinct class objects for the same fields, making direct `==`
        # unreliable. Compare field tuples instead, mirroring the `==`-not-`is`
        # discipline _FlagArity's docstring already documents for this hazard.
        return [(spec.payload, spec.cwd, spec.invokes_shell) for spec in specs]

    def test_extract_interpreter_segment_specs_call_delegates(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            StdinLiteral,
            _extract_interpreter_segment_specs,
        )
        from autoskillit.hooks._runtime._github_mutation_analysis import (
            _extract_interpreter_segment_specs_call,
        )

        segment = ["python3", "-c", "print(1)"]
        called_specs, called_unresolved = _extract_interpreter_segment_specs_call(segment)
        direct_specs, direct_unresolved = _extract_interpreter_segment_specs(segment)
        assert self._spec_tuples(called_specs) == self._spec_tuples(direct_specs)
        assert called_unresolved == direct_unresolved

        # The wrapper forwards the keyword-only stdin_literals parameter so a
        # heredoc/herestring-fed `python3 -` program is inspected the same
        # way an inline `-c` program is (rectify #4941 Part A).
        heredoc_literals = (
            StdinLiteral(
                text="import subprocess; subprocess.run(['git','push'])",
                kind="heredoc",
                outer_expansion=False,
            ),
        )
        stdin_segment = ["python3", "-"]
        called_specs, called_unresolved = _extract_interpreter_segment_specs_call(
            stdin_segment, stdin_literals=heredoc_literals
        )
        direct_specs, direct_unresolved = _extract_interpreter_segment_specs(
            stdin_segment, stdin_literals=heredoc_literals
        )
        expected = [(["git", "push"], None, False)]
        assert self._spec_tuples(called_specs) == expected
        assert self._spec_tuples(direct_specs) == expected
        assert called_unresolved is False
        assert direct_unresolved is False

    def test_command_position_candidate_spans_call_delegates(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _command_position_candidate_spans,
        )
        from autoskillit.hooks._runtime._github_mutation_analysis import (
            _command_position_candidate_spans_call,
        )

        segment = ["inspect()", "{", "gh", "pr", "view"]
        assert _command_position_candidate_spans_call(
            segment
        ) == _command_position_candidate_spans(segment)

    def test_process_substitution_occurrences_call_delegates(self) -> None:
        from autoskillit.hooks._runtime._command_classification import (
            _extract_process_substitution_occurrences,
        )
        from autoskillit.hooks._runtime._github_mutation_analysis import (
            _extract_process_substitution_occurrences_call,
        )

        command = "cat <(gh pr view 7)"
        assert _extract_process_substitution_occurrences_call(
            command
        ) == _extract_process_substitution_occurrences(command)

    def test_evaluated_payloads_call_delegates(self) -> None:
        from autoskillit.hooks._runtime._command_classification import evaluated_payloads
        from autoskillit.hooks._runtime._github_mutation_analysis import _evaluated_payloads_call

        # EvaluatedPayload is a plain dataclass; compare field tuples rather
        # than instances directly for the same cross-module class-identity
        # reason documented on _spec_tuples above.
        def _payload_tuples(payloads):
            return [
                (p.text, str(p.kind), p.consumer_index, p.origin, p.source_span) for p in payloads
            ]

        command = 'bash -c "pip install -e ."'
        assert _payload_tuples(_evaluated_payloads_call(command)) == _payload_tuples(
            evaluated_payloads(command)
        )
