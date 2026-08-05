"""Shared command classification primitives for guard scripts.

Supported interpreters: python3?, perl, ruby, node.
To add coverage for a new interpreter, update both _INTERPRETER_RE and
_INTERPRETER_LINE_RE.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlsplit

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


def tokenize_command_segments(command: str) -> list[list[str]]:
    """Split a shell command into segments of (verb, args...) token lists.

    Each segment is one logical command, separated by shell operators.
    Returns [] on shlex parse error (unclosed quotes).
    """
    try:
        stripped = _HEREDOC_MARKER_RE.sub(r"\2", strip_heredoc_bodies(command))
        lexer = shlex.shlex(
            _normalize_newlines_for_tokenize(stripped),
            posix=True,
            punctuation_chars=";&|",
        )
        lexer.whitespace_split = True
        tokens = list(lexer)
    except (ValueError, TypeError):
        return []

    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SHELL_OPERATORS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def extract_redirect_targets(tokens: list[str], cwd: str = "") -> list[str]:
    """Extract redirect target paths from shlex-tokenized command tokens.

    Returns resolved paths including pseudo-devices — caller filters.
    Relative paths are resolved against cwd when provided.

    Handles three redirect forms at depth 0 only:
    - Separate:  ['>', '/path'] or ['>>', '/path']
    - Split:     ['2>', '/path'] (operator-only token + next token)
    - Merged:    ['2>/path'] or ['2>>/path']

    Tracks subshell nesting via '(' and ')' — both standalone and fused
    with adjacent text (shlex merges '(' with following chars in POSIX mode).
    """
    targets: list[str] = []
    depth = 0
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "(" or (tok.startswith("(") and len(tok) > 1):
            depth += 1
            if tok.endswith(")") and len(tok) > 1:
                depth -= 1
            i += 1
            continue
        if tok == ")":
            if depth > 0:
                depth -= 1
            i += 1
            continue
        if tok.endswith(")") and len(tok) > 1:
            if depth > 0:
                depth -= 1
            i += 1
            continue
        if depth > 0:
            i += 1
            continue
        if tok in (">", ">>"):
            if i + 1 < len(tokens):
                path = tokens[i + 1]
                while path and path[-1] in _TRAILING_SHELL_CLOSERS:
                    path = path[:-1]
                resolved = resolve_write_target(path, cwd)
                if resolved is not None:
                    targets.append(resolved)
                i += 2
                continue
        elif _REDIRECT_OP_ONLY_RE.match(tok):
            if i + 1 < len(tokens):
                path = tokens[i + 1]
                while path and path[-1] in _TRAILING_SHELL_CLOSERS:
                    path = path[:-1]
                resolved = resolve_write_target(path, cwd)
                if resolved is not None:
                    targets.append(resolved)
                i += 2
                continue
        else:
            m = _REDIRECT_TOKEN_RE.match(tok)
            if m:
                path = m.group(2)
                while path and path[-1] in _TRAILING_SHELL_CLOSERS:
                    path = path[:-1]
                resolved = resolve_write_target(path, cwd)
                if resolved is not None:
                    targets.append(resolved)
        i += 1
    return targets


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


class GitHubMutationStatus(StrEnum):
    """Deterministic cardinality result for a shell command."""

    NONE = "none"
    SINGLE_RESOLVED = "single_resolved"
    MULTIPLE = "multiple"
    UNRESOLVED = "unresolved"


class GitHubMutationKind(StrEnum):
    """Closed mutation families relevant to GitHub review publication."""

    PULL_REVIEW = "pull_review"
    PULL_REVIEW_COMMENT = "pull_review_comment"
    PULL_REVIEW_REPLY = "pull_review_reply"
    GRAPHQL_REVIEW = "graphql_review"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class GitHubMutationRecord:
    method: str
    route: str
    kind: GitHubMutationKind
    request_count: int
    review_comment_count: int | None


@dataclass(frozen=True, slots=True)
class GitHubMutationAnalysis:
    status: GitHubMutationStatus
    mutations: tuple[GitHubMutationRecord, ...]
    request_count: int | None
    review_comment_count: int | None
    reason: str


_GITHUB_WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_GITHUB_INPUT_LIMIT = 1024 * 1024
_DYNAMIC_SHELL_TOKEN_RE = re.compile(r"\$|`|\*|\?|\[")
_REPEATABLE_SHELL_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:for|while|until)\b"
    r"|\b[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\)\s*\{"
)
_PROCESS_SUBSTITUTION_RE = re.compile(r"[<>]\(")
_POSSIBLE_GITHUB_EXEC_RE = re.compile(
    r"""(?:^|[\s;&|()'"])(?:[^\s;&|()'"]*/)?(?:gh|curl)(?:[\s'"]|$)""",
    re.IGNORECASE,
)
_GH_DISPATCH_WORDS: frozenset[str] = frozenset({"eval", "xargs", "source", "."})
_POSSIBLE_GITHUB_EXEC_NAMES: frozenset[str] = frozenset({"gh", "curl"})
_PULL_REVIEW_ROUTE_RE = re.compile(
    r"^/repos/[^/]+/[^/]+/pulls/\d+/reviews"
    r"(?:/\d+(?:/events)?)?/?$",
    re.IGNORECASE,
)
_PULL_REVIEW_REPLY_ROUTE_RE = re.compile(
    r"^/repos/[^/]+/[^/]+/pulls/\d+/comments/\d+/replies/?$",
    re.IGNORECASE,
)
_PULL_REVIEW_COMMENT_ROUTE_RE = re.compile(
    r"^/repos/[^/]+/[^/]+/(?:pulls/\d+/comments(?:/\d+)?|pulls/comments/\d+)/?$",
    re.IGNORECASE,
)
_GRAPHQL_REVIEW_MUTATIONS: frozenset[str] = frozenset(
    {
        "addPullRequestReview",
        "submitPullRequestReview",
        "dismissPullRequestReview",
        "deletePullRequestReview",
        "addPullRequestReviewComment",
        "addPullRequestReviewThread",
    }
)


def _segment_has_possible_github_exec_token(segment: Sequence[str]) -> bool:
    """True when gh/curl (path-normalized) is in command position within *segment*.

    Checks the segment's own command verb — via ``command_verb_and_args``,
    which already skips env/wrapper prefixes and loop control words like
    ``do``/``then`` — not every token. A bare-word argument that merely
    mentions ``gh``/``curl`` (e.g. ``echo "gh"``, which shlex collapses to
    the same tokens as an unquoted ``echo gh``) is never in command position
    and must not trip this check.

    An inline function definition (``name() {``) or a bare compound-command
    opener (``{``) fuses its body's first command into the same shlex
    segment as the opener — the segmenter only splits on shell operators
    like ``;``, never on ``{`` — so the body's own verb would otherwise be
    invisible to a verb-only check. When the segment's verb is such an
    opener, the check is re-applied to the token immediately following it.
    """
    verb, args = command_verb_and_args(list(segment))
    if _normalize_executable(verb) in _POSSIBLE_GITHUB_EXEC_NAMES:
        return True
    body: list[str] | None = None
    if verb == "{":
        body = args
    elif verb.endswith("()") and args[:1] == ["{"]:
        body = args[1:]
    if body is None:
        return False
    body_verb, _body_args = command_verb_and_args(body)
    return _normalize_executable(body_verb) in _POSSIBLE_GITHUB_EXEC_NAMES


def _segments_have_possible_github_exec_token(segments: Sequence[Sequence[str]]) -> bool:
    """True when any segment contains gh/curl as a standalone token.

    Used to bound repeatable shell constructs (for/while/until, an inline
    function body fused into its opener's segment): a possible-exec token
    reachable through repetition is cardinality-unresolved regardless of
    which segment it lands in after shlex splits the payload on ``;``.
    """
    return any(_segment_has_possible_github_exec_token(segment) for segment in segments)


def _segments_have_dispatch_word_exec_risk(segments: Sequence[Sequence[str]]) -> bool:
    """True when a segment opened by eval/xargs/source/. also mentions gh/curl.

    Scoped to the dispatch word's own segment, not the whole payload: a
    ``gh`` command appearing as an unrelated *sibling* segment — e.g.
    ``source .venv/bin/activate && gh pr view`` — is already independently
    walked and classified by the normal segment loop and must not be
    treated as hidden behind an unrelated dispatch word earlier on the
    same line. ``eval``/``xargs`` genuinely consume or hand off the
    following text to a fresh command, so their own segment's joined text
    is searched (not just its tokens) to catch eval's single quoted
    argument token.
    """
    for segment in segments:
        verb, _args = command_verb_and_args(list(segment))
        if verb not in _GH_DISPATCH_WORDS:
            continue
        if _POSSIBLE_GITHUB_EXEC_RE.search(" ".join(segment)):
            return True
    return False


def _none_github_analysis() -> GitHubMutationAnalysis:
    return GitHubMutationAnalysis(
        status=GitHubMutationStatus.NONE,
        mutations=(),
        request_count=0,
        review_comment_count=None,
        reason="",
    )


def _unresolved_github_analysis(
    reason: str,
    mutations: Sequence[GitHubMutationRecord] = (),
) -> GitHubMutationAnalysis:
    return GitHubMutationAnalysis(
        status=GitHubMutationStatus.UNRESOLVED,
        mutations=tuple(mutations),
        request_count=None,
        review_comment_count=None,
        reason=reason,
    )


def _is_dynamic_shell_value(value: str) -> bool:
    return bool(_DYNAMIC_SHELL_TOKEN_RE.search(value))


def _normalize_github_route(route: str) -> str:
    if route.startswith(("http://", "https://")):
        parsed = urlsplit(route)
        return parsed.path or "/"
    if not route.startswith("/"):
        return f"/{route}"
    return route


def _github_mutation_kind(route: str) -> GitHubMutationKind:
    normalized = _normalize_github_route(route)
    if _PULL_REVIEW_REPLY_ROUTE_RE.fullmatch(normalized):
        return GitHubMutationKind.PULL_REVIEW_REPLY
    if _PULL_REVIEW_COMMENT_ROUTE_RE.fullmatch(normalized):
        return GitHubMutationKind.PULL_REVIEW_COMMENT
    if _PULL_REVIEW_ROUTE_RE.fullmatch(normalized):
        return GitHubMutationKind.PULL_REVIEW
    return GitHubMutationKind.OTHER


def _json_object_without_duplicate_keys(raw: bytes) -> dict[str, Any]:
    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    if not isinstance(value, dict):
        raise ValueError("GitHub --input payload must be a JSON object")
    return value


def _load_literal_github_input(
    value: str,
    *,
    cwd: str,
) -> tuple[dict[str, Any] | None, str]:
    if value == "-":
        return (None, "GitHub --input stdin is unresolved")
    if not value or _is_dynamic_shell_value(value):
        return (None, "GitHub --input path is dynamic")
    if os.path.isabs(value):
        path = os.path.normpath(value)
    else:
        if not cwd or not os.path.isabs(cwd):
            return (None, "relative GitHub --input requires an absolute cwd")
        path = os.path.normpath(os.path.join(cwd, value))

    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            return (None, "GitHub --input must be a regular non-symlink file")
        if before.st_size > _GITHUB_INPUT_LIMIT:
            return (None, "GitHub --input exceeds the inspection limit")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            after = os.fstat(fd)
            if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (
                after.st_dev,
                after.st_ino,
            ):
                return (None, "GitHub --input file identity changed")
            chunks: list[bytes] = []
            remaining = _GITHUB_INPUT_LIMIT + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(fd)
        if len(raw) > _GITHUB_INPUT_LIMIT:
            return (None, "GitHub --input exceeds the inspection limit")
        return (_json_object_without_duplicate_keys(raw), "")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return (None, f"GitHub --input is not safely inspectable: {exc}")


_INPUT_SAFE_PRIOR_COMMANDS: frozenset[str] = frozenset(
    {"[", "cat", "echo", "false", "head", "ls", "printf", "pwd", "stat", "test", "true", "wc"}
)


def _segment_is_safe_before_literal_input(segment: Sequence[str], *, cwd: str) -> bool:
    """Return whether *segment* is proven unable to rewrite a later input file."""
    if extract_redirect_targets(list(segment), cwd):
        return False
    verb, _ = command_verb_and_args(list(segment))
    executable = _normalize_executable(verb)
    return executable in _INPUT_SAFE_PRIOR_COMMANDS


def _comment_count_from_payload(payload: dict[str, Any]) -> tuple[int | None, str]:
    if "comments" not in payload:
        return (None, "")
    comments = payload["comments"]
    if not isinstance(comments, list):
        return (None, "GitHub review comments must be a JSON array")
    return (len(comments), "")


def _flag_value(
    args: Sequence[str],
    index: int,
    *,
    long_name: str,
    short_name: str | None = None,
) -> tuple[str | None, int, bool]:
    token = args[index]
    if token == long_name or (short_name is not None and token == short_name):
        if index + 1 >= len(args):
            return (None, index + 1, False)
        return (args[index + 1], index + 2, True)
    if token.startswith(f"{long_name}="):
        return (token.split("=", 1)[1], index + 1, True)
    if short_name and token.startswith(short_name) and token != short_name:
        return (token[len(short_name) :], index + 1, True)
    return (None, index, False)


def _analyze_gh_api(
    args: Sequence[str],
    *,
    cwd: str,
    input_context_safe: bool,
) -> tuple[GitHubMutationRecord | None, str]:
    method: str | None = None
    route: str | None = None
    input_value: str | None = None
    field_values: list[str] = []
    has_body_fields = False
    paginate = False
    graphql = False
    i = 0

    while i < len(args):
        token = args[i]
        if token == "graphql" and route is None:
            graphql = True
            route = "/graphql"
            i += 1
            continue

        value, next_i, matched = _flag_value(args, i, long_name="--method", short_name="-X")
        if matched or token in {"--method", "-X"}:
            if not matched or value is None:
                return (None, "GitHub API method is missing")
            method = value.upper()
            i = next_i
            continue

        value, next_i, matched = _flag_value(args, i, long_name="--input")
        if matched or token == "--input":
            if not matched or value is None:
                return (None, "GitHub --input path is missing")
            input_value = value
            has_body_fields = True
            i = next_i
            continue

        field_match = False
        for long_name, short_name in (
            ("--field", "-F"),
            ("--raw-field", "-f"),
        ):
            value, next_i, matched = _flag_value(
                args, i, long_name=long_name, short_name=short_name
            )
            if matched or token in {long_name, short_name}:
                if not matched or value is None:
                    return (None, f"{long_name} value is missing")
                field_values.append(value)
                has_body_fields = True
                i = next_i
                field_match = True
                break
        if field_match:
            continue

        if token == "--paginate":
            paginate = True
            i += 1
            continue
        if token in {"-H", "--header", "--hostname", "--cache"}:
            if i + 1 >= len(args):
                return (None, f"{token} value is missing")
            i += 2
            continue
        if token.startswith(("--header=", "--hostname=", "--cache=")):
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        if route is None:
            route = token
            i += 1
            continue
        return (None, "multiple GitHub API routes are unresolved")

    if route is None:
        if method is not None or has_body_fields:
            return (None, "GitHub API route is missing")
        return (None, "")
    if _is_dynamic_shell_value(route):
        return (None, "GitHub API route is dynamic")
    if method is not None and _is_dynamic_shell_value(method):
        return (None, "GitHub API method is dynamic")

    payload: dict[str, Any] = {}
    if input_value is not None:
        if not input_context_safe:
            return (None, "a prior command may rewrite the inspected GitHub --input file")
        loaded, reason = _load_literal_github_input(input_value, cwd=cwd)
        if loaded is None:
            return (None, reason)
        payload = loaded

    effective_method = method or ("POST" if has_body_fields else "GET")
    normalized_route = _normalize_github_route(route)
    if effective_method not in _GITHUB_WRITE_METHODS:
        return (None, "")
    if paginate:
        return (None, "mutation request count is indeterminate with --paginate")

    comment_count, reason = _comment_count_from_payload(payload)
    if reason:
        return (None, reason)

    if graphql:
        query = payload.get("query")
        if query is None:
            for field in field_values:
                key, separator, value = field.partition("=")
                if separator and key == "query":
                    query = value
                    break
        if not isinstance(query, str) or _is_dynamic_shell_value(query):
            return (None, "GraphQL mutation document is unresolved")
        if not re.search(r"\bmutation\b", query):
            return (None, "")
        kind = (
            GitHubMutationKind.GRAPHQL_REVIEW
            if any(
                re.search(rf"\b{re.escape(name)}\b", query) for name in _GRAPHQL_REVIEW_MUTATIONS
            )
            else GitHubMutationKind.OTHER
        )
    else:
        kind = _github_mutation_kind(normalized_route)

    return (
        GitHubMutationRecord(
            method=effective_method,
            route=normalized_route,
            kind=kind,
            request_count=1,
            review_comment_count=comment_count,
        ),
        "",
    )


_GH_HELP_FLAGS: frozenset[str] = frozenset({"--help", "-h"})
# Value-taking flags across the gh subcommands this module classifies below,
# curated so the --help exemption cannot be spoofed by a flag's own value
# (e.g. `gh pr review 5 --approve --body --help`) without needing a full
# per-subcommand flag grammar — mirrors the hardcoded-per-command flag
# tables already used by _analyze_gh_api/_analyze_curl_segment.
_GH_KNOWN_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "--body",
        "-b",
        "--add-label",
        "--remove-label",
        "--add-assignee",
        "--remove-assignee",
        "--reason",
        "--target",
        "--visibility",
    }
)


def _gh_subcommand_table(spec: str) -> dict[str, frozenset[str]]:
    pairs = (row.split(":", 1) for row in spec.split(";"))
    return {noun: frozenset(verbs.split()) for noun, verbs in pairs}


_GH_MUTATION_SUBCOMMANDS = _gh_subcommand_table(
    "cache:delete;codespace:create delete edit rebuild stop;gist:create delete edit "
    "rename;gpg-key:add delete;issue:close comment create delete develop edit lock pin reopen "
    "transfer unlock unpin;label:clone create delete edit;pr:close comment edit lock merge "
    "ready reopen unlock;project:close copy create delete edit field-create field-delete "
    "item-add item-archive item-create item-delete item-edit link mark-template "
    "unlink;release:create delete delete-asset edit upload;repo:archive create delete edit "
    "fork rename sync unarchive;run:cancel delete rerun;secret:delete set;ssh-key:add "
    "delete;variable:delete set;workflow:disable enable run"
)
# `pr create` has its own guard; other unlisted mutation-capable verbs fail closed.
_GH_READ_ONLY_SUBCOMMANDS = _gh_subcommand_table(
    "cache:list;codespace:code cp jupyter list logs ports ssh view;gist:clone list "
    "view;gpg-key:list;issue:list status view;label:list;pr:checkout checks diff list status "
    "view;project:field-list item-list list view;release:download list verify verify-asset "
    "view;repo:clone list set-default view;run:download list view "
    "watch;secret:list;ssh-key:list;variable:list;workflow:list view"
)


def _gh_args_have_bare_help_flag(args: Sequence[str]) -> bool:
    """Return True when -h/--help appears as its own flag, not another flag's value."""
    i = 0
    n = len(args)
    while i < n:
        token = args[i]
        if token in _GH_HELP_FLAGS:
            return True
        if token in _GH_KNOWN_VALUE_FLAGS:
            i += 2
            continue
        i += 1
    return False


def _analyze_gh_segment(
    args: Sequence[str],
    *,
    cwd: str,
    input_context_safe: bool,
) -> tuple[GitHubMutationRecord | None, str]:
    if not args:
        return (None, "")
    if _gh_args_have_bare_help_flag(args[1:]):
        return (None, "")
    if args[:2] == ["pr", "create"]:
        return (None, "")
    if args[:2] == ["pr", "review"]:
        return (
            GitHubMutationRecord(
                method="POST",
                route="/gh/pr/review",
                kind=GitHubMutationKind.PULL_REVIEW,
                request_count=1,
                review_comment_count=None,
            ),
            "",
        )
    noun = args[0]
    mutation_verbs = _GH_MUTATION_SUBCOMMANDS.get(noun)
    if mutation_verbs is not None and len(args) >= 2:
        verb = args[1]
        if verb in _GH_READ_ONLY_SUBCOMMANDS[noun]:
            return (None, "")
        if verb not in mutation_verbs:
            return (None, f"gh {noun} {verb} mutation classification is unresolved")
        return (
            GitHubMutationRecord(
                method="POST",
                route=f"/gh/{noun}/{verb}",
                kind=GitHubMutationKind.OTHER,
                request_count=1,
                review_comment_count=None,
            ),
            "",
        )
    if args[0] != "api":
        return (None, "")
    return _analyze_gh_api(args[1:], cwd=cwd, input_context_safe=input_context_safe)


def _analyze_curl_segment(
    args: Sequence[str],
) -> tuple[list[GitHubMutationRecord], str]:
    method: str | None = None
    has_data = False
    force_get = False
    urls: list[str] = []
    saw_next = False
    i = 0
    data_flags = (
        ("--data", "-d"),
        ("--data-raw", None),
        ("--data-binary", None),
        ("--data-urlencode", None),
        ("--form", "-F"),
        ("--upload-file", "-T"),
    )
    value_flags = (("--header", "-H"), ("--user", "-u"), ("--output", "-o"))
    while i < len(args):
        token = args[i]
        value, next_i, matched = _flag_value(args, i, long_name="--request", short_name="-X")
        if matched or token in {"--request", "-X"}:
            if not matched or value is None:
                return ([], "curl method is missing")
            method = value.upper()
            i = next_i
            continue
        value, next_i, matched = _flag_value(args, i, long_name="--url")
        if matched or token == "--url":
            if not matched or value is None:
                return ([], "curl URL is missing")
            urls.append(value)
            i = next_i
            continue
        if token in {"-G", "--get"}:
            force_get = True
            i += 1
            continue
        if token == "--next":
            saw_next = True
            i += 1
            continue
        matched_value_flag = False
        for long_name, short_name in data_flags:
            value, next_i, matched = _flag_value(
                args,
                i,
                long_name=long_name,
                short_name=short_name,
            )
            if matched or token == long_name or (short_name is not None and token == short_name):
                if not matched or value is None:
                    return ([], f"{token} value is missing")
                has_data = True
                i = next_i
                matched_value_flag = True
                break
        if matched_value_flag:
            continue
        for long_name, short_name in value_flags:
            value, next_i, matched = _flag_value(
                args,
                i,
                long_name=long_name,
                short_name=short_name,
            )
            if matched or token == long_name or token == short_name:
                if not matched or value is None:
                    return ([], f"{token} value is missing")
                i = next_i
                matched_value_flag = True
                break
        if matched_value_flag:
            continue
        if token.startswith("-"):
            i += 1
            continue
        urls.append(token)
        i += 1

    if method is not None and _is_dynamic_shell_value(method):
        return ([], "curl method is dynamic")
    if any(_is_dynamic_shell_value(url) for url in urls):
        return ([], "curl URL is dynamic")
    github_urls = []
    for url in urls:
        hostname = urlsplit(url).hostname
        if hostname is not None and hostname.lower() in {"api.github.com", "github.com"}:
            github_urls.append(url)
    if not github_urls:
        return ([], "")
    effective_method = method or ("GET" if force_get else ("POST" if has_data else "GET"))
    if effective_method not in _GITHUB_WRITE_METHODS:
        return ([], "")
    if saw_next or len(github_urls) != 1 or len(urls) != 1:
        return ([], "curl mutation request count is indeterminate")
    route = urlsplit(github_urls[0]).path or "/"
    return (
        [
            GitHubMutationRecord(
                method=effective_method,
                route=route,
                kind=_github_mutation_kind(route),
                request_count=1,
                review_comment_count=None,
            )
        ],
        "",
    )


def _segment_cwd(segment: Sequence[str], cwd: str) -> str:
    current = cwd
    for index, token in enumerate(segment):
        if token in {"-C", "--chdir"} and index and segment[index - 1] == "env":
            if index + 1 < len(segment):
                value = segment[index + 1]
                if os.path.isabs(value):
                    current = value
                elif current:
                    current = os.path.normpath(os.path.join(current, value))
        elif token.startswith("--chdir=") and "env" in segment[:index]:
            value = token.split("=", 1)[1]
            if os.path.isabs(value):
                current = value
            elif current:
                current = os.path.normpath(os.path.join(current, value))
    return current


def _analyze_github_segment(
    segment: Sequence[str],
    *,
    cwd: str,
    input_context_safe: bool = True,
) -> tuple[list[GitHubMutationRecord], str]:
    verb, args = command_verb_and_args(list(segment))
    executable = _normalize_executable(verb)
    if executable == "gh":
        record, reason = _analyze_gh_segment(
            args,
            cwd=_segment_cwd(segment, cwd),
            input_context_safe=input_context_safe,
        )
        return (([record] if record is not None else []), reason)
    if executable == "curl":
        return _analyze_curl_segment(args)
    return ([], "")


def analyze_github_mutations(
    command: str,
    *,
    cwd: str = "",
) -> GitHubMutationAnalysis:
    """Classify all reachable mutations, treating uncertainty as absorbing."""
    if not isinstance(command, str) or not command.strip():
        return _none_github_analysis()

    records: list[GitHubMutationRecord] = []
    reasons: list[str] = []
    queue: list[tuple[str, str, int]] = [(command, cwd, 0)]
    argv_payloads: list[tuple[list[str], str]] = []

    while queue:
        payload, payload_cwd, depth = queue.pop(0)
        if depth > 32:
            reasons.append("nested mutation command depth is unresolved")
            continue
        segments = tokenize_command_segments(payload)
        if not segments and payload.strip():
            if _POSSIBLE_GITHUB_EXEC_RE.search(payload):
                reasons.append("mutation-bearing shell payload could not be parsed")
            continue

        current_cwd = payload_cwd
        input_context_safe = True
        for segment in segments:
            verb, args = command_verb_and_args(segment)
            if _normalize_executable(verb) == "cd":
                if len(args) != 1 or _is_dynamic_shell_value(args[0]):
                    reasons.append("shell cwd transition is unresolved")
                elif os.path.isabs(args[0]):
                    current_cwd = os.path.normpath(args[0])
                elif current_cwd:
                    current_cwd = os.path.normpath(os.path.join(current_cwd, args[0]))
                else:
                    reasons.append("relative shell cwd transition has no authority")
                continue
            found, reason = _analyze_github_segment(
                segment,
                cwd=current_cwd,
                input_context_safe=input_context_safe,
            )
            records.extend(found)
            if reason:
                reasons.append(reason)

            interpreter_specs, has_unresolved = _extract_interpreter_segment_specs(segment)
            if has_unresolved and _POSSIBLE_GITHUB_EXEC_RE.search(payload):
                reasons.append("interpreter subprocess command or cwd is unresolved")
            for spec in interpreter_specs:
                interpreter_cwd = current_cwd
                if spec.cwd is not None:
                    if os.path.isabs(spec.cwd):
                        interpreter_cwd = os.path.normpath(spec.cwd)
                    elif current_cwd:
                        interpreter_cwd = os.path.normpath(os.path.join(current_cwd, spec.cwd))
                    else:
                        reasons.append("relative interpreter cwd has no authority")
                        continue
                if isinstance(spec.payload, str):
                    queue.append((spec.payload, interpreter_cwd, depth + 1))
                else:
                    argv_payloads.append((spec.payload, interpreter_cwd))

            input_context_safe = input_context_safe and _segment_is_safe_before_literal_input(
                segment,
                cwd=current_cwd,
            )

        for nested in extract_shell_command_payloads(payload):
            queue.append((nested, current_cwd, depth + 1))

        if (
            _REPEATABLE_SHELL_RE.search(payload) or _PROCESS_SUBSTITUTION_RE.search(payload)
        ) and _segments_have_possible_github_exec_token(segments):
            reasons.append("mutation cardinality is unresolved in a shell wrapper")
        if _segments_have_dispatch_word_exec_risk(segments):
            reasons.append("mutation cardinality is unresolved in a shell wrapper")

    for argv, argv_cwd in argv_payloads:
        found, reason = _analyze_github_segment(argv, cwd=argv_cwd)
        records.extend(found)
        if reason:
            reasons.append(reason)

    if reasons:
        return _unresolved_github_analysis("; ".join(dict.fromkeys(reasons)), records)
    request_count = sum(record.request_count for record in records)
    if request_count == 0:
        return _none_github_analysis()
    if request_count != 1 or len(records) != 1:
        return GitHubMutationAnalysis(
            status=GitHubMutationStatus.MULTIPLE,
            mutations=tuple(records),
            request_count=request_count,
            review_comment_count=None,
            reason="",
        )
    record = records[0]
    return GitHubMutationAnalysis(
        status=GitHubMutationStatus.SINGLE_RESOLVED,
        mutations=(record,),
        request_count=1,
        review_comment_count=record.review_comment_count,
        reason="",
    )
