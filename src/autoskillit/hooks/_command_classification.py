"""Shared command classification primitives for guard scripts.

Supported interpreters: python3?, perl, ruby, node.
To add coverage for a new interpreter, update both _INTERPRETER_RE and
_INTERPRETER_LINE_RE.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

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

# Operators that terminate a shlex token and split command segments.
# Parentheses are tracked by the lexer as fused tokens (`(cmd` or `cmd)`)
# and handled separately in extract_redirect_targets.
_SHELL_OPERATORS: frozenset[str] = frozenset({"&&", "||", ";", "|", "&"})

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

_HEREDOC_BODY_RE = re.compile(
    r"(<<-?\s*['\"]?(\w+)['\"]?[^\n]*)\n.*?\n\t*(\2)(?=[ \t]*(?:\n|$))",
    re.DOTALL,
)

# After strip_heredoc_bodies() a heredoc collapses to "<<WORD ...\nWORD".
# This removes the marker and terminator, keeping the rest of the opening
# line (real redirects), so segments carry only executable tokens.
_HEREDOC_MARKER_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n\t*\1(?=[ \t]*(?:\n|$))")

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


def strip_heredoc_bodies(command: str) -> str:
    """Strip heredoc body content, preserving the opening line and terminator.

    The opening line (containing << and any real redirects) is kept intact.
    Only the body lines between the opening and terminator are removed.
    """
    return _HEREDOC_BODY_RE.sub(r"\1\n\3", command)


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


def _normalize_newlines_for_tokenize(command: str) -> str:
    """Replace bare (unquoted) newlines with ' ; ' so shlex treats them as boundaries."""
    result: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        c = command[i]
        if c == "\\" and not in_single and i + 1 < len(command):
            result.append(c)
            result.append(command[i + 1])
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "\n" and not in_single and not in_double:
            result.append(" ; ")
            i += 1
            continue
        result.append(c)
        i += 1
    return "".join(result)


@dataclass(frozen=True, slots=True)
class _CommandSegment:
    tokens: list[str]
    redirect_syntax: list[bool]


def _mark_unquoted_output_redirects(command: str) -> tuple[str, dict[str, str]]:
    """Replace recognized redirect operators with shlex-stable placeholders."""
    rendered: list[str] = []
    redirects: dict[str, str] = {}
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        char = command[i]
        if char == "\\" and not in_single and i + 1 < len(command):
            rendered.extend((char, command[i + 1]))
            i += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            rendered.append(char)
            i += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            rendered.append(char)
            i += 1
            continue
        if in_single or in_double:
            rendered.append(char)
            i += 1
            continue

        start = i
        if char.isdecimal() and (i == 0 or command[i - 1].isspace() or command[i - 1] in ";&|("):
            while i < len(command) and command[i].isdecimal():
                i += 1
            if i >= len(command) or command[i] != ">":
                rendered.append(command[start])
                i = start + 1
                continue
        elif char != ">":
            rendered.append(char)
            i += 1
            continue

        operator_start = start
        operator_end = i + 1
        if operator_end < len(command) and command[operator_end] == ">":
            operator_end += 1
        if operator_end < len(command) and command[operator_end] == "(":
            rendered.append(command[start])
            i = start + 1
            continue
        if (
            operator_end < len(command)
            and command[operator_end] == "&"
            and (not command[operator_start:i] or command[operator_start:i].isdecimal())
        ):
            fd_end = operator_end + 1
            while fd_end < len(command) and command[fd_end].isdecimal():
                fd_end += 1
            if fd_end == operator_end + 1:
                rendered.append(command[start])
                i = start + 1
                continue
            operator_end = fd_end

        marker = f"__AUTOSKILLIT_REDIRECT_{len(redirects)}__"
        redirects[marker] = command[operator_start:operator_end]
        rendered.extend((" ", marker, " "))
        i = operator_end
    return ("".join(rendered), redirects)


def _tokenize_command_segments_with_redirects(command: str) -> list[_CommandSegment]:
    """Tokenize commands while retaining which redirect-shaped tokens are syntax."""
    try:
        stripped = _HEREDOC_MARKER_RE.sub(r"\2", strip_heredoc_bodies(command))
        marked, redirects = _mark_unquoted_output_redirects(
            _normalize_newlines_for_tokenize(stripped)
        )
        lexer = shlex.shlex(
            marked,
            posix=True,
            punctuation_chars=";&|",
        )
        lexer.whitespace_split = True
        tokens = list(lexer)
    except (ValueError, TypeError):
        return []

    segments: list[_CommandSegment] = []
    current_tokens: list[str] = []
    current_redirect_syntax: list[bool] = []
    for token in tokens:
        if token in _SHELL_OPERATORS:
            if current_tokens:
                segments.append(_CommandSegment(current_tokens, current_redirect_syntax))
                current_tokens = []
                current_redirect_syntax = []
        else:
            redirect = redirects.get(token)
            current_tokens.append(redirect if redirect is not None else token)
            current_redirect_syntax.append(redirect is not None)
    if current_tokens:
        segments.append(_CommandSegment(current_tokens, current_redirect_syntax))
    return segments


def tokenize_command_segments(command: str) -> list[list[str]]:
    """Split a shell command into segments of (verb, args...) token lists."""
    return [segment.tokens for segment in _tokenize_command_segments_with_redirects(command)]


def _partition_output_redirects(
    tokens: Sequence[str],
    *,
    cwd: str,
    redirect_syntax: Sequence[bool] | None = None,
) -> tuple[list[str], list[str], int]:
    """Separate depth-zero output control from executable argv."""
    syntax = redirect_syntax if redirect_syntax is not None else [True] * len(tokens)
    executable: list[str] = []
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
            executable.append(token)
            i += 1
            continue
        if token == ")":
            if depth > 0:
                depth -= 1
            executable.append(token)
            i += 1
            continue
        if token.endswith(")") and len(token) > 1:
            if depth > 0:
                depth -= 1
            executable.append(token)
            i += 1
            continue
        if depth > 0 or not syntax[i]:
            executable.append(token)
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
                executable.append(token)
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
    return (executable, targets, file_redirect_count)


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


def command_verb_and_args(segment: list[str]) -> tuple[str, list[str]]:
    """Return (verb, args) for *segment*, skipping env/wrappers/assignments.

    The verb is the raw executable token; args are the tokens after it. If
    the segment ends inside a wrapper or a required wrapper value is
    absent, returns ("", []).
    """
    if not segment:
        return ("", [])
    start = 0
    while start < len(segment):
        token = segment[start]
        if token in {"do", "then", "elif", "else"}:
            start += 1
            continue
        if _is_posix_assignment(token):
            start += 1
            continue
        if token == "env":
            new_start = _consume_env(start + 1, segment)
            if new_start <= start:
                return ("", [])
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
                return ("", [])
            start += 2
            continue
        if token in _WRAPPERS_WITH_SHORT_FLAG:
            if start + 1 >= len(segment):
                return ("", [])
            if not segment[start + 1].startswith("-"):
                return ("", [])
            start += 2
            continue
        break
    if start >= len(segment):
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


_GIT_GLOBAL_FLAGS: frozenset[str] = frozenset(
    {"-C", "--work-tree", "--git-dir", "--no-pager", "--bare", "-c"}
)
_GIT_GLOBAL_FLAGS_WITH_VALUE: frozenset[str] = frozenset({"-C", "--work-tree", "--git-dir", "-c"})


def extract_git_subcommand_and_flags(
    segment: list[str],
) -> tuple[str, list[str]] | None:
    """Extract the git subcommand and its flags from a tokenized segment.

    Skips global git flags (and their value tokens) to find the subcommand,
    then returns (subcommand, remaining_tokens). Returns None if the segment
    is not a git command or has no subcommand.
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
        if token in _GIT_GLOBAL_FLAGS_WITH_VALUE:
            i += 2  # skip flag and its value
            continue
        if token in _GIT_GLOBAL_FLAGS:
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        # First non-flag token is the subcommand
        subcommand = token
        remaining = segment[i + 1 :]
        return (subcommand, remaining)
    return None


