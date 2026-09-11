"""Shared command classification primitives for guard scripts.

Supported interpreters: python3?, perl, ruby, node.
To add coverage for a new interpreter, update both _INTERPRETER_RE and
_INTERPRETER_LINE_RE.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from autoskillit.hooks._classification._tokenizer import (  # noqa: F401
        ArgvToken,
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

_NESTED_SHELL_RE = re.compile(r"(?:^|&&|\|\||;)\s*(?:bash|sh|zsh|dash)\s+-c\s+")

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


# Boundary-adjacency operator set for guards that scan raw shlex.split token
# streams (pr_create, git_ops, planner_gh_discovery, artifact_download,
# compose_pr_body): `!` and `(` may precede a fresh command verb there but
# are not split tokens for the segment lexer above.
_SHELL_OPS: frozenset[str] = frozenset({"&&", "||", ";", "!", "|", "("})

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

# Shell control words that mark the start of a new command in compound
# shell constructs (loops, conditionals, case statements). When a `gh` token
# is preceded by one of these, treat it as the verb of a fresh command — even
# though shlex does not treat them as operators. Keep this set narrow: only
# words that legitimately precede a command in real shell scripts.
_SHELL_CONTROL_WORDS: frozenset[str] = frozenset(
    {
        "do",
        "done",
        "then",
        "else",
        "elif",
        "esac",
        "fi",
        "in",
    }
)

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

_REDIRECT_TOKEN_RE = re.compile(r"^(\d*)>{1,2}(.+)$")
_REDIRECT_OP_ONLY_RE = re.compile(r"^(\d*)>{1,2}$")
_FD_REDIRECT_RE = re.compile(r"^\d*>{1,2}&")
_FD_DUPLICATION_RE = re.compile(r"^\d*>&\d+$")
_TRAILING_SHELL_CLOSERS = frozenset({")", "`", "}", "'", '"', ";", "&", "|"})
_SHELL_VAR_RE = re.compile(r"\$\{[A-Za-z_]|\$[A-Za-z_]")


_PROTECTED_PATH_METADATA_GIT_SUBCOMMANDS: frozenset[str] = frozenset({"add", "diff", "status"})

_GIT_ADD_CONTENT_FLAGS: frozenset[str] = frozenset(
    {
        "-p",
        "--patch",
        "-e",
        "--edit",
        "-i",
        "--interactive",
        "--pathspec-from-file",
        # Content-staging flags: -A/--all stages all changes (incl. content);
        # --force/--no-ignore-removal/--no-all are the no-restriction variants.
        # Without these, `git add -A -- src/.../foo.yaml` is classified as
        # metadata but actually stages content for indirect read via
        # `git diff --staged`.
        "-A",
        "--all",
        "--force",
        "--no-ignore-removal",
        "--no-all",
    }
)
_GIT_STATUS_CONTENT_FLAGS: frozenset[str] = frozenset({"-v", "--verbose"})
_GIT_DIFF_CONTENT_FLAGS: frozenset[str] = frozenset(
    {
        "-p",
        "--patch",
        "--patch-with-stat",
        "--patch-with-raw",
        "--binary",
        "--text",
        "--word-diff",
        "--color-words",
    }
)
_GIT_DIFF_METADATA_FLAGS: frozenset[str] = frozenset(
    {
        "--name-only",
        "--name-status",
        "--stat",
        "--shortstat",
        "--numstat",
        "--summary",
    }
)
_SHELL_SUBSTITUTION_RE = re.compile(r"\$\(|`|[<>]\(")
_SHELL_STATE_VAR_RE = re.compile(r"\$(?:_|[A-Za-z][A-Za-z0-9_]*|\{[^}]+\})")
_PROTECTED_READ_SHELL_OPS: frozenset[str] = frozenset({"&&", "||", ";", "|", "&"})
_WC_FLAG_RE = re.compile(r"-l+|--lines$")


class SearchPattern(Protocol):
    def search(self, string: str, /): ...


def resolve_write_target(path: str, cwd: str = "") -> str | None:
    if not path:
        return None
    if path.startswith("&") or _FD_REDIRECT_RE.match(path):
        return None
    if _SHELL_VAR_RE.search(path):
        path = os.path.expandvars(path)
        if _SHELL_VAR_RE.search(path):
            return None
    if os.path.isabs(path):
        return path
    if cwd:
        return os.path.join(cwd, path)
    return None


def _partition_output_redirect_indices(
    tokens: Sequence[str],
    *,
    cwd: str,
    redirect_syntax: Sequence[bool] | None = None,
) -> tuple[list[int], list[str], int]:
    """Core of _partition_output_redirects: which *tokens* indices are executable argv.

    Shared so a caller threading a second, index-aligned parallel array (e.g.
    ArgvToken quote provenance) can project it onto the same partitioning
    decision without re-deriving the redirect-syntax logic independently.
    """
    syntax = redirect_syntax if redirect_syntax is not None else [True] * len(tokens)
    executable_indices: list[int] = []
    targets: list[str] = []
    file_redirect_count = 0
    depth = 0
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "(" or (token.startswith("(") and len(token) > 1):
            depth += 1
            if token.endswith(")") and len(token) > 1:
                depth -= 1
            executable_indices.append(i)
            i += 1
            continue
        if token == ")":
            if depth > 0:
                depth -= 1
            executable_indices.append(i)
            i += 1
            continue
        if token.endswith(")") and len(token) > 1:
            if depth > 0:
                depth -= 1
            executable_indices.append(i)
            i += 1
            continue
        if depth > 0 or not syntax[i]:
            executable_indices.append(i)
            i += 1
            continue
        if _FD_DUPLICATION_RE.fullmatch(token):
            i += 1
            continue

        target: str | None = None
        if _REDIRECT_OP_ONLY_RE.fullmatch(token):
            file_redirect_count += 1
            if i + 1 < len(tokens) and not (
                syntax[i + 1]
                and (
                    _REDIRECT_OP_ONLY_RE.fullmatch(tokens[i + 1])
                    or _FD_DUPLICATION_RE.fullmatch(tokens[i + 1])
                )
            ):
                target = tokens[i + 1]
                i += 2
            else:
                i += 1
        else:
            match = _REDIRECT_TOKEN_RE.fullmatch(token)
            if match is None:
                executable_indices.append(i)
                i += 1
                continue
            file_redirect_count += 1
            target = match.group(2)
            i += 1

        if target is not None:
            while target and target[-1] in _TRAILING_SHELL_CLOSERS:
                target = target[:-1]
            resolved = resolve_write_target(target, cwd)
            if resolved is not None:
                targets.append(resolved)
    return (executable_indices, targets, file_redirect_count)


def _partition_output_redirects(
    tokens: Sequence[str],
    *,
    cwd: str,
    redirect_syntax: Sequence[bool] | None = None,
) -> tuple[list[str], list[str], int]:
    """Separate depth-zero output control from executable argv."""
    executable_indices, targets, file_redirect_count = _partition_output_redirect_indices(
        tokens, cwd=cwd, redirect_syntax=redirect_syntax
    )
    return ([tokens[i] for i in executable_indices], targets, file_redirect_count)


def _select_executable_argv_tokens(
    tokens: Sequence[str],
    argv_tokens: Sequence[ArgvToken],
    *,
    cwd: str,
    redirect_syntax: Sequence[bool] | None = None,
) -> list[ArgvToken]:
    """Project *argv_tokens* onto the same indices _partition_output_redirects

    keeps as executable argv -- for callers threading ArgvToken quote
    provenance through the same redirect-partitioning decision that
    _partition_output_redirects already makes from *tokens* alone.
    """
    executable_indices, _targets, _count = _partition_output_redirect_indices(
        tokens, cwd=cwd, redirect_syntax=redirect_syntax
    )
    return [argv_tokens[i] for i in executable_indices]


def extract_redirect_targets(tokens: list[str], cwd: str = "") -> list[str]:
    """Extract resolved redirect target paths from already-tokenized input.

    Returns resolved paths including pseudo-devices — caller filters.
    Relative paths are resolved against cwd when provided.
    """
    return _partition_output_redirects(tokens, cwd=cwd)[1]


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
        if token in {"while", "until", "if", "do", "then", "elif", "else"}:
            start += 1
            continue
        if _is_posix_assignment(token):
            start += 1
            continue
        if token == "env":
            new_start = _consume_env(start + 1, segment)
            if new_start <= start:
                return None
            start = new_start
            continue
        if token in _COMMAND_WRAPPERS:
            # Wrappers (sudo, nice, etc.) may consume attached/detached value options.
            new_start = _consume_wrapper_options(start + 1, segment)
            if new_start <= start + 1:
                # No options consumed; check for missing required value.
                start += 1
                continue
            start = new_start
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
    candidate starts at the same index as :func:`_verb_start_index`. An inline
    function (``name() { ...``) or group (``{ ...``) also exposes its body as a
    second candidate without re-tokenizing or changing indices, so callers can
    keep a parallel token-provenance array aligned with the original segment.
    """
    start = _verb_start_index(list(segment))
    if start is None:
        return ()

    end = len(segment)
    spans: list[tuple[int, int]] = [(start, end)]
    verb = segment[start]
    if verb == "{" and start + 1 < end:
        spans.append((start + 1, end))
    elif verb.endswith("()") and start + 2 < end and segment[start + 1] == "{":
        spans.append((start + 2, end))
    return tuple(spans)


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


