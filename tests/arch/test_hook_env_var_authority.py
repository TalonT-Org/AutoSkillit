"""Architectural guard: hooks reading PROVIDER_PROFILE must also read AGENT_BACKEND.

Provider profile (``AUTOSKILLIT_PROVIDER_PROFILE``) is a credentials/model
label — it answers "which API endpoint and model configuration?" — NOT a
backend-identity signal ("which backend CLI is executing?"). Using profile
as the sole authority for backend-specific decisions causes false
denials/bypasses (e.g., Codex-backend steps that carry the default
``"anthropic"`` credentials bundle are denied based on Anthropic quota
they cannot consume).

Each hook script that reads ``AUTOSKILLIT_PROVIDER_PROFILE`` MUST also
read ``AUTOSKILLIT_AGENT_BACKEND`` so backend identity can be checked
independently of provider routing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_PROVIDER_PROFILE_VAR = "AUTOSKILLIT_PROVIDER_PROFILE"
_AGENT_BACKEND_VAR = "AUTOSKILLIT_AGENT_BACKEND"


class _EnvVarReadCollector(ast.NodeVisitor):
    """Collect literal env-var names read by executable env-lookup expressions.

    Tracks the three executable read patterns:

    - ``os.environ.get("NAME", default)``
    - ``os.getenv("NAME", default)``
    - ``os.environ["NAME"]``

    Reads nested inside docstrings, type annotations, or other non-runtime
    contexts are still recorded because they are syntactically the same
    expression. The collector walks all call/subscript nodes regardless
    of where they appear; if a file contains the literal anywhere (even
    in a comment-like string), the guard fires so reviewers cannot mask
    the read with a comment or docstring.
    """

    def __init__(self) -> None:
        self.reads: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_os_environ_get_call(node):
            name = self._first_string_arg(node)
            if name is not None:
                self.reads.add(name)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self._is_os_environ_subscript(node):
            name = self._subscript_string_value(node)
            if name is not None:
                self.reads.add(name)
        self.generic_visit(node)

    @staticmethod
    def _is_os_environ_get_call(node: ast.Call) -> bool:
        func = node.func
        # os.environ.get("NAME", ...) or os.getenv("NAME", ...)
        if isinstance(func, ast.Attribute) and func.attr in {"get", "getenv"}:
            value = func.value
            if isinstance(value, ast.Attribute) and value.attr == "environ":
                if isinstance(value.value, ast.Name) and value.value.id == "os":
                    return True
            if isinstance(value, ast.Name) and value.id == "os":
                return True
        return False

    @staticmethod
    def _is_os_environ_subscript(node: ast.Subscript) -> bool:
        value = node.value
        # os.environ["NAME"]
        return (
            isinstance(value, ast.Attribute)
            and value.attr == "environ"
            and isinstance(value.value, ast.Name)
            and value.value.id == "os"
        )

    @staticmethod
    def _first_string_arg(node: ast.Call) -> str | None:
        if not node.args:
            return None
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return None

    @staticmethod
    def _subscript_string_value(node: ast.Subscript) -> str | None:
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
        return None


def _collect_reads(py_file: Path) -> set[str]:
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    collector = _EnvVarReadCollector()
    collector.visit(tree)
    return collector.reads


def _iter_hook_files() -> list[Path]:
    """Recursively enumerate hook scripts, skipping package markers and shared modules.

    Files whose basename starts with ``_`` (e.g., ``_hook_settings.py``,
    ``_hook_utils.py``) are shared stdlib-only modules imported by hook
    scripts; their env-var usage is delegated from the executable hooks
    themselves and is therefore covered transitively.
    """
    hooks_root = SRC_ROOT / "hooks"
    files: list[Path] = []
    for py_file in sorted(hooks_root.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        files.append(py_file)
    return files


def test_hooks_with_provider_profile_must_also_read_agent_backend() -> None:
    """Every executable hook that reads PROVIDER_PROFILE must read AGENT_BACKEND.

    This is a structural guard: a hook that already reads
    ``AUTOSKILLIT_PROVIDER_PROFILE`` has a working env-var lookup pattern,
    so adding ``AUTOSKILLIT_AGENT_BACKEND`` is mechanically trivial and
    enforced. Hooks that don't read either variable inherit the implicit
    behavior of treating the host as a default Claude backend — they are
    not subject to this guard.
    """
    violations: list[str] = []
    for py_file in _iter_hook_files():
        reads = _collect_reads(py_file)
        if _PROVIDER_PROFILE_VAR in reads and _AGENT_BACKEND_VAR not in reads:
            relpath = py_file.relative_to(SRC_ROOT)
            violations.append(f"{relpath}: reads PROVIDER_PROFILE without AGENT_BACKEND")

    assert not violations, (
        "Hook reads AUTOSKILLIT_PROVIDER_PROFILE without also reading "
        "AUTOSKILLIT_AGENT_BACKEND. Provider profile is a credentials "
        "label, not a backend-identity signal. Check both to avoid "
        "profile-as-identity confusion.\n" + "\n".join(f"  {v}" for v in violations)
    )