def _is_allowed_wc_flag(token: str) -> bool:
    """Return True when *token* is a wc flag that does not reveal file contents.

    Allows ``-l``, repeated ``-l`` (e.g. ``-ll``), and the long form ``--lines``
    only. Any value-bearing variant (``--lines=10``) or compound form
    (``-lL``) is rejected because those are not used for metadata-only reads.
    """
    return bool(_WC_FLAG_RE.fullmatch(token))


def is_allowed_protected_path_metadata_command(segment: list[str]) -> bool:
    """Return True for protected-path commands that inspect metadata or VCS state.

    Protected recipe/skill/agent paths are normally deny-by-default because most
    commands that mention them are content reads. These narrow exceptions support
    legitimate pipeline work on files already in scope.
    """
    verb = command_verb(segment)
    if verb == "git" or verb.endswith("/git"):
        git_parts = extract_git_subcommand_and_flags(segment)
        if git_parts is None:
            return False
        subcommand, flags = git_parts
        if subcommand not in _PROTECTED_PATH_METADATA_GIT_SUBCOMMANDS:
            return False
        if subcommand == "add":
            return not any(
                flag in _GIT_ADD_CONTENT_FLAGS or flag.startswith("--pathspec-from-file=")
                for flag in flags
            )
        if subcommand == "status":
            return not any(flag in _GIT_STATUS_CONTENT_FLAGS for flag in flags)
        if subcommand == "diff":
            if any(
                flag in _GIT_DIFF_CONTENT_FLAGS
                or flag.startswith("-U")
                or flag.startswith("--unified")
                or flag.startswith("--word-diff")
                or flag.startswith("--color-words")
                or flag.startswith("--patch-with-stat")
                or flag.startswith("--patch-with-raw")
                for flag in flags
            ):
                return False
            return any(
                flag in _GIT_DIFF_METADATA_FLAGS or flag.startswith("--stat=") for flag in flags
            )
        return False
    if verb == "wc" or verb.endswith("/wc"):
        start = _command_start_index(segment)
        if start is None:
            return False
        flags = [token for token in segment[start + 1 :] if token.startswith("-")]
        return bool(flags) and all(_is_allowed_wc_flag(token) for token in flags)
    return False


