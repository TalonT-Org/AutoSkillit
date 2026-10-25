"""Architectural invariant: every source-named .autoskillit child is classified."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_STR_RX = re.compile(r"(?:^|[/\"'`\s(~])\.autoskillit/([A-Za-z0-9._-]+)")

# These names are accounted for elsewhere: home-level artifacts, a read-only
# legacy path, and a runtime directory with a real-git proof.
_CLASSIFIED_ELSEWHERE: dict[str, int] = {
    # Home-level hook state: hooks/guards/mcp_health_advisor.py:36.
    "active_kitchens.json": 1,
    # Home-level marketplace shape: core/types/constants/_type_constants_retirements.py:309.
    "marketplace": 1,
    # Home-level plugin storage: cli/doctor/_doctor_install.py:358.
    "plugin-generations": 1,
    # Home-level hook projection repair: workspace/_projected_artifact/_hook_repair.py:538.
    "plugin-projections": 1,
    # Home-level dismissal state: cli/update/_update_checks.py:79.
    "update_check.json": 1,
    # Legacy path is read only for migration: cli/install/_marketplace.py:162.
    "scripts": 1,
    # Runtime self-ignore is proven by tests/workspace/test_clone_gate_runtime_state.py.
    "managed-headless-session-lineage": 1,
}


def _str_const(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _assignment_targets(node: ast.AST) -> Iterator[ast.expr]:
    if isinstance(node, ast.Assign):
        yield from node.targets
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        yield node.target


def _is_root(node: ast.AST, roots: frozenset[str]) -> bool:
    if _str_const(node) == ".autoskillit":
        return True
    if isinstance(node, ast.Name):
        return node.id in roots
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _str_const(node.right) == ".autoskillit"
    if isinstance(node, ast.Call) and node.args:
        return _str_const(node.args[-1]) == ".autoskillit"
    return False


def _children_named_in(tree: ast.Module) -> set[str]:
    """Collect children from literal paths and simple syntactic path expressions.

    Recognize direct root assignments, slash joins, and adjacent literal
    arguments or sequence elements. Root names are matched across the module
    without scope analysis; dynamic strings and indirect aliases are not traced.
    """
    roots = {
        target.id
        for node in ast.walk(tree)
        for target in _assignment_targets(node)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and node.value is not None
        and isinstance(target, ast.Name)
        and _is_root(node.value, frozenset())
    }
    names: set[str] = set()
    for node in ast.walk(tree):
        text = _str_const(node)
        if text is not None:
            names.update(match.group(1) for match in _STR_RX.finditer(text))
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and _is_root(node.left, roots)
        ):
            right = _str_const(node.right)
            if right:
                names.add(right.split("/")[0])
        seq = (
            node.elts
            if isinstance(node, (ast.Tuple, ast.List))
            else node.args
            if isinstance(node, ast.Call)
            else ()
        )
        for first, second in zip(seq, seq[1:]):
            if _str_const(first) == ".autoskillit" and _str_const(second):
                names.add(_str_const(second).split("/")[0])
    return {name.rstrip(".") for name in names} - {""}


def _src_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit"


def test_every_autoskillit_child_named_in_src_is_classified() -> None:
    from autoskillit.core.io import _AUTOSKILLIT_GITIGNORE_ENTRIES, _COMMITTED_BY_DESIGN

    src = _src_root()
    assert src.is_dir(), f"src/autoskillit not found at {src}"

    named = {
        child
        for path in src.rglob("*.py")
        for child in _children_named_in(ast.parse(path.read_text(encoding="utf-8")))
    }
    ignored_names = {entry.rstrip("/") for entry in _AUTOSKILLIT_GITIGNORE_ENTRIES}
    classified_names = {
        name for name, classification in _CLASSIFIED_ELSEWHERE.items() if classification == 1
    }
    unclassified = sorted(
        named - set(_COMMITTED_BY_DESIGN) - ignored_names - {".gitignore"} - classified_names
    )

    assert not unclassified, (
        f"Unclassified .autoskillit children named in src/autoskillit: {unclassified}. "
        f"Commit a persistent child by adding it to _COMMITTED_BY_DESIGN; list a file "
        f"in _AUTOSKILLIT_GITIGNORE_ENTRIES only when explicit setup writes it through "
        f"ensure_project_gitignore; runtime ensure_project_temp does not maintain the "
        f"tracked parent .gitignore. For a runtime "
        f"directory, call ensure_self_ignored when creating it, prove the behavior with "
        f"a real-git test, and name that test in an adjacent justification comment."
    )


def test_zero_classification_flag_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(_CLASSIFIED_ELSEWHERE, "managed-headless-session-lineage", 0)

    with pytest.raises(AssertionError, match="managed-headless-session-lineage"):
        test_every_autoskillit_child_named_in_src_is_classified()


def test_classification_map_matches_policy_checker_contract() -> None:
    from tests.arch.test_acceptance_policy_relaxation_gate import check

    surface = check.PolicySurface(
        "tests/arch/test_autoskillit_root_children_registered.py",
        "_CLASSIFIED_ELSEWHERE",
        "int_map",
        default=0,
    )
    set_source = "_CLASSIFIED_ELSEWHERE: set[str] = {'managed-headless-session-lineage'}"
    with pytest.raises(check.UnsupportedSurfaceShape):
        check.extract_surface_values(set_source, surface)

    actual_source = Path(__file__).read_text(encoding="utf-8")
    actual = check.extract_surface_values(actual_source, surface)
    assert {name: value.limit for name, value in actual.items()} == _CLASSIFIED_ELSEWHERE
