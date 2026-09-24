"""Shared command classification primitives for guard scripts.

Supported interpreters: python3?, perl, ruby, node.
To add coverage for a new interpreter, update both _INTERPRETER_RE and
_INTERPRETER_LINE_RE.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from autoskillit.hooks._classification._tokenizer import (  # noqa: F401
        ArgvToken,
        EvaluatedSegment,
        StdinLiteral,
        _CommandSegment,
        _normalize_newlines_for_tokenize,
        _tokenize_command_segments_with_redirects,
    )
    from autoskillit.hooks._classification._tokenizer import (
        strip_heredoc_bodies as _strip_heredoc_bodies_impl,
    )
    from autoskillit.hooks._classification._tokenizer import (
        tokenize_command_segments as _tokenize_command_segments_impl,
    )
else:
    if __package__:
        from .._classification import _tokenizer
    else:
        from _classification import _tokenizer

    ArgvToken = _tokenizer.ArgvToken
    EvaluatedSegment = _tokenizer.EvaluatedSegment
    StdinLiteral = _tokenizer.StdinLiteral
    _CommandSegment = _tokenizer._CommandSegment
    _normalize_newlines_for_tokenize = _tokenizer._normalize_newlines_for_tokenize
    _tokenize_command_segments_with_redirects = (
        _tokenizer._tokenize_command_segments_with_redirects
    )
    _strip_heredoc_bodies_impl = _tokenizer.strip_heredoc_bodies
    _tokenize_command_segments_impl = _tokenizer.tokenize_command_segments


def strip_heredoc_bodies(command: str) -> str:
    """Strip heredoc bodies while preserving opening lines and terminators."""
    return _strip_heredoc_bodies_impl(command)


def tokenize_command_segments(command: str) -> list[list[str]]:
    """Split a shell command into segments of verb-and-argument tokens."""
    return _tokenize_command_segments_impl(command)


PROTECTED_SOURCE_PATH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:\.autoskillit|src/autoskillit)/recipes/.*\.ya?ml"),
    re.compile(r"src/autoskillit/skills(?:_extended)?/.*/SKILL\.md"),
    re.compile(r"src/autoskillit/agents/.*\.md"),
    re.compile(r"src/autoskillit/skill_resources/.*\.md"),
]

DECLARABLE_SOURCE_PATH_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    PROTECTED_SOURCE_PATH_PATTERNS
)

_INTERPRETER_RE = re.compile(
    r"(?:^|&&|\|\||;)\s*(?:env\s+)?(?:python3?|perl|ruby|node)\s+"
    r"(?:-[ce]\s|.*<<)"
)

_INTERPRETER_LINE_RE = re.compile(r"(?:python3?|perl|ruby|node)\s+(?:-[ce]\s|.*<<)")

_WRITE_APIS_RE = re.compile(
    r"\.write_text\s*\(|\.write_bytes\s*\("
    r"|open\s*\([^)]*['\"][wWaAxX]\+?[bB]?['\"]"
    r"|shutil\.(?:copy|move|copyfile|copytree)\s*\("
)

_SUBPROCESS_APIS_RE = re.compile(
    r"subprocess\.(?:run|call|Popen|check_call|check_output)\s*\("
    r"|os\.(?:system|popen|exec[lv]p?e?)\s*\("
)

_LITERAL_OPEN_PATH_RE = re.compile(r"""open\s*\(\s*(['"])([^'"]+)\1\s*,\s*['"][wWaAxX]""")
_LITERAL_PATH_CONSTRUCTOR_RE = re.compile(
    r"""Path\s*\(\s*(['"])([^'"]+)\1\s*\)\s*\.(?:write_text|write_bytes)\s*\("""
)

_WRITE_CALL_SITE_RE = re.compile(
    r"open\s*\([^)]*['\"][wWaAxX]\+?[bB]?['\"]"
    r"|Path\s*\([^)]*\)\s*\.(?:write_text|write_bytes)\s*\("
    r"|shutil\.(?:copy|move|copyfile|copytree)\s*\("
)


# Command wrappers whose only effect is to invoke the next command with
# adjusted environment/priority. The verb is the token after the wrapper.
# 'xargs' is intentionally excluded: it dispatches a downstream reader and
# adding it would let `xargs cat src/.../foo.yaml` reach the reader check,
# weakening xargs-chain bypass detection (see D1 design decision).
_COMMAND_WRAPPERS: frozenset[str] = frozenset({"command", "nice", "time", "sudo", "nohup"})
# Wrappers that consume a mandatory DURATION as their first non-wrapper token.
_WRAPPERS_WITH_DURATION: frozenset[str] = frozenset({"timeout"})
# Wrappers that take a single short flag as their first non-wrapper token
# (e.g. 'stdbuf -o0', 'stdbuf -i0', 'stdbuf -e0').
_WRAPPERS_WITH_SHORT_FLAG: frozenset[str] = frozenset({"stdbuf"})

# env option arity tables.
_ENV_NO_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "-i",
        "-0",
        "-v",
        "-V",
        "--help",
        "--version",
        "--ignore-environment",
        "--null",
        "--debug",
    }
)
_ENV_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "-u",
        "--unset",
        "-C",
        "--chdir",
        "-S",
        "--split-string",
        "--default-signal",
        "--ignore-signal",
        "--block-signal",
        "--argv0",
    }
)
# Flags that take a value either as the next token or attached with '='.
_ENV_VALUE_FLAGS_ATTACHED: frozenset[str] = frozenset(
    {
        "-u",
        "--unset",
        "-C",
        "--chdir",
        "--argv0",
        "--default-signal",
        "--ignore-signal",
        "--block-signal",
    }
)
# Wrappers taking a value optionally attached ('-u=root', '--user=root').
_WRAPPER_VALUE_FLAGS_ATTACHED: frozenset[str] = frozenset(
    {
        "-u",
        "--user",
        "-n",
        "--adjustment",
        "-k",
        "--kill-after",
        "-s",
        "--signal",
    }
)
_WRAPPER_VALUE_FLAGS_DETACHED: frozenset[str] = frozenset(
    {
        "-u",
        "--user",
        "-n",
        "--adjustment",
        "-k",
        "--kill-after",
        "-s",
        "--signal",
        "-g",
        "--group",
        "-p",
        "--priority",
    }
)