def _tokenize_protected_read_segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(
            _normalize_newlines_for_tokenize(command), posix=True, punctuation_chars=";&|()"
        )
        lexer.whitespace_split = True
        tokens = list(lexer)
    except (ValueError, TypeError):
        return []

    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _PROTECTED_READ_SHELL_OPS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def command_has_blocked_protected_path_read(
    command: str, protected_path_patterns: Sequence[SearchPattern]
) -> bool:
    """Return True when a command reads a protected recipe/skill/agent path."""
    if not any(pattern.search(command) for pattern in protected_path_patterns):
        return False

    if "<<" in command or _SHELL_SUBSTITUTION_RE.search(command):
        return True

    segments = _tokenize_protected_read_segments(command)
    if not segments:
        return True

    if len(segments) > 1 and _SHELL_STATE_VAR_RE.search(command):
        return True

    for segment in segments:
        segment_text = " ".join(segment)
        if any(pattern.search(segment_text) for pattern in protected_path_patterns):
            if not is_allowed_protected_path_metadata_command(segment):
                return True
    return False


def has_interpreter_write(command: str) -> bool:
    if not _INTERPRETER_RE.search(command):
        return False
    return bool(_WRITE_APIS_RE.search(command))


def extract_interpreter_write_paths(command: str) -> list[str] | None:
    """Extract literal file paths from an interpreter write command.

    Returns:
        None    — command is not an interpreter write (no prefix or no write API).
        []      — interpreter write detected but not all paths are static literals
                  (dynamic variable, f-string, shutil two-arg, or mixed).
        [paths] — all write target paths are static literals (may be relative).
    """
    if not _INTERPRETER_RE.search(command):
        return None
    if not _WRITE_APIS_RE.search(command):
        return None

    call_site_count = len(_WRITE_CALL_SITE_RE.findall(command))

    paths: list[str] = []
    for m in _LITERAL_OPEN_PATH_RE.finditer(command):
        paths.append(m.group(2))
    for m in _LITERAL_PATH_CONSTRUCTOR_RE.finditer(command):
        paths.append(m.group(2))

    if len(paths) < call_site_count:
        return []

    return paths if paths else []


