from __future__ import annotations

import ast
from pathlib import Path


def _physical_line_count(source: str) -> int:
    """Count lines on the tokenizer's numbering.

    ``Path.read_text`` already normalises CR and CRLF endings to LF; counting LF
    (plus an unterminated final line) keeps this total on the same numbering as
    ``ast`` ``lineno``/``end_lineno``. ``str.splitlines`` does not: it also splits on
    form feeds and other separators the tokenizer treats as whitespace.
    """
    if not source:
        return 0
    return source.count("\n") + (0 if source.endswith("\n") else 1)


def count_budget_lines(path: Path) -> int:
    """Return the REQ-CNST-010 line count for ``path``: physical lines minus import lines.

    Every physical line occupied by an ``import``/``from ... import`` statement is
    excluded wherever it appears (module level, under ``if TYPE_CHECKING:``, inside
    ``try``, inside a function) and however it is laid out (single line, parenthesised
    block, backslash continuation). Blank and comment-only lines within a multiline
    import span are excluded too. Every line outside those spans counts, including
    separately laid-out enclosing statements, blanks, comments, docstrings and
    ``__all__`` lists. Shared import lines are subtracted only once. A file Python
    cannot parse raises ``SyntaxError``; the gate fails closed instead of guessing.

    ``from __future__`` statements are excluded like other imports. Dynamic import
    calls remain counted as ordinary expressions; source code is never executed.

    This function is REQ-CNST-010's measurement, not a registered
    ``PolicySurface`` -- ``scripts/check_policy_relaxation.py`` does not model
    measurement function bodies -- so an edit here changes every file's effective
    budget without tripping that gate. The REQ-CNST-010-EN-NN exemption ledger
    this measures against (``LineLimitExemption``/``_LINE_LIMIT_EXEMPTIONS``) is a
    registered policy surface and stays in
    ``tests/arch/_subpackage_isolation_line_limits.py``.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    import_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if node.end_lineno is None:
                raise SyntaxError(
                    f"import statement at line {node.lineno} has no end_lineno",
                    (str(path), node.lineno, node.col_offset, None),
                )
            import_lines.update(range(node.lineno, node.end_lineno + 1))
    return _physical_line_count(source) - len(import_lines)