class SearchPattern(Protocol):
    def search(self, string: str, /): ...


def extract_patch_paths(command: str) -> list[str]:
    """Extract target paths from unified and Codex apply_patch input."""
    paths: list[str] = []
    for line in command.splitlines():
        if line.startswith("+++ b/"):
            paths.append(line[6:])
        elif line.startswith(("*** Update File: ", "*** Add File: ", "*** Delete File: ")):
            paths.append(line.partition(": ")[2].strip())
    return paths


def _shell_source(argv_tokens: Sequence[ArgvToken] | None, index: int) -> str | None:
    """Keep a shell token's raw spelling; Python argv has no shell expansion."""
    if argv_tokens is None:
        return None
    return argv_tokens[index].raw_span.strip() if index < len(argv_tokens) else ""


def _non_option_indices(segment: list[str]) -> list[int]:
    separator = next(
        (index for index, token in enumerate(segment[1:], start=1) if token == "--"),
        len(segment),
    )
    return [
        index
        for index in range(1, len(segment))
        if (index < separator and not segment[index].startswith("-")) or index > separator
    ]


def _write_verb_operand_indices(verb: str, segment: list[str]) -> list[int]:
    operands = _non_option_indices(segment)
    if verb == "sed":
        has_inplace = any(token.startswith(("-i", "--in-place")) for token in segment[1:])
        if not has_inplace:
            return []
        return operands[-1:]
    if verb in {"mv", "cp"}:
        return operands[-1:] if len(operands) >= 2 else []
    if verb == "install":
        # GNU install's -t/--target-directory form takes its destination
        # from a flag; otherwise the last operand is the destination.
        if "-t" in segment[1:] or "--target-directory" in segment[1:]:
            return operands[:1]
        if len(operands) < 2:
            return []
        return operands[-1:]
    if verb == "patch":
        return operands[:1]
    return operands


def extract_write_verb_targets(
    verb: str,
    segment: list[str],
    cwd: str = "",
    *,
    argv_tokens: Sequence[ArgvToken] | None = None,
) -> tuple[list[str], bool]:
    """Return write-verb targets and whether a write target could not resolve."""
    operands = _write_verb_operand_indices(verb, segment)
    targets: list[str] = []
    unresolved_target = False
    for index in operands:
        resolved = resolve_write_target(
            segment[index], cwd, shell_source=_shell_source(argv_tokens, index)
        )
        if resolved is None:
            unresolved_target = True
        else:
            targets.append(resolved)
    return targets, unresolved_target


