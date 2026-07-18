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
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Protocol

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

OUTPUT_BUDGET_MAX_COMMAND_CHARS = 65_536
OUTPUT_BUDGET_MAX_NESTING_DEPTH = 8


class CommandBudgetDisposition(Enum):
    BOUNDED = "bounded"
    HAZARDOUS = "hazardous"
    UNKNOWN = "unknown"


# A producer classifier returns whether the producer's stdout and stderr are
# intrinsically bounded, respectively.  Returning None means the segment is
# outside the caller's risky-producer policy.
OutputBudgetProducerClassifier = Callable[[list[str]], tuple[bool, bool] | None]


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


def tokenize_command_structure(command: str) -> list[tuple[str, list[str]]] | None:
    """Tokenize shell segments while retaining the connector before each segment."""
    if len(command) > OUTPUT_BUDGET_MAX_COMMAND_CHARS:
        return None
    try:
        stripped = _HEREDOC_MARKER_RE.sub(r"\2", strip_heredoc_bodies(command))
        lexer = shlex.shlex(
            _normalize_newlines_for_tokenize(stripped), posix=True, punctuation_chars=";&|"
        )
        lexer.whitespace_split = True
        raw_tokens = list(lexer)
    except (ValueError, TypeError):
        return None
    tokens: list[str] = []
    i = 0
    while i < len(raw_tokens):
        token = raw_tokens[i]
        # shlex splits the ampersand in descriptor duplication (2>&1) because
        # ampersand is also shell punctuation.  Recombine only when the token
        # before it is a redirect operator and the target is a literal fd.
        if (
            re.fullmatch(r"\d*>{1,2}", token)
            and i + 2 < len(raw_tokens)
            and raw_tokens[i + 1] == "&"
            and re.fullmatch(r"\d+|-", raw_tokens[i + 2])
        ):
            tokens.append(f"{token}&{raw_tokens[i + 2]}")
            i += 3
            continue
        tokens.append(token)
        i += 1
    result: list[tuple[str, list[str]]] = []
    current: list[str] = []
    connector = ""
    for token in tokens:
        if token in {"&&", "||", ";", "|", "|&", "&"}:
            if current:
                result.append((connector, current))
                current = []
            connector = token
        else:
            current.append(token)
    if current:
        result.append((connector, current))
    return result


_OUTPUT_REDIRECT_RE = re.compile(r"^(?P<fd>[012]?)(?P<op>>{1,2})(?P<target>.*)$")
_OUTPUT_FD_DUP_RE = re.compile(r"^(?P<fd>[012]?)(?P<op>>{1,2})&(?P<target>\d+|-)$")
_OUTPUT_DYNAMIC_TARGET_RE = re.compile(r"[$`*?\[\]{}]")
_OUTPUT_UNSUPPORTED_SYNTAX_RE = re.compile(r"[<>]\(|<<<|(?:[3-9]\d*)>&|>&-")
_OUTPUT_BYTE_CAP_VERBS: frozenset[str] = frozenset({"head", "tail"})

_DEST_MODEL = "model"
_DEST_PIPE = "pipe"
_DEST_SAFE = "safe"
_DEST_UNSAFE = "unsafe"
_DEST_UNKNOWN = "unknown"


def _is_project_temp_target(target: str, cwd: str) -> bool:
    if target == "/dev/null":
        return True
    if not target or _OUTPUT_DYNAMIC_TARGET_RE.search(target):
        return False
    root = os.path.realpath(cwd or os.getcwd())
    temp_root = os.path.join(root, ".autoskillit", "temp")
    resolved = os.path.realpath(target if os.path.isabs(target) else os.path.join(root, target))
    try:
        return os.path.commonpath((temp_root, resolved)) == temp_root
    except ValueError:
        return False


def _redirect_target_destination(target: str, cwd: str) -> str:
    if not target or _OUTPUT_DYNAMIC_TARGET_RE.search(target):
        return _DEST_UNKNOWN
    return _DEST_SAFE if _is_project_temp_target(target, cwd) else _DEST_UNSAFE


