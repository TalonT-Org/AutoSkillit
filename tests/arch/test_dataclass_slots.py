# tests/arch/test_dataclass_slots.py
import ast
import pathlib

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC = pathlib.Path(__file__).parent.parent.parent / "src" / "autoskillit"


def _frozen_without_slots(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return (lineno, class_name) for frozen=True dataclasses missing slots=True."""
    tree = ast.parse(path.read_text())
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != "dataclass":
                continue
            has_frozen = any(
                isinstance(kw, ast.keyword)
                and kw.arg == "frozen"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in dec.keywords
            )
            has_slots = any(
                isinstance(kw, ast.keyword)
                and kw.arg == "slots"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in dec.keywords
            )
            if has_frozen and not has_slots:
                violations.append((node.lineno, node.name))
    return violations


def test_all_frozen_dataclasses_have_slots() -> None:
    """Every @dataclass(frozen=True) in src/autoskillit/ must also have slots=True."""
    all_violations: list[str] = []
    for py_file in sorted(_SRC.rglob("*.py")):
        for lineno, cls_name in _frozen_without_slots(py_file):
            rel = py_file.relative_to(_SRC.parent.parent)
            all_violations.append(f"  {rel}:{lineno}  {cls_name}")

    assert not all_violations, (
        f"Found {len(all_violations)} frozen dataclass(es) missing slots=True:\n"
        + "\n".join(all_violations)
    )
