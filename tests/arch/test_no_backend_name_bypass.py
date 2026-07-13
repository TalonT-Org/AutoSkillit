"""Backend-specific behavior must use capability fields, not name comparisons."""

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

# Exempt files where backend name comparison is structurally necessary.
_EXEMPT_FILES: frozenset[str] = frozenset(
    {
        # Pre-instantiation config guard: backend object not yet constructed
        "server/_factory.py",
        # Pre-instantiation config guard: explicit override string before backend object
        "server/tools/_auto_overrides.py",
        # Cassette format from filenames; selects api_simulator player (no backend at replay time)
        "execution/recording.py",
        # Claude-specific JSONL stdout format; parse_session_result() is Claude-only
        "execution/headless/_headless_result.py",
        # IL-0 module: cannot import BackendCapabilities (IL-1); routes version data by name
        "core/_version_snapshot.py",
        # Binary name check on CmdSpec subprocess argv; no backend context in assert_headless_cmd()
        "execution/headless/_headless_helpers.py",
        # FeatureDef.requires_backend_alignment is config-layer; no capabilities at scan time
        "cli/session/_session_launch.py",
    }
)

_BACKEND_NAME_LITERALS: frozenset[str] = frozenset({"claude-code", "codex", "claude"})
_BACKEND_NAME_CONSTANTS: frozenset[str] = frozenset(
    {"AGENT_BACKEND_CLAUDE_CODE", "AGENT_BACKEND_CODEX"}
)


class _BackendNameComparisonVisitor(ast.NodeVisitor):
    """Find backend-name comparisons using string literals or AGENT_BACKEND_* constants."""

    def __init__(self) -> None:
        self.violations: list[int] = []

    def _is_backend_ref(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Constant) and node.value in _BACKEND_NAME_LITERALS:
            return True
        if isinstance(node, ast.Name) and node.id in _BACKEND_NAME_CONSTANTS:
            return True
        if isinstance(node, ast.Set):
            return any(self._is_backend_ref(elt) for elt in node.elts)
        return False

    def visit_Compare(self, node: ast.Compare) -> None:
        for comparator in node.comparators:
            if self._is_backend_ref(comparator):
                self.violations.append(node.lineno)
        if self._is_backend_ref(node.left):
            self.violations.append(node.lineno)
        self.generic_visit(node)


def test_no_backend_name_string_comparisons():
    """Production code must not compare backend names directly — use BackendCapabilities fields."""
    from autoskillit.core import paths

    src_root = paths.pkg_root()
    violations: list[str] = []
    for py_file in src_root.rglob("*.py"):
        relpath = str(py_file.relative_to(src_root))
        if relpath in _EXEMPT_FILES:
            continue
        if relpath.startswith("hooks/"):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        visitor = _BackendNameComparisonVisitor()
        visitor.visit(tree)
        for lineno in visitor.violations:
            violations.append(f"{relpath}:{lineno}")

    assert not violations, (
        "Backend-name string comparisons found (use BackendCapabilities fields instead):\n"
        + "\n".join(f"  {v}" for v in sorted(violations))
    )
