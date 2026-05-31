"""Guard: CmdSpec.is_resume must be used; no "--resume" in <expr>.cmd string matches."""

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


class _ResumeStringMatchVisitor(ast.NodeVisitor):
    """Detect `"--resume" in <expr>` patterns that should use CmdSpec.is_resume."""

    def __init__(self) -> None:
        self.violations: list[int] = []

    def visit_Compare(self, node: ast.Compare) -> None:
        for op in node.ops:
            if not isinstance(op, ast.In):
                continue
            if isinstance(node.left, ast.Constant) and node.left.value == "--resume":
                self.violations.append(node.lineno)
        self.generic_visit(node)


def test_no_resume_string_match_in_spec_cmd():
    """No production code may use `"--resume" in <expr>` — use CmdSpec.is_resume instead."""
    from autoskillit.core import paths

    src_root = paths.pkg_root()
    violations: list[str] = []
    for py_file in src_root.rglob("*.py"):
        relpath = str(py_file.relative_to(src_root))
        if relpath.startswith("hooks/"):
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        visitor = _ResumeStringMatchVisitor()
        visitor.visit(tree)
        for lineno in visitor.violations:
            violations.append(f"{relpath}:{lineno}")

    assert not violations, (
        'Found `"--resume" in <expr>` patterns — use CmdSpec.is_resume instead:\n'
        + "\n".join(f"  {v}" for v in sorted(violations))
    )