def extract_git_subcommand_and_flags(
    segment: list[str],
) -> tuple[str, list[str]] | None:
    """Extract the git subcommand and its flags from a tokenized segment.

    Skips global git flags (and their value tokens) to find the subcommand,
    then returns (subcommand, remaining_tokens). Returns None if the segment
    is not a git command or has no subcommand.

    Returns ("<unresolved>", []) when an unrecognized `-`-prefixed global
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
    i = start + 1
    while i < len(segment):
        token = segment[i]
        if token.startswith("-"):
            _, next_i, recognized = _consume_str_flag(segment, i, _GIT_GLOBAL_FLAG_SPEC)
            if not recognized:
                return ("<unresolved>", [])
            i = next_i
            continue
        # First non-flag token is the subcommand
        subcommand = token
        remaining = segment[i + 1 :]
        return (subcommand, remaining)
    return None


if TYPE_CHECKING:
    from autoskillit.hooks._classification._flags import (  # noqa: F401
        _GIT_GLOBAL_FLAG_SPEC,
        _GIT_GLOBAL_FLAGS,
        _GIT_GLOBAL_FLAGS_WITH_VALUE,
        _PIP_GLOBAL_FLAG_SPEC,
        _argv_token_after_prefix,
        _argv_token_value_after_key,
        _consume_argv_flag,
        _consume_str_flag,
        _FlagArity,
        _tokenize_protected_read_segments,
        command_has_blocked_protected_path_read,
        is_allowed_protected_path_metadata_command,
    )
    from autoskillit.hooks._classification._interpreters import (  # noqa: F401
        _extract_interpreter_command_specs,
        _extract_interpreter_segment_specs,
        _extract_process_substitution_occurrences,
        _normalize_executable,
        _segment_evaluates_shell_payload,
        extract_interpreter_command_payloads,
        extract_interpreter_write_paths,
        extract_shell_command_payloads,
        has_interpreter_wrapped_command,
        has_interpreter_write,
        has_nested_shell,
        tokenize_shell_payload_segments,
    )
else:
    if __package__:
        from .._classification import _flags, _interpreters
    else:
        from _classification import _flags, _interpreters

    _GIT_GLOBAL_FLAG_SPEC = _flags._GIT_GLOBAL_FLAG_SPEC
    _GIT_GLOBAL_FLAGS = _flags._GIT_GLOBAL_FLAGS
    _GIT_GLOBAL_FLAGS_WITH_VALUE = _flags._GIT_GLOBAL_FLAGS_WITH_VALUE
    _PIP_GLOBAL_FLAG_SPEC = _flags._PIP_GLOBAL_FLAG_SPEC
    _argv_token_after_prefix = _flags._argv_token_after_prefix
    _argv_token_value_after_key = _flags._argv_token_value_after_key
    _consume_argv_flag = _flags._consume_argv_flag
    _consume_str_flag = _flags._consume_str_flag
    _FlagArity = _flags._FlagArity
    _tokenize_protected_read_segments = _flags._tokenize_protected_read_segments
    command_has_blocked_protected_path_read = _flags.command_has_blocked_protected_path_read
    is_allowed_protected_path_metadata_command = _flags.is_allowed_protected_path_metadata_command
    _extract_interpreter_command_specs = _interpreters._extract_interpreter_command_specs
    _extract_interpreter_segment_specs = _interpreters._extract_interpreter_segment_specs
    _extract_process_substitution_occurrences = (
        _interpreters._extract_process_substitution_occurrences
    )
    _normalize_executable = _interpreters._normalize_executable
    _segment_evaluates_shell_payload = _interpreters._segment_evaluates_shell_payload
    extract_interpreter_command_payloads = _interpreters.extract_interpreter_command_payloads
    extract_interpreter_write_paths = _interpreters.extract_interpreter_write_paths
    extract_shell_command_payloads = _interpreters.extract_shell_command_payloads
    has_interpreter_wrapped_command = _interpreters.has_interpreter_wrapped_command
    has_interpreter_write = _interpreters.has_interpreter_write
    has_nested_shell = _interpreters.has_nested_shell
    tokenize_shell_payload_segments = _interpreters.tokenize_shell_payload_segments