def _is_posix_assignment(token: str) -> bool:
    """Return True if *token* is a leading NAME=value assignment (POSIX env)."""
    if "=" not in token:
        return False
    if token.startswith("="):
        return False
    name, _sep, _value = token.partition("=")
    if not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in name)


def _consume_env(start: int, segment: list[str]) -> int:
    """Return the index of the first non-env token after the 'env' wrapper.

    Handles --, no-value flags, value flags (detached and attached), and
    NAME=value assignments.
    """
    i = start
    # Allow leading whitespace-like wrapper-only segment with no command.
    while i < len(segment):
        token = segment[i]
        if token == "--":
            i += 1
            break
        if token in _ENV_NO_VALUE_FLAGS:
            i += 1
            continue
        if "=" in token and token.split("=", 1)[0] in _ENV_VALUE_FLAGS_ATTACHED:
            i += 1
            continue
        if token in _ENV_VALUE_FLAGS and i + 1 < len(segment):
            i += 2
            continue
        if token.startswith("-") or _is_posix_assignment(token):
            if token in _ENV_VALUE_FLAGS and i + 1 < len(segment):
                i += 2
                continue
            # Unknown long flag that may take a value (e.g. --foo=bar).
            if "=" in token:
                i += 1
                continue
            i += 1
            continue
        break
    return i


def _env_chdir_value(start: int, segment: list[str]) -> tuple[str, int, int] | None:
    """Return the last chdir value, token index, and attached-prefix length."""
    end = _consume_env(start, segment)
    chdir: tuple[str, int, int] | None = None
    i = start
    while i < end:
        token = segment[i]
        if token in {"-C", "--chdir"} and i + 1 < end:
            chdir = (segment[i + 1], i + 1, 0)
            i += 2
        elif token.startswith("--chdir="):
            chdir = (token.partition("=")[2], i, len("--chdir="))
            i += 1
        elif token.startswith("-C") and token != "-C":
            chdir = (token[2:], i, 2)
            i += 1
        elif token in _ENV_VALUE_FLAGS and i + 1 < end:
            i += 2
        else:
            i += 1
    return chdir


def _consume_wrapper_options(start: int, segment: list[str]) -> int:
    """Skip past a wrapper's attached/detached value options.

    Wrappers (sudo, nice, nohup, time, command) accept option flags before
    the inner command. Many take values either attached (--user=root) or as
    the next detached token (-u root). Returns the index of the first
    non-option token.
    """
    i = start
    while i < len(segment):
        token = segment[i]
        if token == "--":
            return i + 1
        if not token.startswith("-"):
            return i
        if "=" in token:
            i += 1
            continue
        if token in _WRAPPER_VALUE_FLAGS_DETACHED and i + 1 < len(segment):
            i += 2
            continue
        i += 1
    return i


def _command_start_index(segment: list[str]) -> int | None:
    if not segment:
        return None
    start = 0
    while start < len(segment):
        token = segment[start]
        if _is_posix_assignment(token):
            start += 1
            continue
        if token == "env":
            start = _consume_env(start + 1, segment)
            continue
        if token in _COMMAND_WRAPPERS:
            start += 1
            continue
        if token in _WRAPPERS_WITH_DURATION and start + 1 < len(segment):
            start += 2
            continue
        if (
            token in _WRAPPERS_WITH_SHORT_FLAG
            and start + 1 < len(segment)
            and segment[start + 1].startswith("-")
        ):
            start += 2
            continue
        break
    return start if start < len(segment) else None


