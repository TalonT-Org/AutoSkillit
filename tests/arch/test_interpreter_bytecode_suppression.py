"""Architectural guard: every interpreter spawn in the hook pipeline must suppress bytecode.

Any site that constructs a Python interpreter invocation — ``sys.executable`` in
a subprocess argv list, or a rendered command string containing ``python3`` as a
command — must include ``-B`` in the argv unconditionally. An env-var assignment
(``PYTHONDONTWRITEBYTECODE``) is never an accepted *substitute* because isolated
(``-I``) spawns ignore ``PYTHON*`` env. Sites with a Python-level
``subprocess`` spawn must *additionally* set ``PYTHONDONTWRITEBYTECODE`` to
``"1"`` in the child env for ordinary-descendant coverage.

This guard turns any future unsuppressed spawn into an instant test failure.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit"

# Modules whose interpreter spawn sites are covered by this guard.
_GUARDED_MODULES: tuple[Path, ...] = (
    _SRC_ROOT / "hooks",
    _SRC_ROOT / "hook_registry.py",
    _SRC_ROOT / "execution" / "backends" / "_codex_hooks.py",
)

# Entries: (module_relpath, line_number) -> reason (≥40 chars).
# An exemption whose site no longer exists fails the orphan meta-test.
_BYTECODE_SUPPRESSION_EXEMPT: dict[tuple[str, int], str] = {}


def _python_files() -> list[Path]:
    """Collect every .py file in the guarded module set."""
    result: list[Path] = []
    for path in _GUARDED_MODULES:
        if path.is_file() and path.suffix == ".py":
            result.append(path)
        elif path.is_dir():
            result.extend(sorted(path.rglob("*.py")))
    return result


def _relpath(path: Path) -> str:
    return path.relative_to(_SRC_ROOT).as_posix()


# ---------------------------------------------------------------------------
# AST helpers — Case 1: sys.executable in list literals
# ---------------------------------------------------------------------------


def _is_sys_executable(node: ast.AST) -> bool:
    """Return True if ``node`` is ``sys.executable``."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "executable"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _list_contains_sys_executable(node: ast.List) -> bool:
    return any(_is_sys_executable(elt) for elt in node.elts)


def _list_contains_dash_b(node: ast.List) -> bool:
    """Check if a list literal contains the string ``"-B"``."""
    return any(isinstance(elt, ast.Constant) and elt.value == "-B" for elt in node.elts)


# ---------------------------------------------------------------------------
# AST helpers — Case 2: command-construction f-strings/strings
#
# Only flag strings that are used as dict values for a key named "command"
# in a dict literal — this is how hooks.json/settings.json/Codex config
# command entries are constructed.
# ---------------------------------------------------------------------------

_PYTHON3_CMD_RE = re.compile(r"\bpython3\s")
_PYTHON3_DASH_B_RE = re.compile(r"\bpython3\s+-B\b")


def _extract_string(node: ast.AST) -> str | None:
    """Extract a string from a Constant or JoinedStr."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
        return "".join(parts) if parts else None
    return None


def _find_command_string_violations(tree: ast.Module) -> list[tuple[int, str]]:
    """Find dict entries ``"command": f"python3 ..."`` missing ``-B``."""
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if value is None or key is None:
                continue
            if not (isinstance(key, ast.Constant) and key.value == "command"):
                continue
            text = _extract_string(value)
            if text is None:
                continue
            if _PYTHON3_CMD_RE.search(text) and not _PYTHON3_DASH_B_RE.search(text):
                violations.append((value.lineno, text))
    return violations


# Also check return statements in render/build command functions
def _find_return_command_violations(tree: ast.Module) -> list[tuple[int, str]]:
    """Find ``return f"python3 ..."`` in command-rendering functions missing ``-B``."""
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = node.name
        if not (
            name.startswith("render_")
            and "command" in name.lower()
            or name.startswith("_build_")
            and "command" in name.lower()
        ):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and child.value is not None:
                text = _extract_string(child.value)
                if text is None:
                    continue
                if _PYTHON3_CMD_RE.search(text) and not _PYTHON3_DASH_B_RE.search(text):
                    violations.append((child.lineno, text))
    return violations


# ---------------------------------------------------------------------------
# Violation collector
# ---------------------------------------------------------------------------


def _collect_violations() -> list[str]:
    """Find every interpreter spawn site missing ``-B`` suppression."""
    violations: list[str] = []
    for path in _python_files():
        rel = _relpath(path)
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))

        # Case 1: list containing sys.executable (subprocess argv)
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and _list_contains_sys_executable(node):
                if not _list_contains_dash_b(node):
                    key = (rel, node.lineno)
                    if key not in _BYTECODE_SUPPRESSION_EXEMPT:
                        violations.append(
                            f"{rel}:{node.lineno}: subprocess argv with "
                            f"sys.executable is missing -B flag"
                        )

        # Case 2: command-construction strings missing -B
        for lineno, text in _find_command_string_violations(tree):
            key = (rel, lineno)
            if key not in _BYTECODE_SUPPRESSION_EXEMPT:
                violations.append(
                    f"{rel}:{lineno}: command string contains python3 without -B flag: {text!r}"
                )

        # Case 3: return statements in command-rendering functions
        for lineno, text in _find_return_command_violations(tree):
            key = (rel, lineno)
            if key not in _BYTECODE_SUPPRESSION_EXEMPT:
                violations.append(
                    f"{rel}:{lineno}: render/build command function returns "
                    f"python3 command without -B flag: {text!r}"
                )

    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_every_interpreter_spawn_suppresses_bytecode() -> None:
    """No spawn site in the hook pipeline may produce bytecode in the tree."""
    violations = _collect_violations()
    assert not violations, (
        "Interpreter spawn sites found without -B bytecode suppression:\n"
        + "\n".join(f"  • {v}" for v in violations)
    )


def test_exemption_registry_has_no_orphans() -> None:
    """Every exemption must still correspond to a real spawn site."""
    if not _BYTECODE_SUPPRESSION_EXEMPT:
        return  # empty registry — nothing to check
    surviving: list[str] = []
    for (rel, lineno), _reason in _BYTECODE_SUPPRESSION_EXEMPT.items():
        path = _SRC_ROOT / rel
        if not path.is_file():
            surviving.append(f"{rel}:{lineno} — file no longer exists")
            continue
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and _list_contains_sys_executable(node):
                if node.lineno == lineno:
                    found = True
                    break
        for _vlineno, _vtext in _find_command_string_violations(tree):
            if _vlineno == lineno:
                found = True
                break
        for _vlineno, _vtext in _find_return_command_violations(tree):
            if _vlineno == lineno:
                found = True
                break
        if not found:
            surviving.append(f"{rel}:{lineno} — no spawn site at this line")
    assert not surviving, "Stale exemption entries in _BYTECODE_SUPPRESSION_EXEMPT:\n" + "\n".join(
        f"  • {s}" for s in surviving
    )


def test_exemption_reasons_are_substantive() -> None:
    """Every exemption reason must be at least 40 characters."""
    short: list[str] = []
    for (rel, lineno), reason in _BYTECODE_SUPPRESSION_EXEMPT.items():
        if len(reason) < 40:
            short.append(f"{rel}:{lineno} — reason is {len(reason)} chars: {reason!r}")
    assert not short, "Exemption reasons must be ≥40 chars:\n" + "\n".join(
        f"  • {s}" for s in short
    )
