"""Interpreter and nested-shell payload classification helpers."""

from __future__ import annotations

import ast
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._command_classification import (
        _INTERPRETER_RE,
        _LITERAL_OPEN_PATH_RE,
        _LITERAL_PATH_CONSTRUCTOR_RE,
        _NESTED_SHELL_RE,
        _SUBPROCESS_APIS_RE,
        _WRITE_APIS_RE,
        _WRITE_CALL_SITE_RE,
        command_verb_and_args,
        tokenize_command_segments,
    )
else:
    if __package__:
        from . import _command_classification as _classification
    else:
        import _command_classification as _classification

    _INTERPRETER_RE = _classification._INTERPRETER_RE
    _LITERAL_OPEN_PATH_RE = _classification._LITERAL_OPEN_PATH_RE
    _LITERAL_PATH_CONSTRUCTOR_RE = _classification._LITERAL_PATH_CONSTRUCTOR_RE
    _NESTED_SHELL_RE = _classification._NESTED_SHELL_RE
    _SUBPROCESS_APIS_RE = _classification._SUBPROCESS_APIS_RE
    _WRITE_APIS_RE = _classification._WRITE_APIS_RE
    _WRITE_CALL_SITE_RE = _classification._WRITE_CALL_SITE_RE
    command_verb_and_args = _classification.command_verb_and_args
    tokenize_command_segments = _classification.tokenize_command_segments


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