def has_interpreter_wrapped_command(command: str, *, target_commands: Sequence[str]) -> bool:
    if not _INTERPRETER_RE.search(command):
        return False
    if not _SUBPROCESS_APIS_RE.search(command):
        return False
    cmd_lower = command.lower()
    return any(tc.lower() in cmd_lower for tc in target_commands)


def has_nested_shell(command: str) -> bool:
    return bool(_NESTED_SHELL_RE.search(command))


_SHELL_INTERPRETERS: frozenset[str] = frozenset({"bash", "sh", "zsh", "dash"})


def _normalize_executable(token: str) -> str:
    return os.path.basename(token).lower()


def _is_shell_interpreter(token: str) -> bool:
    base = _normalize_executable(token)
    if base in _SHELL_INTERPRETERS:
        return True
    # Versioned forms: bash5, sh4, dash0.5, zsh5
    for name in _SHELL_INTERPRETERS:
        if base.startswith(name) and base[len(name) :].isdigit():
            return True
    return False


def extract_shell_command_payloads(command: str) -> list[str]:
    """Return shell text payloads that will actually be evaluated.

    Includes the argument following `-c` for path-normalized bash/sh/zsh/dash
    invocations, the joined argument payload for `eval`, balanced `$(...)`
    payloads and backtick payloads occurring outside single quotes (including
    those inside double quotes). Nested payloads are extracted recursively.
    Single-quoted text, escaped substitutions, and heredoc bodies are inert.
    """
    payloads: list[str] = []
    segments = tokenize_command_segments(command)
    for segment in segments:
        verb, args = command_verb_and_args(segment)
        if not verb:
            continue
        if _is_shell_interpreter(verb) and args and args[0] == "-c" and len(args) >= 2:
            payloads.append(args[1])
            continue
        if verb == "eval" and args:
            payloads.append(" ".join(args))
            continue
    # Substitution scan
    for sub in _extract_substitution_payloads(command):
        payloads.append(sub)
    return payloads


def _segment_evaluates_shell_payload(tokens: list[str], payload: str) -> bool:
    """Return whether *tokens* structurally evaluate *payload* as shell text."""
    verb, args = command_verb_and_args(tokens)
    if _is_shell_interpreter(verb) and args and args[0] == "-c" and len(args) >= 2:
        return args[1] == payload
    if verb == "eval" and args:
        return " ".join(args) == payload
    rendered = " ".join(tokens)
    return f"$({payload})" in rendered or f"`{payload}`" in rendered


def tokenize_shell_payload_segments(command: str) -> list[list[str]] | None:
    """Return tokenized segments for every evaluated shell payload in *command*.

    Walks the outer command and every distinct extracted payload recursively.
    Each successfully parsed
    segment of every payload is appended to the result so callers can apply
    verb-position policies like ``command_verb_and_args`` to each segment.

    Returns ``None`` when the outer command or any non-empty evaluated
    payload cannot be tokenized; callers interpret ``None`` as no deny
    match (fail-open). Returns ``[]`` when the command has no evaluated
    shell payload to traverse.
    """
    outer = tokenize_command_segments(command)
    if not outer and command.strip():
        return None

    result: list[list[str]] = []
    seen: set[str] = set()
    queue: list[str] = list(extract_shell_command_payloads(command))
    while queue:
        payload = queue.pop(0)
        if payload in seen:
            continue
        seen.add(payload)
        if not payload.strip():
            continue
        segments = tokenize_command_segments(payload)
        if not segments and payload.strip():
            return None
        result.extend(segments)
        queue.extend(extract_shell_command_payloads(payload))
    return result


def _find_substitution_end(command: str, start: int) -> int:
    """Return the index of the ``)`` closing a ``$(`` whose body starts at *start*.

    Quotes open a fresh quoting context inside a substitution, so a literal
    ``)`` within a quoted span must not terminate the scan. Returns
    ``len(command)`` when the substitution is unclosed.
    """
    depth = 1
    n = len(command)
    k = start
    while k < n:
        ch = command[k]
        if ch == "\\" and k + 1 < n:
            k += 2
            continue
        if ch == "'":
            k += 1
            while k < n and command[k] != "'":
                k += 1
            k += 1
            continue
        if ch == '"':
            k += 1
            while k < n and command[k] != '"':
                if command[k] == "\\" and k + 1 < n:
                    k += 2
                    continue
                k += 1
            k += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return k
        k += 1
    return n


