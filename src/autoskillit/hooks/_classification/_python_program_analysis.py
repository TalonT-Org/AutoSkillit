"""AST-based extraction of subprocess/os calls from a Python program's source text.

Split out of `_interpreters.py` (rectify #4941 Part A) to keep that module's
stdin-consumer/evaluated-payload machinery under the REQ-CNST-010 line cap.
Self-contained: parses a Python -c program (or a heredoc/herestring-fed
`python3`/`python3 -` body) via the stdlib `ast` module and returns any
literal-argv or literal-string `subprocess.*`/`os.*` call it finds, with no
dependency on the rest of `_classification/`.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass

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
# Functions that always run their string argument through a shell,
# regardless of any `shell=` keyword (os.system/os.popen have none -- they
# unconditionally shell out). subprocess.* only invokes a shell when called
# with `shell=True`; a plain string passed without it is never split into
# words by a shell (see `_python_program_command_specs`).
_ALWAYS_SHELL_FUNCS: frozenset[str] = frozenset({"os.system", "os.popen"})


def _dotted_call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return None


def _parse_python_program_literals(program: str) -> list[ast.Call]:
    """Return subprocess/os call AST nodes found in a Python -c program."""
    try:
        tree = ast.parse(program, mode="exec")
    except SyntaxError:
        return []
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            dotted = _dotted_call_name(node)
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
    invokes_shell: bool = False


def _python_c_program(executable: str, args: Sequence[str]) -> str | None:
    """Return the literal program passed to a normalized Python executable's -c."""
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?", executable) is None:
        return None
    try:
        index = args.index("-c")
    except ValueError:
        return None
    return args[index + 1] if index + 1 < len(args) else None


def _python_program_command_specs(
    program: str,
) -> tuple[list[_InterpreterCommandSpec], bool]:
    specs: list[_InterpreterCommandSpec] = []
    has_unresolved = False
    for call in _parse_python_program_literals(program):
        dotted = _dotted_call_name(call)
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
        invokes_shell = dotted in _ALWAYS_SHELL_FUNCS or (
            isinstance(shell_arg, ast.Constant) and shell_arg.value is True
        )
        payload: str | list[str] | None
        if invokes_shell:
            payload = _literal_to_string(first)
        else:
            payload = _literal_to_argv(first)
            if payload is None:
                payload = _literal_to_string(first)
        if payload is None:
            has_unresolved = True
        else:
            specs.append(_InterpreterCommandSpec(payload, cwd, invokes_shell))
    return (specs, has_unresolved)


def _python_program_evaluated_specs(program: str) -> list[_InterpreterCommandSpec]:
    """Return specs that yield an executable argv or shell command."""
    specs, _has_unresolved = _python_program_command_specs(program)
    return [
        spec
        for spec in specs
        if (isinstance(spec.payload, list) and bool(spec.payload))
        or (isinstance(spec.payload, str) and spec.invokes_shell)
    ]
