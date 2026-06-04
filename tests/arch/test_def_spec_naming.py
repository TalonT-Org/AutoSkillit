"""Architectural guard: *Def/*Spec naming convention — structural form must match suffix.

*Def classes must be NamedTuple or @dataclass(frozen=True).
*Spec classes must be @dataclass (any) or TypedDict.
"""

import ast
import pathlib

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC = pathlib.Path(__file__).parent.parent.parent / "src" / "autoskillit"

_EXEMPT_DEF: frozenset[str] = frozenset()

_EXEMPT_SPEC: frozenset[str] = frozenset()


def _is_namedtuple(node: ast.ClassDef) -> bool:
    return any(
        (isinstance(b, ast.Name) and b.id == "NamedTuple")
        or (isinstance(b, ast.Attribute) and b.attr == "NamedTuple")
        for b in node.bases
    )


def _is_dataclass(node: ast.ClassDef, *, require_frozen: bool = False) -> bool:
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "dataclass":
            return not require_frozen
        if (
            isinstance(dec, ast.Attribute)
            and isinstance(dec.value, ast.Name)
            and dec.value.id == "dataclasses"
            and dec.attr == "dataclass"
        ):
            return not require_frozen
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if isinstance(func, ast.Name) and func.id == "dataclass":
            pass
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "dataclasses"
            and func.attr == "dataclass"
        ):
            pass
        else:
            continue
        if not require_frozen:
            return True
        return any(
            isinstance(kw, ast.keyword)
            and kw.arg == "frozen"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in dec.keywords
        )
    return False


def _is_typeddict(node: ast.ClassDef) -> bool:
    return any(
        (isinstance(b, ast.Name) and b.id == "TypedDict")
        or (isinstance(b, ast.Attribute) and b.attr == "TypedDict")
        for b in node.bases
    )


def _invalid_def_classes(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text())
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.name.endswith("Def"):
            continue
        if node.name in _EXEMPT_DEF:
            continue
        if _is_namedtuple(node) or _is_dataclass(node, require_frozen=True):
            continue
        violations.append((node.lineno, node.name))
    return violations


def _invalid_spec_classes(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text())
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.name.endswith("Spec"):
            continue
        if node.name in _EXEMPT_SPEC:
            continue
        if _is_dataclass(node) or _is_typeddict(node):
            continue
        violations.append((node.lineno, node.name))
    return violations


def test_def_classes_are_immutable() -> None:
    """Every class ending in *Def must be a NamedTuple or @dataclass(frozen=True)."""
    assert _SRC.is_dir(), f"Source directory not found: {_SRC}"
    all_violations: list[str] = []
    for py_file in sorted(_SRC.rglob("*.py")):
        for lineno, cls_name in _invalid_def_classes(py_file):
            rel = py_file.relative_to(_SRC.parent.parent)
            all_violations.append(f"  {rel}:{lineno}  {cls_name}")
    assert not all_violations, (
        f"Found {len(all_violations)} *Def class(es) that are not NamedTuple "
        f"or @dataclass(frozen=True):\n" + "\n".join(all_violations)
    )


def test_spec_classes_are_dataclass_or_typeddict() -> None:
    """Every class ending in *Spec must be a @dataclass or TypedDict."""
    assert _SRC.is_dir(), f"Source directory not found: {_SRC}"
    all_violations: list[str] = []
    for py_file in sorted(_SRC.rglob("*.py")):
        for lineno, cls_name in _invalid_spec_classes(py_file):
            rel = py_file.relative_to(_SRC.parent.parent)
            all_violations.append(f"  {rel}:{lineno}  {cls_name}")
    assert not all_violations, (
        f"Found {len(all_violations)} *Spec class(es) that are not "
        f"@dataclass or TypedDict:\n" + "\n".join(all_violations)
    )


# --- Calibration tests ---


def test_def_guard_catches_plain_dataclass(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "bad.py"
    src.write_text("from dataclasses import dataclass\n\n@dataclass\nclass BadDef:\n    x: int\n")
    assert _invalid_def_classes(src) == [(4, "BadDef")]


def test_def_guard_catches_plain_class(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "bad.py"
    src.write_text("class PlainDef:\n    x: int\n")
    assert _invalid_def_classes(src) == [(1, "PlainDef")]


def test_def_guard_allows_frozen_dataclass(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "good.py"
    src.write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass(frozen=True)\n"
        "class GoodDef:\n"
        "    x: int\n"
    )
    assert _invalid_def_classes(src) == []


def test_def_guard_allows_frozen_dataclass_module_qualified(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "good.py"
    src.write_text(
        "import dataclasses\n\n"
        "@dataclasses.dataclass(frozen=True, slots=True)\n"
        "class GoodDef:\n"
        "    x: int\n"
    )
    assert _invalid_def_classes(src) == []


def test_def_guard_allows_namedtuple(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "good.py"
    src.write_text("from typing import NamedTuple\n\nclass GoodDef(NamedTuple):\n    x: int\n")
    assert _invalid_def_classes(src) == []


def test_spec_guard_catches_plain_class(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "bad.py"
    src.write_text("class BadSpec:\n    x: int\n")
    assert _invalid_spec_classes(src) == [(1, "BadSpec")]


def test_spec_guard_catches_namedtuple(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "bad.py"
    src.write_text("from typing import NamedTuple\n\nclass BadSpec(NamedTuple):\n    x: int\n")
    assert _invalid_spec_classes(src) == [(3, "BadSpec")]


def test_spec_guard_allows_dataclass(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "good.py"
    src.write_text(
        "from dataclasses import dataclass\n\n@dataclass\nclass GoodSpec:\n    x: int\n"
    )
    assert _invalid_spec_classes(src) == []


def test_spec_guard_allows_frozen_dataclass(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "good.py"
    src.write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class GoodSpec:\n"
        "    x: int\n"
    )
    assert _invalid_spec_classes(src) == []


def test_spec_guard_allows_typeddict(tmp_path: pathlib.Path) -> None:
    src = tmp_path / "good.py"
    src.write_text("from typing import TypedDict\n\nclass GoodSpec(TypedDict):\n    x: int\n")
    assert _invalid_spec_classes(src) == []
