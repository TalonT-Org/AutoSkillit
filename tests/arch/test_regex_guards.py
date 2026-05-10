"""Arch guard: keyword regexes in cmd-scanning rules must use path-safe guards.

Enforces that all re.compile calls in cmd-scanning rule files either:
  - Use cmd_keyword_pattern() (builder function), OR
  - Contain (?<![.a-zA-Z_/]) (manual path-safe lookbehind), OR
  - Are in the EXEMPT_PATTERNS frozenset (non-keyword patterns)

This prevents regressions where a developer adds a bare \\b keyword regex
that would false-positive on paths containing keyword-like substrings
(e.g., .local matching 'local', /export/ matching 'export').
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

# Files that scan cmd fields for keywords and must use path-safe patterns
CMD_SCANNING_RULE_FILES = [
    SRC_ROOT / "recipe" / "rules" / "rules_inline_script.py",
    SRC_ROOT / "recipe" / "_git_helpers.py",
]

# Guard markers that indicate path-safe construction
GUARD_MARKERS = frozenset(
    {
        "(?<![.a-zA-Z_/])",  # path-safe lookbehind (established pattern)
        "cmd_keyword_pattern",  # builder function use
    }
)

# Patterns that are exempt from the guard requirement (they don't match keywords)
EXEMPT_PATTERNS = frozenset(
    {
        "_JQ_BLOCK_RE",  # strips jq blocks, not keyword matching
        "_VAR_ASSIGN_RE",  # line-start anchored, not keyword matching
        "_AND_CHAIN_RE",  # literal &&, not keyword matching
        "_LITERAL_ORIGIN_RE",  # has its own context-safe lookbehind
    }
)


def _extract_re_compile_patterns(filepath: Path) -> list[tuple[str, str, int]]:
    """Return (variable_name, pattern_string, line_number) for each re.compile call."""
    source = filepath.read_text()
    tree = ast.parse(source)
    results = []

    # Map from re.compile Call nodes to their assigned variable names
    compile_calls: dict[int, str] = {}

    # First pass: find all Assign / AnnAssign nodes with a Call value and extract var names
    for node in ast.walk(tree):
        # Simple assignment: _FOO_RE = re.compile(...)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                    compile_calls[id(node.value)] = target.id
        # Annotated assignment: _FOO_RE: re.Pattern[str] = re.compile(...)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and isinstance(node.value, ast.Call):
                compile_calls[id(node.value)] = node.target.id

    # Second pass: find all re.compile / regex.compile calls
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_re_compile = (
            isinstance(func, ast.Attribute)
            and func.attr == "compile"
            and isinstance(func.value, ast.Name)
            and func.value.id in ("re", "regex")
        )
        if not is_re_compile:
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
            continue

        var_name = compile_calls.get(id(node), "<unknown>")
        results.append((var_name, first_arg.value, node.lineno))

    return results


def test_cmd_keyword_regexes_use_path_safe_guards():
    """All keyword-matching regexes in cmd-scanning rules must use path-safe guards."""
    violations = []
    for filepath in CMD_SCANNING_RULE_FILES:
        for var_name, pattern, lineno in _extract_re_compile_patterns(filepath):
            if var_name in EXEMPT_PATTERNS:
                continue
            # If it uses a guard marker, it's safe
            if any(marker in pattern for marker in GUARD_MARKERS):
                continue
            # If it uses bare \b without a guard, it's a violation
            if r"\b" in pattern:
                rel_path = filepath.relative_to(SRC_ROOT.parent.parent)
                violations.append(
                    f"{rel_path}:{lineno} {var_name} uses \\b without path-safe guard"
                )

    assert violations == [], "Regex patterns missing path-safe guards:\n" + "\n".join(violations)
