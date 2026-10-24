"""Architectural guard: strict xfail bridges must be specific and cite an issue.

Every ``pytest.mark.xfail(strict=True, reason=...)`` decorator — both on function
definitions and inside ``pytest.param(..., marks=...)`` — must include a ``#NNNN``
issue reference in its ``reason`` string and a specific ``raises=`` exception type.
Bare ``Exception``, ``BaseException``, and ``AssertionError`` do not identify the
expected failure precisely. Class-valued ``raises=`` matching includes subclasses;
the replay helper ``_assert_admitted`` checks the exact rejection code and detail.
Files in ``_XFAIL_POLICY_EXEMPT_FILES`` are excluded when they have an alternative
self-governance mechanism (e.g. a companion meta-test that forces the count
monotonically non-increasing).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_TESTS_ROOT = Path(__file__).resolve().parent.parent

_XFAIL_POLICY_EXEMPT_FILES: frozenset[str] = frozenset(
    {
        "arch/test_recipe_diagram_freshness.py",  # permanent: shrink meta-test
        "arch/test_capability_consistency.py",  # permanent: bounded known-violations set
        "skills/test_skill_body_cleanliness.py",  # permanent: bounded known-violators set
    }
)

_EXEMPT_CAP = 3


def _is_xfail_strict_true(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    is_xfail = False
    if isinstance(func, ast.Name) and func.id == "xfail":
        is_xfail = True
    elif isinstance(func, ast.Attribute) and func.attr == "xfail":
        is_xfail = True
    if not is_xfail:
        return False
    for kw in node.keywords:
        if kw.arg == "strict" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _extract_reason(node: ast.Call) -> str | None:
    for kw in node.keywords:
        if kw.arg == "reason":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
            if isinstance(kw.value, ast.JoinedStr):
                parts = []
                for v in kw.value.values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        parts.append(v.value)
                return "".join(parts) if parts else None
            return None
    return None


def _decorator_xfail_nodes(decorator: ast.expr) -> list[ast.Call]:
    if not isinstance(decorator, ast.Call):
        return []
    if _is_xfail_strict_true(decorator):
        return [decorator]
    return [
        node
        for node in ast.walk(decorator)
        if isinstance(node, ast.Call) and node is not decorator and _is_xfail_strict_true(node)
    ]


def _param_mark_xfail_nodes(call: ast.Call) -> list[ast.Call]:
    func = call.func
    is_param = (isinstance(func, ast.Attribute) and func.attr == "param") or (
        isinstance(func, ast.Name) and func.id == "param"
    )
    if not is_param:
        return []
    results = []
    for keyword in call.keywords:
        if keyword.arg == "marks":
            for node in ast.walk(keyword.value):
                if isinstance(node, ast.Call) and _is_xfail_strict_true(node):
                    results.append(node)
    return results


def _collect_xfail_strict_true_nodes(tree: ast.Module) -> list[ast.Call]:
    results: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                results.extend(_decorator_xfail_nodes(decorator))
        if isinstance(node, ast.Call):
            results.extend(_param_mark_xfail_nodes(node))
    return list({id(node): node for node in results}.values())


def _strict_xfail_raises_issue(node: ast.Call) -> str | None:
    raises = next((kw.value for kw in node.keywords if kw.arg == "raises"), None)
    if raises is None:
        return "requires a specific raises= exception type"
    for exception in ast.walk(raises):
        name = None
        if isinstance(exception, ast.Name):
            name = exception.id
        elif (
            isinstance(exception, ast.Attribute)
            and isinstance(exception.value, ast.Name)
            and exception.value.id == "builtins"
        ):
            name = exception.attr
        if name in {"Exception", "BaseException", "AssertionError"}:
            return f"raises={name} is too broad"
    return None


def test_strict_xfail_reasons_cite_tracking_issue() -> None:
    """Strict xfail bridges must name an expected type and cite a tracking issue."""
    violations: list[str] = []
    for py_file in sorted(_TESTS_ROOT.rglob("test_*.py")):
        rel = py_file.relative_to(_TESTS_ROOT).as_posix()
        if rel in _XFAIL_POLICY_EXEMPT_FILES:
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for xfail_node in _collect_xfail_strict_true_nodes(tree):
            raises_issue = _strict_xfail_raises_issue(xfail_node)
            if raises_issue is not None:
                violations.append(f"{rel}:{xfail_node.lineno} — {raises_issue}")
            reason = _extract_reason(xfail_node)
            if reason is None:
                violations.append(
                    f"{rel}:{xfail_node.lineno} — reason must be a string literal "
                    "so policy can be checked"
                )
            elif not re.search(r"#\d+", reason):
                violations.append(
                    f"{rel}:{xfail_node.lineno} — reason={reason!r} does not cite "
                    "a tracking issue; add #NNNN or resolve the defect and remove the xfail"
                )
    assert not violations, "strict xfail bridge policy violations:\n" + "\n".join(
        f"  {v}" for v in violations
    )


@pytest.mark.parametrize(
    ("raises_clause", "expected_issue"),
    [
        pytest.param("", True, id="missing"),
        pytest.param(", raises=Exception", True, id="exception"),
        pytest.param(", raises=BaseException", True, id="base-exception"),
        pytest.param(", raises=AssertionError", True, id="assertion-error"),
        pytest.param(", raises=(ValueError, Exception)", True, id="broad-tuple"),
        pytest.param(", raises=builtins.Exception", True, id="qualified-broad-type"),
        pytest.param(", raises=(ValueError, LookupError)", False, id="specific-tuple"),
        pytest.param(", raises=builtins.LookupError", False, id="qualified-specific-type"),
        pytest.param(", raises=LookupError", False, id="specific-type"),
    ],
)
def test_strict_xfail_raises_policy_on_synthetic_source(
    raises_clause: str, expected_issue: bool
) -> None:
    """Synthetic param marks enforce raises precision and are collected once."""
    tree = ast.parse(
        "import pytest\n"
        "@pytest.mark.parametrize('value', [\n"
        "    pytest.param(1, marks=pytest.mark.xfail(\n"
        "        strict=True, reason='tracked #1234'"
        f"{raises_clause})),\n"
        "])\n"
        "def test_example(value):\n"
        "    pass\n"
    )
    nodes = _collect_xfail_strict_true_nodes(tree)
    assert len(nodes) == 1
    assert (_strict_xfail_raises_issue(nodes[0]) is not None) is expected_issue


def _collect_imperative_xfail_nodes(tree: ast.Module) -> list[ast.Call]:
    """Find imperative ``pytest.xfail(...)``/``xfail(...)`` calls used as bare statements.

    Distinct AST shape from ``_collect_xfail_strict_true_nodes``: that collector visits
    decorator and ``pytest.param(marks=...)`` sites, which carry a ``strict=`` keyword.
    The imperative form raises immediately at call time, has no ``strict=`` keyword, and
    was previously invisible to this policy — exactly the blind spot that hid an
    untracked context-limit-compliance exemption (issue #4305).
    """
    results: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        is_xfail = (isinstance(func, ast.Name) and func.id == "xfail") or (
            isinstance(func, ast.Attribute) and func.attr == "xfail"
        )
        if is_xfail:
            results.append(call)
    return results


def _extract_imperative_reason(call: ast.Call) -> str | None:
    """Extract the reason string from an imperative xfail call (positional or keyword)."""
    arg = call.args[0] if call.args else None
    if arg is None:
        for kw in call.keywords:
            if kw.arg == "reason":
                arg = kw.value
                break
    if arg is None:
        return None
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr):
        parts = [
            v.value for v in arg.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        ]
        return "".join(parts) if parts else None
    return None


def test_imperative_xfail_reasons_cite_tracking_issue() -> None:
    """Every imperative pytest.xfail(...) call must contain a #NNNN issue reference."""
    violations: list[str] = []
    for py_file in sorted(_TESTS_ROOT.rglob("test_*.py")):
        rel = py_file.relative_to(_TESTS_ROOT).as_posix()
        if rel in _XFAIL_POLICY_EXEMPT_FILES:
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for call in _collect_imperative_xfail_nodes(tree):
            reason = _extract_imperative_reason(call)
            if reason is None:
                violations.append(
                    f"{rel}:{call.lineno} — reason must be a string literal so policy "
                    "can be checked"
                )
            elif not re.search(r"#\d+", reason):
                violations.append(
                    f"{rel}:{call.lineno} — reason={reason!r} does not cite "
                    "a tracking issue; add #NNNN or resolve the defect and remove the xfail"
                )
    assert not violations, "pytest.xfail(...) without tracking issue reference:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_imperative_xfail_collector_flags_missing_citation() -> None:
    """Synthetic AST: an imperative pytest.xfail() with no #NNNN citation is detected."""
    tree = ast.parse(
        "import pytest\n\ndef test_something():\n    pytest.xfail('no citation here')\n"
    )
    nodes = _collect_imperative_xfail_nodes(tree)
    assert len(nodes) == 1
    reason = _extract_imperative_reason(nodes[0])
    assert reason == "no citation here"
    assert not re.search(r"#\d+", reason)


def test_imperative_xfail_collector_passes_with_citation() -> None:
    """Synthetic AST: an imperative pytest.xfail() citing #NNNN passes the policy."""
    tree = ast.parse(
        "import pytest\n\ndef test_something():\n    pytest.xfail('known gap, tracking: #1234')\n"
    )
    nodes = _collect_imperative_xfail_nodes(tree)
    assert len(nodes) == 1
    reason = _extract_imperative_reason(nodes[0])
    assert reason is not None
    assert re.search(r"#\d+", reason)


def test_imperative_xfail_collector_ignores_decorator_and_param_forms() -> None:
    """The imperative collector must not double-count decorator/pytest.param(marks=...) xfails."""
    tree = ast.parse(
        "import pytest\n\n"
        "@pytest.mark.xfail(strict=True, reason='tracked #99')\n"
        "def test_decorated():\n"
        "    pass\n"
    )
    assert _collect_imperative_xfail_nodes(tree) == []


def test_exemption_entries_have_rationale_comments() -> None:
    """Every _XFAIL_POLICY_EXEMPT_FILES entry must have a rationale comment."""
    src = Path(__file__).read_text()
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_XFAIL_POLICY_EXEMPT_FILES":
                if isinstance(node.value, ast.Call) and node.value.args:
                    set_node = node.value.args[0]
                    if isinstance(set_node, ast.Set):
                        for elt in set_node.elts:
                            if isinstance(elt, ast.Constant):
                                line = lines[elt.lineno - 1]
                                assert re.search(r"#\s*(permanent|tracking):", line), (
                                    f"Exempt entry {elt.value!r} on line {elt.lineno} "
                                    "must have a '# permanent:' or '# tracking:' comment"
                                )


def test_exemption_registry_size_cap() -> None:
    """Exemption registry must not exceed _EXEMPT_CAP."""
    assert len(_XFAIL_POLICY_EXEMPT_FILES) <= _EXEMPT_CAP, (
        f"_XFAIL_POLICY_EXEMPT_FILES has {len(_XFAIL_POLICY_EXEMPT_FILES)} entries "
        f"but cap is {_EXEMPT_CAP}. Review whether all entries are still needed."
    )


def test_exempt_files_exist() -> None:
    """Every exemption registry entry must resolve to an existing file."""
    missing = [
        entry
        for entry in sorted(_XFAIL_POLICY_EXEMPT_FILES)
        if not (_TESTS_ROOT / entry).is_file()
    ]
    assert not missing, f"Exempt entries point to non-existent files: {missing}"
