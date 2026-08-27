"""Standing invariant: every bounding limits dataclass has per-field coverage.

A bounding-limits dataclass with N fields but only one ever driven to a
triggering value gives false confidence: the untested fields could be
off-by-one, inverted, or charging the wrong thing entirely and the suite would
still pass (the defect T-B1 in the exploration-capture-immunity rectify plan,
#4756, removes for ``SnapshotCaptureLimits``).

This guard has two halves:

1. Discovery (violations-by-default): every ``@dataclass`` in ``src/`` whose
   name ends ``Limits``/``Budget`` and whose fields are all numeric must be a
   key in ``_BOUNDED_LIMITS_REGISTRY`` or in ``_BOUNDED_LIMITS_ALLOWLIST`` with
   a rationale and tracking issue — the shape ``_DETACHED_SPAWN_ALLOWLIST``
   (``tests/arch/test_ast_rules.py``) already uses.
2. Coverage: a registered dataclass's candidate test module(s) must contain a
   ``pytest.mark.parametrize`` that either (a) is visibly sourced from
   ``dataclasses.fields(<ClassName>)`` — detected by AST, so a table update
   automatically extends coverage with no separate edit — or (b) references,
   as string literals, every field the class *currently* has (checked against
   a live ``dataclasses.fields()`` import, not a hand-copied name list in this
   guard itself, so this guard cannot itself drift out of sync with the
   dataclass).
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = SRC_ROOT.parent.parent
_TESTS_ROOT = _REPO_ROOT / "tests"
_NUMERIC_ANNOTATIONS = frozenset({"int", "float"})

# dataclass name -> (importable module path, candidate test file(s) relative to tests/)
_BOUNDED_LIMITS_REGISTRY: dict[str, tuple[str, tuple[str, ...]]] = {
    "SnapshotCaptureLimits": (
        "autoskillit.exploration.snapshot._records",
        ("exploration/test_snapshot.py",),
    ),
    "CollectorLimits": (
        "autoskillit.exploration.collectors._bounded",
        ("exploration/test_bounded_collectors.py",),
    ),
    "EvidenceReaderLimits": (
        "autoskillit.server.tools._evidence_reader._startup",
        ("server/test_tools_evidence_reader.py",),
    ),
    "SweepBudgetSpec": (
        "autoskillit.hooks._capture._types",
        ("hooks/test_capture_lifecycle.py",),
    ),
    "CaptureCapacitySpec": (
        "autoskillit.hooks._capture._types",
        ("hooks/test_capture_lifecycle.py",),
    ),
    "LockWaitSpec": (
        "autoskillit.hooks._capture._types",
        ("hooks/test_capture_lifecycle.py",),
    ),
}
_REQUIRED_CAPTURE_BOUNDED_SPEC_NAMES = frozenset(
    {"SweepBudgetSpec", "CaptureCapacitySpec", "LockWaitSpec"}
)

# dataclass name -> rationale — the shape _DETACHED_SPAWN_ALLOWLIST uses, keyed
# by name instead of path since a *Limits dataclass is what this guard tracks.
_BOUNDED_LIMITS_ALLOWLIST: dict[str, str] = {}


def _is_dataclass_decorator(decorator: ast.expr) -> bool:
    if isinstance(decorator, ast.Call):
        return _is_dataclass_decorator(decorator.func)
    if isinstance(decorator, ast.Name):
        return decorator.id == "dataclass"
    if isinstance(decorator, ast.Attribute):
        return decorator.attr == "dataclass"
    return False


def _dataclass_field_annotations(class_def: ast.ClassDef) -> dict[str, str] | None:
    """Return {field_name: annotation_name} for a simple dataclass body.

    Returns ``None`` if any field's annotation isn't a bare ``Name`` (e.g. a
    subscript like ``dict[str, int] | None``) — treated conservatively as "not
    provably all-numeric" rather than guessed at.
    """
    fields: dict[str, str] = {}
    for stmt in class_def.body:
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        if not isinstance(stmt.annotation, ast.Name):
            return None
        fields[stmt.target.id] = stmt.annotation.id
    return fields


def _discover_bounded_limits_dataclasses() -> dict[str, Path]:
    """Every numeric ``@dataclass`` named ``*Limits`` or ``*Budget`` in ``src/``."""
    found: dict[str, Path] = {}
    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not node.name.endswith(("Limits", "Budget")):
                continue
            if not any(_is_dataclass_decorator(dec) for dec in node.decorator_list):
                continue
            fields = _dataclass_field_annotations(node)
            if not fields:
                continue
            if all(annotation in _NUMERIC_ANNOTATIONS for annotation in fields.values()):
                found[node.name] = py_file
    return found


def test_every_bounded_limits_dataclass_is_registered() -> None:
    discovered = _discover_bounded_limits_dataclasses()
    missing = sorted(
        f"  {name} ({path.relative_to(_REPO_ROOT)})"
        for name, path in discovered.items()
        if name not in _BOUNDED_LIMITS_REGISTRY
    )
    assert not missing, (
        "Bounding limits dataclasses missing from _BOUNDED_LIMITS_REGISTRY in "
        "tests/arch/test_bounded_limits_coverage.py. Add a registry entry naming the "
        "test module(s) that exercise every field, or an allowlist entry with a "
        "rationale and tracking issue:\n" + "\n".join(missing)
    )


def test_required_capture_bounded_specs_are_registered() -> None:
    assert _REQUIRED_CAPTURE_BOUNDED_SPEC_NAMES <= _BOUNDED_LIMITS_REGISTRY.keys()


def _target_field_names(module_path: str, class_name: str) -> frozenset[str]:
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return frozenset(field.name for field in dataclasses.fields(cls))


def _parametrize_calls(tree: ast.Module) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "parametrize"
    ]


def _is_generative_fields_call(node: ast.AST, class_name: str) -> bool:
    """True if *node* contains a call to ``dataclasses.fields()``/``fields()`` naming
    *class_name* — proof the parametrize is sourced from the dataclass's own
    current field set rather than a hand-copied list that could drift."""
    for descendant in ast.walk(node):
        if not isinstance(descendant, ast.Call):
            continue
        func = descendant.func
        is_fields_call = (isinstance(func, ast.Name) and func.id == "fields") or (
            isinstance(func, ast.Attribute) and func.attr == "fields"
        )
        if is_fields_call and any(
            isinstance(n, ast.Name) and n.id == class_name for n in ast.walk(descendant)
        ):
            return True
    return False


def _literal_field_name_references(node: ast.AST, expected: frozenset[str]) -> frozenset[str]:
    return frozenset(
        constant.value
        for constant in ast.walk(node)
        if isinstance(constant, ast.Constant)
        and isinstance(constant.value, str)
        and constant.value in expected
    )


def test_parametrize_detection_requires_actual_reference() -> None:
    """Prove the two detectors are live, not vacuous."""
    generative = ast.parse(
        "@pytest.mark.parametrize('field', [f.name for f in dataclasses.fields(Foo)])\n"
        "def test_x(field): pass\n"
    ).body[0]
    literal = ast.parse(
        "@pytest.mark.parametrize('field', ['a', 'b'])\ndef test_x(field): pass\n"
    ).body[0]
    assert isinstance(generative, ast.FunctionDef)
    assert isinstance(literal, ast.FunctionDef)
    assert _is_generative_fields_call(generative.decorator_list[0], "Foo")
    assert not _is_generative_fields_call(literal.decorator_list[0], "Foo")
    assert _literal_field_name_references(literal.decorator_list[0], frozenset({"a", "c"})) == {
        "a"
    }


def test_every_registered_dataclass_has_per_field_coverage() -> None:
    missing: list[str] = []
    for class_name, (module_path, candidate_paths) in _BOUNDED_LIMITS_REGISTRY.items():
        if class_name in _BOUNDED_LIMITS_ALLOWLIST:
            continue
        expected = _target_field_names(module_path, class_name)
        covered: frozenset[str] = frozenset()
        generative = False
        for rel_path in candidate_paths:
            full_path = _TESTS_ROOT / rel_path
            if not full_path.exists():
                continue
            tree = ast.parse(full_path.read_text(encoding="utf-8"))
            for call in _parametrize_calls(tree):
                if _is_generative_fields_call(call, class_name):
                    generative = True
                covered |= _literal_field_name_references(call, expected)
        if not generative and covered < expected:
            missing.append(
                f"{class_name}: fields {sorted(expected - covered)} have no per-field "
                f"parametrized test in {', '.join(candidate_paths)}"
            )
    assert not missing, "Bounding limits dataclasses missing per-field coverage:\n" + "\n".join(
        f"  {m}" for m in missing
    )


def test_bounded_limits_allowlist_entries_are_still_registered() -> None:
    """An allowlist entry for a dataclass no longer discovered/registered is dead
    weight — remove it rather than leave a stale escape hatch."""
    stale = sorted(
        name for name in _BOUNDED_LIMITS_ALLOWLIST if name not in _BOUNDED_LIMITS_REGISTRY
    )
    assert not stale, f"Stale _BOUNDED_LIMITS_ALLOWLIST entries: {stale}"