def _segment_output_destinations(
    tokens: list[str], *, stdout_base: str, stderr_base: str, cwd: str
) -> tuple[str, str, list[str], bool]:
    """Apply ordered stdout/stderr redirects and return remaining argv.

    The bool result reports unsupported or ambiguous descriptor syntax.  The
    destination copy for ``2>&1`` is intentionally immediate, matching shell
    left-to-right redirection semantics.
    """
    stdout = stdout_base
    stderr = stderr_base
    argv: list[str] = []
    unknown = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        fd_dup = _OUTPUT_FD_DUP_RE.fullmatch(token)
        if fd_dup:
            fd = int(fd_dup.group("fd") or "1")
            target = fd_dup.group("target")
            if fd not in {1, 2} or target not in {"1", "2"}:
                unknown = True
            elif fd == 1:
                stdout = stdout if target == "1" else stderr
            else:
                stderr = stdout if target == "1" else stderr
            i += 1
            continue

        redirect = _OUTPUT_REDIRECT_RE.fullmatch(token)
        if redirect:
            fd = int(redirect.group("fd") or "1")
            target = redirect.group("target")
            if not target:
                if i + 1 >= len(tokens):
                    unknown = True
                    i += 1
                    continue
                target = tokens[i + 1]
                i += 1
            destination = _redirect_target_destination(target, cwd)
            if fd == 1:
                stdout = destination
            elif fd == 2:
                stderr = destination
            else:
                unknown = True
            i += 1
            continue

        if token.startswith("<") or re.match(r"\d+<", token):
            # Ordinary literal stdin redirection does not affect model-visible
            # output.  Dynamic/process forms were rejected before tokenization.
            if _OUTPUT_DYNAMIC_TARGET_RE.search(token):
                unknown = True
            argv.append(token)
            i += 1
            continue
        argv.append(token)
        i += 1
    return (stdout, stderr, argv, unknown)


def command_tokens_without_output_redirections(tokens: list[str]) -> list[str] | None:
    """Return command argv with stdout/stderr redirects removed.

    ``None`` means descriptor syntax was ambiguous or unsupported.  This is
    primarily exposed for stdlib-only guards that need to classify arguments
    without accidentally treating redirect targets as input paths.
    """
    _stdout, _stderr, argv, unknown = _segment_output_destinations(
        tokens, stdout_base=_DEST_MODEL, stderr_base=_DEST_MODEL, cwd=os.getcwd()
    )
    return None if unknown else argv


def _byte_cap_value(argv: list[str], max_inline_bytes: int) -> int | None:
    verb, args = command_verb_and_args(argv)
    if os.path.basename(verb) not in _OUTPUT_BYTE_CAP_VERBS:
        return None
    value: str | None = None
    for index, arg in enumerate(args):
        if arg in {"-c", "--bytes"} and index + 1 < len(args):
            value = args[index + 1]
            break
        if arg.startswith("-c") and len(arg) > 2:
            value = arg[2:]
            break
        if arg.startswith("--bytes="):
            value = arg.partition("=")[2]
            break
    if value is None or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if 0 < parsed <= max_inline_bytes else None


def _combine_budget_dispositions(
    left: CommandBudgetDisposition, right: CommandBudgetDisposition
) -> CommandBudgetDisposition:
    if CommandBudgetDisposition.UNKNOWN in {left, right}:
        return CommandBudgetDisposition.UNKNOWN
    if CommandBudgetDisposition.HAZARDOUS in {left, right}:
        return CommandBudgetDisposition.HAZARDOUS
    return CommandBudgetDisposition.BOUNDED