def _verb_start_index(segment: list[str]) -> int | None:
    """Return the index of the command verb in *segment*, skipping shell

    keywords (while/until/if/do/then/elif/else), POSIX assignments, `env`,
    and wrapper prefixes (sudo/nice/timeout/...). Returns None when the segment is
    empty or ends inside a wrapper (missing a required value) -- the same
    cases command_verb_and_args reports as ("", []). Exposed separately so
    a caller threading a second, index-aligned parallel array (e.g.
    ArgvToken quote provenance) can slice it at the same start point
    command_verb_and_args computes for *segment* itself, rather than
    re-deriving that decision independently.
    """
    if not segment:
        return None
    start = 0
    while start < len(segment):
        token = segment[start]
        if token in {"while", "until", "if", "do", "then", "elif", "else", "!"}:
            start += 1
            continue
        if _is_posix_assignment(token):
            start += 1
            continue
        if token == "env":
            start = _consume_env(start + 1, segment)
            continue
        if token in _COMMAND_WRAPPERS:
            # Wrappers (sudo, nice, etc.) may consume attached/detached value options.
            start = _consume_wrapper_options(start + 1, segment)
            continue
        if token in _WRAPPERS_WITH_DURATION:
            if start + 1 >= len(segment):
                return None
            start += 2
            continue
        if token in _WRAPPERS_WITH_SHORT_FLAG:
            if start + 1 >= len(segment):
                return None
            if not segment[start + 1].startswith("-"):
                return None
            start += 2
            continue
        break
    return start if start < len(segment) else None


def _command_position_candidate_spans(segment: Sequence[str]) -> tuple[tuple[int, int], ...]:
    """Return verb-aligned candidate spans in *segment*'s original index domain.

    Each span is ``(start, end)`` with an exclusive ``end``. The direct command
    candidate starts at the same index as :func:`_verb_start_index`. Group
    bodies are separate tokenizer segments, so callers retain aligned token
    provenance without deriving a second candidate here.
    """
    start = _verb_start_index(list(segment))
    if start is None:
        return ()

    return ((start, len(segment)),)


def command_verb_and_args(segment: list[str]) -> tuple[str, list[str]]:
    """Return (verb, args) for *segment*, skipping env/wrappers/assignments.

    The verb is the raw executable token; args are the tokens after it. If
    the segment ends inside a wrapper or a required wrapper value is
    absent, returns ("", []).
    """
    start = _verb_start_index(segment)
    if start is None:
        return ("", [])
    return (segment[start], segment[start + 1 :])


def command_verb(segment: list[str]) -> str:
    """Return the command verb from a segment, skipping 'env' prefix."""
    verb, _args = command_verb_and_args(segment)
    return verb


def is_gh_command(segment: list[str]) -> bool:
    """Return True if the segment's command verb is 'gh'."""
    return command_verb(segment) == "gh"


def is_git_command(segment: list[str]) -> bool:
    """Return True if the segment's command verb is 'git' or ends with '/git'."""
    verb = command_verb(segment)
    return verb == "git" or verb.endswith("/git")


@dataclass(slots=True)
class GitInvocation:
    subcommand: str
    flags: list[str]
    global_flags: list[str]
    prefix_tokens: list[str]


def _normalized_git_global_flag(token: str) -> str:
    if token.startswith("--") and "=" in token:
        return token.partition("=")[0]
    for flag, arity in _GIT_GLOBAL_FLAG_SPEC.items():
        if (
            arity == _FlagArity.VALUE
            and len(flag) == 2
            and token.startswith(flag)
            and token != flag
        ):
            return flag
    return token


def extract_git_subcommand_and_flags(segment: list[str]) -> GitInvocation | None:
    """Extract the git subcommand and its flags from a tokenized segment.

    Skips global git flags (and their value tokens) to find the subcommand,
    then returns its subcommand, remaining tokens, global flags, and skipped
    command prefix. Returns None if the segment
    is not a git command or has no subcommand.

    Returns a `"<unresolved>"` invocation when an unrecognized `-`-prefixed global
    flag is encountered before the subcommand -- distinguishable from None
    (not a git command, or the segment ends before a subcommand appears),
    since this function's three callers treat the two differently: an
    unrecognized flag means the real subcommand could not be found (fail
    closed, ambiguous), not "there is no git subcommand here" (already
    safe). See is_allowed_protected_path_metadata_command and
    `_git_command_classification._classify_git_segment` /
    `_git_command_classification._contains_blocked_git_op` (moved from
    `git_ops_guard.py` per Step 9 of #4665).
    """
    start = _command_start_index(segment)
    if start is None:
        return None
    verb = segment[start]
    if verb != "git" and not verb.endswith("/git"):
        return None
    prefix_tokens = segment[:start]
    global_flags: list[str] = []
    i = start + 1
    while i < len(segment):
        token = segment[i]
        if token.startswith("-"):
            _, next_i, recognized = _consume_str_flag(segment, i, _GIT_GLOBAL_FLAG_SPEC)
            if not recognized:
                return GitInvocation("<unresolved>", [], [], [])
            global_flags.append(_normalized_git_global_flag(token))
            i = next_i
            continue
        # First non-flag token is the subcommand
        subcommand = token
        remaining = segment[i + 1 :]
        return GitInvocation(subcommand, remaining, global_flags, prefix_tokens)
    return None


