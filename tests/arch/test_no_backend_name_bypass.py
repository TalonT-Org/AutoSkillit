"""Backend-specific behavior must use capability fields, not name comparisons."""

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

# Exempt files where backend name comparison is structurally necessary.
_EXEMPT_FILES: frozenset[str] = frozenset(
    {
        "execution/backends/__init__.py",  # BACKEND_REGISTRY lookup
        "server/_factory.py",  # Feature-gated backend swap (pre-capabilities)
        "cli/doctor/_doctor_runtime.py",  # Binary existence checks
        "cli/doctor/_doctor_mcp.py",  # MCP config path checks
        "cli/_init_helpers.py",  # CLI init — string config, no CodingAgentBackend
        "cli/_marketplace.py",  # Marketplace — Claude Code-only install guard
        "execution/recording.py",  # Recording format dispatch (pre-capabilities context)
        "execution/headless/_headless_evidence.py",  # Claude-specific evidence extraction
        "execution/headless/_headless_result.py",  # Claude-specific result parsing
        "server/tools/tools_execution.py",  # Provider-override backend routing
        "core/_version_snapshot.py",  # Version snapshot — routes codex_version by backend name
        "execution/headless/_headless_helpers.py",  # assert_headless_cmd claude -p flag check
        "server/_lifespan.py",  # Codex MCP registration gate
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