def _trace_pipeline_destination(
    parsed: list[tuple[str, list[str]]],
    producer_index: int,
    destination: str,
    *,
    max_inline_bytes: int,
    cwd: str,
    input_already_bounded: bool = False,
) -> CommandBudgetDisposition:
    if destination == _DEST_SAFE:
        return CommandBudgetDisposition.BOUNDED
    if destination in {_DEST_MODEL, _DEST_UNSAFE}:
        return CommandBudgetDisposition.HAZARDOUS
    if destination == _DEST_UNKNOWN:
        return CommandBudgetDisposition.UNKNOWN

    # The content is on a pipeline.  It remains unbounded until a terminal
    # byte cap; every intermediate stderr must stay in that pipeline or go to
    # an approved sink, otherwise that branch remains model-visible.
    index = producer_index + 1
    while index < len(parsed) and parsed[index][0] in {"|", "|&"}:
        connector, tokens = parsed[index]
        next_connector = parsed[index + 1][0] if index + 1 < len(parsed) else ""
        has_next_pipe = next_connector in {"|", "|&"}
        stdout_base = _DEST_PIPE if has_next_pipe else _DEST_MODEL
        stderr_base = _DEST_PIPE if has_next_pipe and next_connector == "|&" else _DEST_MODEL
        stdout, stderr, argv, unknown = _segment_output_destinations(
            tokens, stdout_base=stdout_base, stderr_base=stderr_base, cwd=cwd
        )
        if unknown or os.path.basename(command_verb(argv)) == "tee":
            return CommandBudgetDisposition.UNKNOWN
        cap = _byte_cap_value(argv, max_inline_bytes)
        if cap is not None:
            # A downstream transform can amplify a capped stream, so only a
            # terminal cap (or one writing directly to a safe sink) proves the
            # model-visible response bound.
            if has_next_pipe:
                return CommandBudgetDisposition.UNKNOWN
            if stdout == _DEST_UNKNOWN:
                return CommandBudgetDisposition.UNKNOWN
            return (
                CommandBudgetDisposition.BOUNDED
                if stdout in {_DEST_MODEL, _DEST_SAFE}
                else CommandBudgetDisposition.HAZARDOUS
            )
        if input_already_bounded:
            # An arbitrary transform may amplify even a small source record.
            # Without a terminal byte cap or safe-file sink its output bound is
            # not statically knowable.
            return (
                CommandBudgetDisposition.BOUNDED
                if stdout == _DEST_SAFE
                else CommandBudgetDisposition.UNKNOWN
            )
        if stdout == _DEST_SAFE:
            return CommandBudgetDisposition.BOUNDED
        if stdout != _DEST_PIPE:
            return (
                CommandBudgetDisposition.UNKNOWN
                if stdout == _DEST_UNKNOWN
                else CommandBudgetDisposition.HAZARDOUS
            )
        if stderr not in {_DEST_PIPE, _DEST_SAFE}:
            return (
                CommandBudgetDisposition.UNKNOWN
                if stderr == _DEST_UNKNOWN
                else CommandBudgetDisposition.HAZARDOUS
            )
        index += 1
    return CommandBudgetDisposition.HAZARDOUS


def _classify_parsed_output_budget(
    parsed: list[tuple[str, list[str]]],
    producer_classifier: OutputBudgetProducerClassifier,
    *,
    max_inline_bytes: int,
    cwd: str,
) -> CommandBudgetDisposition:
    profiles = [producer_classifier(tokens) for _connector, tokens in parsed]
    if not any(profile is not None for profile in profiles):
        return CommandBudgetDisposition.BOUNDED

    disposition = CommandBudgetDisposition.BOUNDED
    for index, profile in enumerate(profiles):
        if profile is None:
            continue
        if any(os.path.basename(command_verb(tokens)) == "tee" for _c, tokens in parsed):
            return CommandBudgetDisposition.UNKNOWN
        connector_after = parsed[index + 1][0] if index + 1 < len(parsed) else ""
        stdout_base = _DEST_PIPE if connector_after in {"|", "|&"} else _DEST_MODEL
        stderr_base = _DEST_PIPE if connector_after == "|&" else _DEST_MODEL
        stdout, stderr, _argv, unknown = _segment_output_destinations(
            parsed[index][1], stdout_base=stdout_base, stderr_base=stderr_base, cwd=cwd
        )
        if unknown:
            return CommandBudgetDisposition.UNKNOWN
        stdout_intrinsic, stderr_intrinsic = profile
        for stream_intrinsic, destination in (
            (stdout_intrinsic, stdout),
            (stderr_intrinsic, stderr),
        ):
            if stream_intrinsic:
                stream_disposition = (
                    _trace_pipeline_destination(
                        parsed,
                        index,
                        destination,
                        max_inline_bytes=max_inline_bytes,
                        cwd=cwd,
                        input_already_bounded=True,
                    )
                    if destination == _DEST_PIPE
                    else CommandBudgetDisposition.BOUNDED
                )
            else:
                stream_disposition = _trace_pipeline_destination(
                    parsed,
                    index,
                    destination,
                    max_inline_bytes=max_inline_bytes,
                    cwd=cwd,
                )
            disposition = _combine_budget_dispositions(disposition, stream_disposition)
    return disposition