def _extract_substitution_payloads(command: str) -> list[str]:
    """Quote/escape-aware state machine returning immediate substitution bodies."""
    payloads: list[str] = []
    i = 0
    n = len(command)
    while i < n:
        c = command[i]
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == "'":
            # Skip single-quoted span
            j = i + 1
            while j < n and command[j] != "'":
                j += 1
            i = j + 1
            continue
        if c == '"':
            # Walk inside double quotes; substitutions are still active here.
            j = i + 1
            while j < n and command[j] != '"':
                if command[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if command[j] == "`":
                    inner_end = j + 1
                    while inner_end < n and command[inner_end] != "`":
                        inner_end += 1
                    inner = command[j + 1 : inner_end]
                    payloads.append(inner)
                    j = inner_end + 1
                    continue
                if command[j] == "$" and j + 1 < n and command[j + 1] == "(":
                    k = _find_substitution_end(command, j + 2)
                    inner = command[j + 2 : k]
                    payloads.append(inner)
                    j = k + 1
                    continue
                j += 1
            i = j + 1
            continue
        if c == "`":
            j = i + 1
            while j < n and command[j] != "`":
                if command[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                j += 1
            inner = command[i + 1 : j]
            payloads.append(inner)
            i = j + 1
            continue
        if c == "$" and i + 1 < n and command[i + 1] == "(":
            j = _find_substitution_end(command, i + 2)
            inner = command[i + 2 : j]
            payloads.append(inner)
            i = j + 1
            continue
        i += 1
    return payloads


_PYTHON_SUBPROCESS_FUNCS: frozenset[str] = frozenset(
    {
        "subprocess.run",
        "subprocess.call",
        "subprocess.Popen",
        "subprocess.check_call",
        "subprocess.check_output",
    }
)
_PYTHON_OS_EXEC_FUNCS: frozenset[str] = frozenset(
    {
        "os.system",
        "os.popen",
        "os.execl",
        "os.execle",
        "os.execlp",
        "os.execv",
        "os.execvp",
        "os.execvpe",
        "os.execve",
    }
)


def _parse_python_program_literals(program: str) -> list[ast.Call]:
    """Return subprocess/os call AST nodes found in a Python -c program."""
    try:
        tree = ast.parse(program, mode="exec")
    except SyntaxError:
        return []
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            dotted: str | None = None
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                dotted = f"{func.value.id}.{func.attr}"
            if dotted in _PYTHON_SUBPROCESS_FUNCS or dotted in _PYTHON_OS_EXEC_FUNCS:
                calls.append(node)
    return calls


def _literal_to_argv(node: ast.AST) -> list[str] | None:
    """Return a literal argv list from a list/tuple literal AST node, else None."""
    if isinstance(node, ast.List):
        elements = node.elts
    elif isinstance(node, ast.Tuple):
        elements = node.elts
    else:
        return None
    out: list[str] = []
    for elt in elements:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            out.append(elt.value)
        else:
            return None
    return out


def _literal_to_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


@dataclass(frozen=True, slots=True)
class _InterpreterCommandSpec:
    payload: str | list[str]
    cwd: str | None


def _python_program_command_specs(
    program: str,
) -> tuple[list[_InterpreterCommandSpec], bool]:
    specs: list[_InterpreterCommandSpec] = []
    has_unresolved = False
    for call in _parse_python_program_literals(program):
        args = call.args
        if not args:
            continue
        cwd_nodes = [keyword.value for keyword in call.keywords if keyword.arg == "cwd"]
        if len(cwd_nodes) > 1:
            has_unresolved = True
            continue
        cwd: str | None = None
        if cwd_nodes:
            cwd_node = cwd_nodes[0]
            if isinstance(cwd_node, ast.Constant) and cwd_node.value is None:
                cwd = None
            else:
                cwd = _literal_to_string(cwd_node)
                if cwd is None:
                    has_unresolved = True
                    continue
        first = args[0]
        shell_arg = next((kw.value for kw in call.keywords if kw.arg == "shell"), None)
        is_shell_true = bool(
            shell_arg is not None
            and isinstance(shell_arg, ast.Constant)
            and shell_arg.value is True
        )
        if is_shell_true:
            cmd_str = _literal_to_string(first)
            if cmd_str is not None:
                specs.append(_InterpreterCommandSpec(cmd_str, cwd))
                continue
            has_unresolved = True
            continue
        argv = _literal_to_argv(first)
        if argv is not None:
            specs.append(_InterpreterCommandSpec(argv, cwd))
            continue
        cmd_str = _literal_to_string(first)
        if cmd_str is not None:
            specs.append(_InterpreterCommandSpec(cmd_str, cwd))
            continue
        has_unresolved = True
    return (specs, has_unresolved)


def _extract_interpreter_command_specs(
    command: str,
) -> tuple[list[_InterpreterCommandSpec], bool]:
    specs: list[_InterpreterCommandSpec] = []
    has_unresolved = False
    if not _INTERPRETER_RE.search(command) or not _SUBPROCESS_APIS_RE.search(command):
        return (specs, has_unresolved)
    py_re = re.compile(
        r"(?:^|&&|\|\||;)\s*(?:env\s+)?(?:python3?(?:\.\d+)?)\s+-c\s+(['\"])(.*?)\1",
        re.DOTALL,
    )
    for match in py_re.finditer(command):
        found, unresolved = _python_program_command_specs(match.group(2))
        specs.extend(found)
        has_unresolved = has_unresolved or unresolved
    return (specs, has_unresolved)


def _extract_interpreter_segment_specs(
    segment: Sequence[str],
) -> tuple[list[_InterpreterCommandSpec], bool]:
    verb, args = command_verb_and_args(list(segment))
    executable = os.path.basename(verb).casefold()
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?", executable) is None:
        return ([], False)
    try:
        command_index = args.index("-c")
    except ValueError:
        return ([], False)
    if command_index + 1 >= len(args):
        return ([], True)
    return _python_program_command_specs(args[command_index + 1])


def extract_interpreter_command_payloads(command: str) -> tuple[list[str | list[str]], bool]:
    """Return literal subprocess payloads and whether any were unresolved."""
    specs, has_unresolved = _extract_interpreter_command_specs(command)
    return ([spec.payload for spec in specs], has_unresolved)


if TYPE_CHECKING:
    from autoskillit.hooks._github_mutation_analysis import (  # noqa: E402
        GitHubMutationAnalysis,
        GitHubMutationKind,
        GitHubMutationRecord,
        GitHubMutationStatus,
        analyze_github_mutations,
    )
elif __package__:
    from ._github_mutation_analysis import (  # noqa: E402
        GitHubMutationAnalysis,
        GitHubMutationKind,
        GitHubMutationRecord,
        GitHubMutationStatus,
        analyze_github_mutations,
    )
else:
    from _github_mutation_analysis import (  # noqa: E402
        GitHubMutationAnalysis,
        GitHubMutationKind,
        GitHubMutationRecord,
        GitHubMutationStatus,
        analyze_github_mutations,
    )

__all__ = [
    "GitHubMutationStatus",
    "GitHubMutationKind",
    "GitHubMutationRecord",
    "GitHubMutationAnalysis",
    "analyze_github_mutations",
]

# Module-level binding so tests can monkeypatch this dict directly on
# `command_classification._GH_READ_ONLY_SUBCOMMANDS`. The constant lives in
# the decomposed sibling; this binding re-aliases the same dict object so
# `monkeypatch.delitem(command_classification._GH_READ_ONLY_SUBCOMMANDS, "pr")`
# continues to mutate the source-of-truth dict in `_github_mutation_analysis`.
if TYPE_CHECKING:
    from autoskillit.hooks._github_mutation_analysis import (  # noqa: E402
        _GH_READ_ONLY_SUBCOMMANDS as _gh_read_only_alias,
    )
elif __package__:
    from ._github_mutation_analysis import (  # noqa: E402
        _GH_READ_ONLY_SUBCOMMANDS as _gh_read_only_alias,
    )
else:
    from _github_mutation_analysis import (  # noqa: E402
        _GH_READ_ONLY_SUBCOMMANDS as _gh_read_only_alias,
    )

_GH_READ_ONLY_SUBCOMMANDS = _gh_read_only_alias
del _gh_read_only_alias
