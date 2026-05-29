"""Arch guard: keyword regexes in cmd-scanning rules must use path-safe guards.

Enforces that all re.compile calls in cmd-scanning rule files either:
  - Contain (?<![.a-zA-Z0-9_/]) (manual path-safe lookbehind), OR
  - Contain (?![.a-zA-Z0-9_/]) (manual path-safe lookahead), OR
  - Are in the EXEMPT_PATTERNS frozenset (non-keyword patterns)

Patterns built via cmd_keyword_pattern() are implicitly excluded from extraction:
their first argument is an f-string (not an ast.Constant), so _extract_re_compile_patterns
skips them. They do not need to appear in GUARD_MARKERS.

This prevents regressions where a developer adds a bare \\b keyword regex
that would false-positive on paths containing keyword-like substrings
(e.g., .local matching 'local', /export/ matching 'export').
"""

from __future__ import annotations

import ast
import pathlib
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

# Files that scan cmd fields for keywords and must use path-safe patterns
CMD_SCANNING_RULE_FILES = [
    SRC_ROOT / "recipe" / "rules" / "rules_inline_script.py",
    SRC_ROOT / "recipe" / "_git_helpers.py",
]

# Guard markers that indicate path-safe construction.
# cmd_keyword_pattern() callers are implicitly excluded from extraction (f-string first arg).
GUARD_MARKERS = frozenset(
    {
        "(?<![.a-zA-Z0-9_/])",  # path-safe lookbehind
        "(?![.a-zA-Z0-9_/])",  # path-safe lookahead
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

    compile_calls: dict[int, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                    compile_calls[id(node.value)] = target.id
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and isinstance(node.value, ast.Call):
                compile_calls[id(node.value)] = node.target.id

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
            if any(marker in pattern for marker in GUARD_MARKERS):
                continue
            # If it uses bare \b without a guard, it's a violation
            if r"\b" in pattern:
                rel_path = filepath.relative_to(SRC_ROOT.parent.parent)
                violations.append(
                    f"{rel_path}:{lineno} {var_name} uses \\b without path-safe guard"
                )

    assert violations == [], "Regex patterns missing path-safe guards:\n" + "\n".join(violations)


HOOK_GUARD_RULE_FILES = [
    SRC_ROOT / "hooks" / "guards" / "write_guard.py",
]


def test_redirect_patterns_exclude_fd_redirects():
    """Redirect-matching regexes in hook guards must not match fd-number redirects
    without a downstream pseudo-device filter."""
    for filepath in HOOK_GUARD_RULE_FILES:
        source = filepath.read_text()
        for var_name, pattern, lineno in _extract_re_compile_patterns(filepath):
            if ">+" in pattern:
                assert "_PSEUDO_DEVICE_PATHS" in source, (
                    f"{filepath.relative_to(SRC_ROOT.parent.parent)}:{lineno} "
                    f"{var_name} matches >-redirects but the file has no "
                    "_PSEUDO_DEVICE_PATHS filter for defense-in-depth"
                )
                break


def test_write_guard_has_safe_path_filtering():
    """write_guard must filter pseudo-device paths from extracted targets."""
    source = (SRC_ROOT / "hooks" / "guards" / "write_guard.py").read_text()
    assert "/dev/null" in source, "write_guard.py must contain a safe-path set including /dev/null"
    assert "_PSEUDO_DEVICE_PATHS" in source, (
        "write_guard.py must define _PSEUDO_DEVICE_PATHS constant"
    )


REQUIRED_WRITE_GUARD_TEST_FAMILIES = {
    "python3",
    "python",
    "heredoc",
    "sed",
    "redirect",
    "tee",
    "mv",
    "cp",
    "rm",
    "patch",
    "git_checkout",
    "git_reset",
    "gh_api",
}


def test_write_guard_has_interpreter_detection() -> None:
    """write_guard must detect interpreter-mediated writes (python3, python).

    Must not rely solely on shell primitives.
    """
    source = (SRC_ROOT / "hooks" / "guards" / "write_guard.py").read_text()
    assert "python" in source.lower() or "_command_classification" in source, (
        "write_guard.py must contain interpreter detection logic for python/python3 "
        "or import it from _command_classification"
    )
    assert any(
        name in source
        for name in (
            "_IS_INTERPRETER_WRITE_RE",
            "_has_interpreter_write",
            "_IS_PYTHON_CMD_RE",
            "_PYTHON_WRITE_APIS_RE",
            "has_interpreter_write",
            "_command_classification",
        )
    ), "write_guard.py must define or import an interpreter write detection mechanism"


def test_write_guard_tests_cover_required_command_families() -> None:
    """Write guard test file must have test function names for all known writing families."""
    test_path = pathlib.Path(__file__).parent.parent / "hooks" / "test_write_guard.py"
    tree = ast.parse(test_path.read_text())
    test_names = {
        node.name.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }
    missing = []
    for family in REQUIRED_WRITE_GUARD_TEST_FAMILIES:
        if not any(family.lower() in name for name in test_names):
            missing.append(family)
    assert not missing, (
        f"test_write_guard.py missing test function names for command families: {missing}. "
        f"Every known file-writing command family must have deny-path test coverage."
    )


COMMAND_CLASSIFYING_GUARDS = [
    SRC_ROOT / "hooks" / "guards" / "write_guard.py",
    SRC_ROOT / "hooks" / "guards" / "pr_create_guard.py",
    SRC_ROOT / "hooks" / "guards" / "planner_gh_discovery_guard.py",
    SRC_ROOT / "hooks" / "guards" / "unsafe_install_guard.py",
    SRC_ROOT / "hooks" / "guards" / "artifact_download_guard.py",
]


def test_command_classifying_guards_use_shared_primitive():
    """Guards that classify Bash commands must import from _command_classification."""
    for filepath in COMMAND_CLASSIFYING_GUARDS:
        source = filepath.read_text()
        assert "_command_classification" in source or "# EXEMPT: " in source, (
            f"{filepath.name} classifies Bash commands but does not use "
            f"the shared _command_classification module"
        )


def test_shared_command_classification_module_exists():
    """The shared command classification module must exist for guards to import."""
    module_path = SRC_ROOT / "hooks" / "_command_classification.py"
    assert module_path.exists(), (
        "hooks/_command_classification.py must exist — "
        "it centralizes interpreter/wrapper detection for all command-classifying guards"
    )


@pytest.mark.parametrize(
    "guard_file,bypass_family",
    [
        ("write_guard.py", "interpreter_write"),
        ("pr_create_guard.py", "interpreter_subprocess"),
        ("planner_gh_discovery_guard.py", "interpreter_subprocess"),
        ("unsafe_install_guard.py", "interpreter_subprocess"),
    ],
)
def test_guard_handles_bypass_family(guard_file: str, bypass_family: str) -> None:
    """Each command-classifying guard must handle its relevant bypass families."""
    source = (SRC_ROOT / "hooks" / "guards" / guard_file).read_text()
    if bypass_family == "interpreter_write":
        assert "has_interpreter_write" in source or "_command_classification" in source
    elif bypass_family == "interpreter_subprocess":
        assert "has_interpreter_wrapped_command" in source or "_command_classification" in source


def test_write_guard_uses_tokenization() -> None:
    """write_guard.py must use the structural tokenization layer from _command_classification."""
    source = (SRC_ROOT / "hooks" / "guards" / "write_guard.py").read_text()
    assert "tokenize_command_segments" in source or "is_gh_command" in source, (
        "write_guard.py must import and use the structural tokenization layer from "
        "_command_classification (tokenize_command_segments or is_gh_command)"
    )


def test_command_classification_exports_tokenization() -> None:
    """_command_classification.py must export the tokenization primitives."""
    source = (SRC_ROOT / "hooks" / "_command_classification.py").read_text()
    for name in ("tokenize_command_segments", "command_verb", "is_gh_command"):
        assert f"def {name}" in source, (
            f"_command_classification.py must define {name}() for structural command parsing"
        )
