"""Machine-checked exceptions for fixtures that mask launch transitions."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from autoskillit.core import atomic_write

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_TESTS_ROOT = Path(__file__).resolve().parent.parent

GUARDED_TRANSITION_SYMBOLS = frozenset(
    {
        "ensure_pre_launch",
        "resolve_executable_launch_binding",
        "executable_binding_matches_current_file",
    }
)


@dataclass(frozen=True)
class MaskingExemption:
    fixture: str
    patched_symbols: frozenset[str]
    rationale: str
    real_path_tests: tuple[str, ...]


GUARD_MASKING_EXEMPTIONS = (
    MaskingExemption(
        fixture="cli/conftest.py::_stub_interactive_prelaunch",
        patched_symbols=frozenset({"ensure_pre_launch", "resolve_executable_launch_binding"}),
        rationale="Legacy CLI tests isolate final command behavior below the real probe boundary.",
        real_path_tests=(
            "cli/test_interactive_cold_launch_medium.py::test_supported_cold_launch_spawns_with_probed_attestation",
            "cli/test_cook_cold_launch_medium.py::test_cook_probes_without_provider_secret_then_spawns_with_attestation",
        ),
    ),
)


def _fixture_defs() -> list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    fixtures: list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(
                (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "fixture"
                )
                or (isinstance(dec, ast.Attribute) and dec.attr == "fixture")
                for dec in node.decorator_list
            ):
                rel = path.relative_to(_TESTS_ROOT).as_posix()
                fixtures.append((node.name, f"{rel}::{node.name}", node))
    return fixtures


def _module_usefixtures() -> dict[str, frozenset[str]]:
    uses: dict[str, frozenset[str]] = {}
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names: set[str] = set()
        for statement in tree.body:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            targets = (
                statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            )
            if not any(
                isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets
            ):
                continue
            value = statement.value
            for call in (node for node in ast.walk(value) if isinstance(node, ast.Call)):
                if isinstance(call.func, ast.Attribute) and call.func.attr == "usefixtures":
                    names.update(
                        arg.value
                        for arg in call.args
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    )
        if names:
            uses[path.relative_to(_TESTS_ROOT).as_posix()] = frozenset(names)
    return uses


def _is_autouse(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr == "fixture"
        and any(
            keyword.arg == "autouse"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in dec.keywords
        )
        for dec in node.decorator_list
    )


def _patched_symbols(node: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    patched: set[str] = set()
    for call in (child for child in ast.walk(node) if isinstance(child, ast.Call)):
        if not (isinstance(call.func, ast.Attribute) and call.func.attr in {"setattr", "setitem"}):
            continue
        candidates: set[str] = set()
        for arg in call.args[:2]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                candidates.add(arg.value.rsplit(".", 1)[-1])
            elif isinstance(arg, ast.Attribute):
                candidates.add(arg.attr)
        patched.update(candidates & GUARDED_TRANSITION_SYMBOLS)
    return frozenset(patched)


def _masking_hits() -> dict[str, frozenset[str]]:
    fixtures = _fixture_defs()
    fixtures_by_location = {
        (qualified.rsplit("::", 1)[0], name): qualified for name, qualified, _node in fixtures
    }
    module_wide_fixtures: set[str] = set()
    for module, names in _module_usefixtures().items():
        module_path = Path(module)
        visible_files = (
            module,
            *(
                str(parent / "conftest.py")
                for parent in (module_path.parent, *module_path.parent.parents)
            ),
        )
        for name in names:
            for visible_file in visible_files:
                if qualified := fixtures_by_location.get((visible_file, name)):
                    module_wide_fixtures.add(qualified)
                    break
    hits: dict[str, frozenset[str]] = {}
    for name, qualified, node in fixtures:
        if qualified not in module_wide_fixtures and not _is_autouse(node):
            continue
        symbols = _patched_symbols(node)
        if symbols:
            hits[qualified] = symbols
    return hits


def _is_unmasked_real_path_test(qualified: str) -> bool:
    rel, function_name = qualified.split("::", 1)
    path = _TESTS_ROOT / rel
    if not path.is_file():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ),
        None,
    )
    if function is None:
        return False
    masking_fixture_names = {row.fixture.rsplit("::", 1)[1] for row in GUARD_MASKING_EXEMPTIONS}
    fixture_args = {
        arg.arg
        for arg in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
    }
    if masking_fixture_names & fixture_args:
        return False
    module_marks = tuple(
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    )
    scopes: tuple[ast.AST, ...] = (*module_marks, *function.decorator_list)
    return not any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "usefixtures"
        and any(
            isinstance(arg, ast.Constant) and arg.value in masking_fixture_names
            for arg in call.args
        )
        for node in scopes
        for call in (child for child in ast.walk(node) if isinstance(child, ast.Call))
    )


def test_launch_transition_masking_exemptions_match_ast_hits() -> None:
    hits = _masking_hits()
    assert hits, "Launch-transition fixture scan matched no guarded symbols"
    registered = {row.fixture: row.patched_symbols for row in GUARD_MASKING_EXEMPTIONS}
    assert hits == registered


def test_module_usefixtures_does_not_match_same_named_sibling_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    atomic_write(
        tmp_path / "first" / "conftest.py",
        """import pytest

@pytest.fixture
def shared_name(monkeypatch):
    monkeypatch.setattr(backend, "ensure_pre_launch", lambda: None)
""",
    )
    atomic_write(
        tmp_path / "second" / "test_consumer.py",
        """import pytest

pytestmark = pytest.mark.usefixtures("shared_name")
""",
    )
    monkeypatch.setattr(sys.modules[__name__], "_TESTS_ROOT", tmp_path)

    assert _masking_hits() == {}


def test_masking_exemptions_have_rationale_and_real_path_coverage() -> None:
    invalid = [row.fixture for row in GUARD_MASKING_EXEMPTIONS if not row.rationale.strip()]
    stale_tests = [
        test
        for row in GUARD_MASKING_EXEMPTIONS
        for test in row.real_path_tests
        if not _is_unmasked_real_path_test(test)
    ]
    assert not invalid, f"Masking exemptions without rationale: {invalid}"
    assert not stale_tests, f"Missing designated real-path tests: {stale_tests}"