def classify_command_output_budget(
    command: str,
    producer_classifier: OutputBudgetProducerClassifier,
    *,
    max_inline_bytes: int,
    cwd: str = "",
    _depth: int = 0,
) -> CommandBudgetDisposition:
    """Classify whether every risky producer has a proven bounded output path.

    The caller supplies the producer policy so this shared parser remains
    independent of any one guard.  Commands outside that policy are BOUNDED;
    malformed, over-limit, deeply nested, or unsupported shell structures are
    UNKNOWN and therefore cannot establish a guard exception.
    """
    if (
        _depth > OUTPUT_BUDGET_MAX_NESTING_DEPTH
        or len(command) > OUTPUT_BUDGET_MAX_COMMAND_CHARS
        or max_inline_bytes <= 0
    ):
        return CommandBudgetDisposition.UNKNOWN
    if _OUTPUT_UNSUPPORTED_SYNTAX_RE.search(command):
        return CommandBudgetDisposition.UNKNOWN
    parsed = tokenize_command_structure(command)
    if parsed is None or (not parsed and command.strip()):
        return CommandBudgetDisposition.UNKNOWN
    if any(connector == "&" for connector, _tokens in parsed):
        return CommandBudgetDisposition.UNKNOWN

    root = cwd or os.getcwd()
    disposition = _classify_parsed_output_budget(
        parsed, producer_classifier, max_inline_bytes=max_inline_bytes, cwd=root
    )
    seen: set[str] = set()
    for payload in extract_shell_command_payloads(command):
        if payload in seen:
            continue
        seen.add(payload)
        child = classify_command_output_budget(
            payload,
            producer_classifier,
            max_inline_bytes=max_inline_bytes,
            cwd=root,
            _depth=_depth + 1,
        )
        disposition = _combine_budget_dispositions(disposition, child)
    return disposition


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

    Walks the outer command and every extracted payload recursively with a
    ``seen`` set so nested ``bash -c``, ``eval``, and active-substitution
    payloads are tokenized once without loops. Each successfully parsed
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
    """Quote/escape-aware state machine that returns active substitution bodies."""
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
                    payloads.extend(_extract_substitution_payloads(inner))
                    j = inner_end + 1
                    continue
                if command[j] == "$" and j + 1 < n and command[j + 1] == "(":
                    k = _find_substitution_end(command, j + 2)
                    inner = command[j + 2 : k]
                    payloads.append(inner)
                    payloads.extend(_extract_substitution_payloads(inner))
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
            payloads.extend(_extract_substitution_payloads(inner))
            i = j + 1
            continue
        if c == "$" and i + 1 < n and command[i + 1] == "(":
            j = _find_substitution_end(command, i + 2)
            inner = command[i + 2 : j]
            payloads.append(inner)
            payloads.extend(_extract_substitution_payloads(inner))
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


def extract_interpreter_command_payloads(command: str) -> tuple[list[str | list[str]], bool]:
    """Return (literal command payloads, has_unresolved_subprocess).

    Each payload is either a shell-command string (for `shell=True` calls or
    `os.system`) or an argv token list (for list/tuple literal subprocess
    calls). has_unresolved_subprocess is True when a matching process-launch
    call was present but its arguments could not be statically resolved.
    """
    payloads: list[str | list[str]] = []
    has_unresolved = False
    if not _INTERPRETER_RE.search(command):
        return (payloads, has_unresolved)
    if not _SUBPROCESS_APIS_RE.search(command):
        return (payloads, has_unresolved)

    # Find each `python* -c "<payload>"` invocation.
    py_re = re.compile(
        r"(?:^|&&|\|\||;)\s*(?:env\s+)?(?:python3?(?:\.\d+)?)\s+-c\s+(['\"])(.*?)\1",
        re.DOTALL,
    )
    for match in py_re.finditer(command):
        program = match.group(2)
        for call in _parse_python_program_literals(program):
            args = call.args
            if not args:
                continue
            first = args[0]
            # shell=True with a string first argument
            shell_arg = next((kw.value for kw in call.keywords if kw.arg == "shell"), None)
            is_shell_true = bool(
                shell_arg is not None
                and isinstance(shell_arg, ast.Constant)
                and shell_arg.value is True
            )
            if is_shell_true:
                cmd_str = _literal_to_string(first)
                if cmd_str is not None:
                    payloads.append(cmd_str)
                    continue
                has_unresolved = True
                continue
            # List/tuple argv literal
            argv = _literal_to_argv(first)
            if argv is not None:
                payloads.append(argv)
                continue
            # String first argument interpreted as shell via shell=True not set;
            # treat as shell-command string for os.system/Popen defaults too.
            cmd_str = _literal_to_string(first)
            if cmd_str is not None:
                payloads.append(cmd_str)
                continue
            has_unresolved = True
    return (payloads, has_unresolved)