if TYPE_CHECKING:
    from autoskillit.hooks._classification import _interpreters  # noqa: F401
    from autoskillit.hooks._classification._flags import (  # noqa: F401
        _GIT_ADD_CONTENT_FLAGS,
        _GIT_DIFF_CONTENT_FLAGS,
        _GIT_DIFF_METADATA_FLAGS,
        _GIT_GLOBAL_FLAG_SPEC,
        _GIT_GLOBAL_FLAGS,
        _GIT_GLOBAL_FLAGS_WITH_VALUE,
        _GIT_STATUS_CONTENT_FLAGS,
        _PIP_GLOBAL_FLAG_SPEC,
        _PROTECTED_PATH_METADATA_GIT_SUBCOMMANDS,
        _SHELL_STATE_VAR_RE,
        _SHELL_SUBSTITUTION_RE,
        _WC_FLAG_RE,
        _argv_token_after_prefix,
        _argv_token_value_after_key,
        _consume_argv_flag,
        _consume_str_flag,
        _FlagArity,
        _spec_key_for_token,
        command_has_blocked_protected_path_read,
        is_allowed_protected_path_metadata_command,
    )
    from autoskillit.hooks._classification._interpreters import (  # noqa: F401
        _PYTHON_INVOCATION_FLAG_SPEC,
        _SHELL_INVOCATION_FLAG_SPEC,
        EvaluatedPayload,
        StdinConsumer,
        _extract_interpreter_segment_specs,
        _extract_process_substitution_occurrences,
        _is_shell_interpreter,
        _normalize_executable,
        evaluated_payloads,
        extract_interpreter_command_payloads,
        extract_interpreter_write_paths,
        extract_shell_command_payloads,
        has_interpreter_write,
        stdin_consumer,
        tokenize_shell_payload_segments,
    )
    from autoskillit.hooks._classification._interpreters import (
        all_evaluated_segments as _all_evaluated_segments_impl,
    )
    from autoskillit.hooks._classification._interpreters import (
        all_evaluated_segments_with_provenance as _all_evaluated_segments_with_provenance_impl,
    )
    from autoskillit.hooks._classification._interpreters import (
        interpreter_invokes as _interpreter_invokes_impl,
    )
    from autoskillit.hooks._classification._interpreters import (
        live_command_text as _live_command_text_impl,
    )
    from autoskillit.hooks._classification._output_redirect import (  # noqa: F401
        _FD_DUPLICATION_RE,
        _REDIRECT_OP_ONLY_RE,
        _REDIRECT_TOKEN_RE,
        OutputRedirectPartition,
        _partition_output_redirect_indices,
        _partition_output_redirects,
        _select_executable_argv_tokens,
        extract_redirect_targets_with_status,
        resolve_write_target,
    )
else:
    if __package__:
        from .._classification import _flags, _interpreters, _output_redirect
    else:
        from _classification import _flags, _interpreters, _output_redirect

    _GIT_ADD_CONTENT_FLAGS = _flags._GIT_ADD_CONTENT_FLAGS
    _GIT_DIFF_CONTENT_FLAGS = _flags._GIT_DIFF_CONTENT_FLAGS
    _GIT_DIFF_METADATA_FLAGS = _flags._GIT_DIFF_METADATA_FLAGS
    _GIT_GLOBAL_FLAG_SPEC = _flags._GIT_GLOBAL_FLAG_SPEC
    _GIT_GLOBAL_FLAGS = _flags._GIT_GLOBAL_FLAGS
    _GIT_GLOBAL_FLAGS_WITH_VALUE = _flags._GIT_GLOBAL_FLAGS_WITH_VALUE
    _GIT_STATUS_CONTENT_FLAGS = _flags._GIT_STATUS_CONTENT_FLAGS
    _PIP_GLOBAL_FLAG_SPEC = _flags._PIP_GLOBAL_FLAG_SPEC
    _PROTECTED_PATH_METADATA_GIT_SUBCOMMANDS = _flags._PROTECTED_PATH_METADATA_GIT_SUBCOMMANDS
    _SHELL_STATE_VAR_RE = _flags._SHELL_STATE_VAR_RE
    _SHELL_SUBSTITUTION_RE = _flags._SHELL_SUBSTITUTION_RE
    _WC_FLAG_RE = _flags._WC_FLAG_RE
    _argv_token_after_prefix = _flags._argv_token_after_prefix
    _argv_token_value_after_key = _flags._argv_token_value_after_key
    _consume_argv_flag = _flags._consume_argv_flag
    _consume_str_flag = _flags._consume_str_flag
    _spec_key_for_token = _flags._spec_key_for_token
    _FlagArity = _flags._FlagArity
    command_has_blocked_protected_path_read = _flags.command_has_blocked_protected_path_read
    is_allowed_protected_path_metadata_command = _flags.is_allowed_protected_path_metadata_command
    _extract_interpreter_segment_specs = _interpreters._extract_interpreter_segment_specs
    _extract_process_substitution_occurrences = (
        _interpreters._extract_process_substitution_occurrences
    )
    _is_shell_interpreter = _interpreters._is_shell_interpreter
    _normalize_executable = _interpreters._normalize_executable
    EvaluatedPayload = _interpreters.EvaluatedPayload
    StdinConsumer = _interpreters.StdinConsumer
    evaluated_payloads = _interpreters.evaluated_payloads
    extract_interpreter_command_payloads = _interpreters.extract_interpreter_command_payloads
    extract_interpreter_write_paths = _interpreters.extract_interpreter_write_paths
    extract_shell_command_payloads = _interpreters.extract_shell_command_payloads
    has_interpreter_write = _interpreters.has_interpreter_write
    stdin_consumer = _interpreters.stdin_consumer
    tokenize_shell_payload_segments = _interpreters.tokenize_shell_payload_segments
    _all_evaluated_segments_impl = _interpreters.all_evaluated_segments
    _all_evaluated_segments_with_provenance_impl = (
        _interpreters.all_evaluated_segments_with_provenance
    )
    _interpreter_invokes_impl = _interpreters.interpreter_invokes
    _live_command_text_impl = _interpreters.live_command_text
    _SHELL_INVOCATION_FLAG_SPEC = _interpreters._SHELL_INVOCATION_FLAG_SPEC
    _PYTHON_INVOCATION_FLAG_SPEC = _interpreters._PYTHON_INVOCATION_FLAG_SPEC
    OutputRedirectPartition = _output_redirect.OutputRedirectPartition
    _FD_DUPLICATION_RE = _output_redirect._FD_DUPLICATION_RE
    _REDIRECT_OP_ONLY_RE = _output_redirect._REDIRECT_OP_ONLY_RE
    _REDIRECT_TOKEN_RE = _output_redirect._REDIRECT_TOKEN_RE
    _partition_output_redirect_indices = _output_redirect._partition_output_redirect_indices
    _partition_output_redirects = _output_redirect._partition_output_redirects
    _select_executable_argv_tokens = _output_redirect._select_executable_argv_tokens
    extract_redirect_targets_with_status = _output_redirect.extract_redirect_targets_with_status
    resolve_write_target = _output_redirect.resolve_write_target


def all_evaluated_segments(
    command: str, *, include_process_substitutions: bool = False
) -> list[list[str]] | None:
    """Return every segment that will actually execute, across every consumer."""
    return _all_evaluated_segments_impl(
        command, include_process_substitutions=include_process_substitutions
    )


# Value-taking git global flags, derived from _GIT_GLOBAL_FLAG_SPEC. A flag
# missing from this set is misread below as a 1-token boolean skip.
_GIT_FLAG_WITH_VALUE: frozenset[str] = frozenset(
    flag for flag, arity in _GIT_GLOBAL_FLAG_SPEC.items() if arity == _FlagArity.VALUE
)


def _git_global_value(
    argv: list[str],
    index: int,
    flag: str,
    argv_tokens: Sequence[ArgvToken] | None,
) -> tuple[str | None, str | None, int] | None:
    token = argv[index]
    if flag not in _GIT_FLAG_WITH_VALUE:
        return None, None, index + 1
    if token == flag:
        if index + 1 >= len(argv):
            return None
        return argv[index + 1], _shell_source(argv_tokens, index + 1), index + 2
    value = token[len(flag) :]
    source = _shell_source(argv_tokens, index)
    if source is not None:
        source = source[len(flag) :]
    if token.startswith("--"):
        value = value.removeprefix("=")
        if source is not None:
            source = source.removeprefix("=")
    return value, source, index + 1


def _git_subcommand_index(
    argv: list[str], cwd: str, argv_tokens: Sequence[ArgvToken] | None = None
) -> tuple[int, str, bool]:
    """Find git's subcommand, applying global cwd and repository-layout flags."""
    i = 1
    layout_unknown = False
    while i < len(argv) and argv[i].startswith("-"):
        token = argv[i]
        flag = _spec_key_for_token(token, _GIT_GLOBAL_FLAG_SPEC)
        if flag not in _GIT_GLOBAL_FLAG_SPEC:
            return len(argv), cwd, layout_unknown
        parsed = _git_global_value(argv, i, flag, argv_tokens)
        if parsed is None:
            return len(argv), cwd, layout_unknown
        value, value_source, i = parsed
        if flag == "-C":
            cwd = resolve_write_target(value or "", cwd, shell_source=value_source) or ""
        elif flag in {"--work-tree", "--git-dir"}:
            layout_unknown = True
    return i, cwd, layout_unknown


def _wrapped_verb_cwd(
    segment: list[str],
    start: int,
    cwd: str,
    argv_tokens: Sequence[ArgvToken] | None = None,
) -> str:
    """Resolve env chdir options for the executable without moving shell redirects."""
    for index, token in enumerate(segment[:start]):
        if token == "env":
            chdir = _env_chdir_value(index + 1, segment)
            if chdir is not None:
                value, value_index, prefix_length = chdir
                source = _shell_source(argv_tokens, value_index)
                if source is not None:
                    source = source[prefix_length:]
                cwd = resolve_write_target(value, cwd, shell_source=source) or ""
    return cwd


def _git_write_targets(
    argv: list[str],
    cwd: str,
    prefix: list[str],
    argv_tokens: Sequence[ArgvToken] | None = None,
) -> tuple[list[str], bool, bool]:
    """Return git restoration targets, unresolved status, and write detection."""
    subcommand_index, git_cwd, layout_unknown = _git_subcommand_index(argv, cwd, argv_tokens)
    if subcommand_index >= len(argv):
        return [], False, False
    subcommand = argv[subcommand_index]
    options = argv[subcommand_index + 1 :]
    if subcommand == "reset" and "--hard" in options:
        return [], False, True
    if subcommand != "checkout" or "--" not in options:
        return [], False, False

    layout_unknown |= any(
        _is_posix_assignment(token) and token.partition("=")[0] in {"GIT_WORK_TREE", "GIT_DIR"}
        for token in prefix
    )
    targets: list[str] = []
    unresolved = False
    pathspec_start = subcommand_index + 2 + options.index("--")
    for index in range(pathspec_start, len(argv)):
        target = resolve_write_target(
            argv[index],
            "" if layout_unknown else git_cwd,
            shell_source=_shell_source(argv_tokens, index),
        )
        if target is None:
            unresolved = True
        else:
            targets.append(target)
    return targets, unresolved, True


def live_command_text(command: str) -> str:
    """Return an occurrence-aware live-text projection of *command*."""
    return _live_command_text_impl(command)


def interpreter_invokes(command: str, *, target: Sequence[str]) -> bool:
    """Return True when a PYTHON-consumer payload resolves to invoking *target*."""
    return _interpreter_invokes_impl(command, target=target)


if TYPE_CHECKING:
    from autoskillit.hooks._classification import _write_target_scan
    from autoskillit.hooks._classification._write_target_scan import (
        UNRESOLVED_WRITE_TARGET_REMEDIATION,
        WriteTargetScan,
    )
else:
    if __package__:
        from .._classification import _write_target_scan
    else:
        from _classification import _write_target_scan

    UNRESOLVED_WRITE_TARGET_REMEDIATION = _write_target_scan.UNRESOLVED_WRITE_TARGET_REMEDIATION
    WriteTargetScan = _write_target_scan.WriteTargetScan


def scan_write_targets(command: str, cwd: str) -> WriteTargetScan:
    """Classify write targets in evaluated shell and Python commands."""
    return _write_target_scan.scan_write_targets(command, cwd)
