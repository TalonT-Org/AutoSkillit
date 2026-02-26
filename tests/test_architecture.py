"""Architectural enforcement: AST-based rules over src/autoskillit/ source files.

Rules enforced here (compile-time, no execution required):
  1. No print() calls in production code
  2. No sensitive keyword arguments passed to logger calls

Note: `import logging` and `logging.getLogger()` are enforced by ruff TID251
at pre-commit time (see pyproject.toml [tool.ruff.lint.flake8-tidy-imports]).
Those rules belong in the toolchain, not duplicated here.

Exemptions:
  - cli.py: may use print() for user-facing terminal output
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

SRC_ROOT = Path(__file__).parent.parent / "src" / "autoskillit"

_SENSITIVE_KEYWORDS = frozenset({"token", "secret", "password", "key", "api_key", "auth"})
_LOGGER_METHODS = frozenset({"debug", "info", "warning", "error", "critical", "exception"})
_PRINT_EXEMPT = frozenset({"cli.py"})
_BROAD_EXCEPTION_TYPES: frozenset[str] = frozenset({"Exception", "BaseException"})


def _has_log_call(body: list[ast.stmt]) -> bool:
    """Return True if body contains any logger.<method>(…) call."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _LOGGER_METHODS
        ):
            return True
    return False


def _has_reraise(body: list[ast.stmt]) -> bool:
    """Return True if body contains any raise statement (re-raise pattern)."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            return True
    return False


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(Path(__file__).parent.parent))
    except ValueError:
        return str(path)


class Violation(NamedTuple):
    file: Path
    line: int
    col: int
    message: str

    def __str__(self) -> str:
        return f"{_rel(self.file)}:{self.line}:{self.col}: {self.message}"


class ArchitectureViolationVisitor(ast.NodeVisitor):
    def __init__(self, filepath: Path) -> None:
        self.filepath = filepath
        self.violations: list[Violation] = []
        self._print_exempt = filepath.name in _PRINT_EXEMPT

    def _add(self, node: ast.AST, message: str) -> None:
        self.violations.append(
            Violation(
                self.filepath,
                node.lineno,
                node.col_offset,
                message,  # type: ignore[attr-defined]
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        # Rule 1: no print() — ruff cannot enforce this in production-only files
        if not self._print_exempt and isinstance(node.func, ast.Name) and node.func.id == "print":
            self._add(node, "print() call — use logger instead")

        # Rule 2: no sensitive kwargs in logger calls — not expressible in ruff
        if isinstance(node.func, ast.Attribute) and node.func.attr in _LOGGER_METHODS:
            for kw in node.keywords:
                if kw.arg and any(s in kw.arg.lower() for s in _SENSITIVE_KEYWORDS):
                    self._add(node, f"sensitive kwarg '{kw.arg}' passed to logger")

        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Rule 3: broad except without logger call or re-raise → silent swallow."""
        is_broad = node.type is None or (
            isinstance(node.type, ast.Name) and node.type.id in _BROAD_EXCEPTION_TYPES
        )
        if is_broad and not _has_log_call(node.body) and not _has_reraise(node.body):
            type_label = ast.unparse(node.type) if node.type else "bare except"
            self._add(
                node,
                f"broad except ({type_label}) without any logger call"
                " — add logger.warning/error with exc_info=True",
            )
        self.generic_visit(node)


def _scan(path: Path) -> list[Violation]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [Violation(path, exc.lineno or 0, 0, f"SyntaxError: {exc.msg}")]
    visitor = ArchitectureViolationVisitor(filepath=path)
    visitor.visit(tree)
    return visitor.violations


_SOURCE_FILES = sorted(SRC_ROOT.rglob("*.py"))


def test_tmp_path_is_ram_backed(tmp_path: Path) -> None:
    """On Linux/WSL2, tmp_path must resolve to /dev/shm (RAM-backed tmpfs).

    On macOS no assertion is made — disk-backed /tmp is acceptable there.
    Fails intentionally on Linux when pytest is invoked directly without --basetemp.
    Always run tests via 'task test-all', not pytest directly.
    """
    if sys.platform == "linux":
        path_str = str(tmp_path)
        assert path_str.startswith("/dev/shm"), (
            f"tmp_path ({path_str!r}) is not in /dev/shm. "
            "Run tests via 'task test-all', which passes "
            "--basetemp=/dev/shm/pytest-tmp."
        )


class TestArchitectureEnforcement:
    """Parametrized AST checks over every .py file in src/autoskillit/."""

    @pytest.mark.parametrize(
        "source_file",
        _SOURCE_FILES,
        ids=[_rel(f) for f in _SOURCE_FILES],
    )
    def test_no_violations(self, source_file: Path) -> None:
        violations = _scan(source_file)
        if violations:
            report = "\n".join(f"  {v}" for v in violations)
            pytest.fail(
                f"Architectural violations in {_rel(source_file)}:\n{report}",
                pytrace=False,
            )


def test_sync_manifest_module_deleted():
    """REQ-SYNC-002: sync_manifest.py does not exist."""
    sync_path = Path(__file__).parent.parent / "src" / "autoskillit" / "sync_manifest.py"
    assert not sync_path.exists()


def test_no_sync_manifest_imports_in_production_code():
    """REQ-SYNC-001: No production module imports from autoskillit.sync_manifest."""
    src_dir = Path(__file__).parent.parent / "src"
    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text()
        assert "sync_manifest" not in content, f"Found sync_manifest reference in {py_file}"


def test_broad_except_exception_without_log_is_violation(tmp_path: Path) -> None:
    """Rule 3: except Exception: pass with no logger call must be flagged."""
    f = tmp_path / "bad.py"
    f.write_text("try:\n    pass\nexcept Exception:\n    pass\n")
    violations = _scan(f)
    assert violations, "Expected violation for broad except Exception without logger"
    messages = " ".join(v.message for v in violations)
    assert "except" in messages.lower()
    assert "logger" in messages.lower()


def test_broad_except_base_exception_without_log_is_violation(tmp_path: Path) -> None:
    """Rule 3: except BaseException: pass with no logger call must be flagged."""
    f = tmp_path / "bad.py"
    f.write_text("try:\n    pass\nexcept BaseException:\n    pass\n")
    violations = _scan(f)
    assert violations, "Expected violation for broad except BaseException without logger"


def test_bare_except_without_log_is_violation(tmp_path: Path) -> None:
    """Rule 3: bare except: pass with no logger call must be flagged."""
    f = tmp_path / "bad.py"
    f.write_text("try:\n    pass\nexcept:\n    pass\n")
    violations = _scan(f)
    assert violations, "Expected violation for bare except without logger"


def test_broad_except_with_log_call_is_not_violation(tmp_path: Path) -> None:
    """Rule 3: except Exception with a logger call is not a violation."""
    f = tmp_path / "ok.py"
    f.write_text(
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "try:\n"
        "    pass\n"
        "except Exception:\n"
        "    logger.warning('failed')\n"
    )
    violations = _scan(f)
    except_violations = [v for v in violations if "except" in v.message.lower()]
    assert not except_violations, f"Unexpected except violation: {except_violations}"


def test_specific_except_without_log_is_not_violation(tmp_path: Path) -> None:
    """Rule 3: except OSError (specific type) without logger is not a violation."""
    f = tmp_path / "ok.py"
    f.write_text("try:\n    pass\nexcept OSError:\n    pass\n")
    violations = _scan(f)
    except_violations = [v for v in violations if "except" in v.message.lower()]
    assert not except_violations, f"Unexpected except violation: {except_violations}"


def test_broad_except_with_reraise_is_not_violation(tmp_path: Path) -> None:
    """Rule 3: except Exception with unconditional re-raise is not a violation."""
    f = tmp_path / "ok.py"
    f.write_text("try:\n    pass\nexcept Exception:\n    raise\n")
    violations = _scan(f)
    except_violations = [v for v in violations if "except" in v.message.lower()]
    assert not except_violations, f"Unexpected except violation: {except_violations}"


def test_server_does_not_import_list_recipes_or_load_recipe_from_recipe_loader() -> None:
    """server.py must route all recipe discovery through recipe_parser, not recipe_loader.

    recipe_loader.list_recipes() is project-only. recipe_parser.list_recipes() covers
    both project and bundled sources. This AST check prevents future refactors from
    silently reintroducing the wrong-module caller pattern.
    """
    src = (Path(__file__).parent.parent / "src" / "autoskillit" / "server.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "recipe_loader" in node.module:
                names = [alias.name for alias in node.names]
                assert "list_recipes" not in names, (
                    "server.py imports list_recipes from recipe_loader. "
                    "Use recipe_parser.list_recipes — it covers both project and bundled sources."
                )
                assert "load_recipe" not in names, (
                    "server.py imports load_recipe from recipe_loader. "
                    "Use recipe_parser.list_recipes to find RecipeInfo.path, "
                    "then path.read_text()."
                )


def _get_module_ast(filename: str) -> ast.Module:
    return ast.parse((SRC_ROOT / filename).read_text())


def _top_level_class_names(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.col_offset == 0
    }


def _top_level_assign_targets(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def test_severity_not_defined_in_semantic_rules():
    """Severity must live in types.py, not semantic_rules.py."""
    tree = _get_module_ast("semantic_rules.py")
    assert "Severity" not in _top_level_class_names(tree), (
        "Severity is still defined in semantic_rules.py; move it to types.py"
    )


def test_severity_defined_in_types():
    """Severity must be a top-level class in types.py."""
    tree = _get_module_ast("types.py")
    assert "Severity" in _top_level_class_names(tree), (
        "Severity not found in types.py; it must be defined there"
    )


def test_skill_tools_not_defined_in_recipe_parser():
    """SKILL_TOOLS must not be locally defined in recipe_parser.py."""
    tree = _get_module_ast("recipe_parser.py")
    assert "_SKILL_TOOLS" not in _top_level_assign_targets(tree), (
        "_SKILL_TOOLS is still locally defined in recipe_parser.py; remove it"
    )


def test_skill_tools_not_defined_in_semantic_rules():
    """SKILL_TOOLS must not be locally defined in semantic_rules.py."""
    tree = _get_module_ast("semantic_rules.py")
    assert "_SKILL_TOOLS" not in _top_level_assign_targets(tree), (
        "_SKILL_TOOLS is still locally defined in semantic_rules.py; remove it"
    )


def test_skill_tools_not_defined_in_contract_validator():
    """SKILL_TOOLS must not be locally defined in contract_validator.py."""
    tree = _get_module_ast("contract_validator.py")
    assert "_SKILL_TOOLS" not in _top_level_assign_targets(tree), (
        "_SKILL_TOOLS is still locally defined in contract_validator.py; remove it"
    )


def test_skill_tools_defined_in_types():
    """SKILL_TOOLS must be a top-level assignment in types.py."""
    tree = _get_module_ast("types.py")
    assert "SKILL_TOOLS" in _top_level_assign_targets(tree), (
        "SKILL_TOOLS not found in types.py; it must be defined there"
    )


# ARCH-REG1 — contract_validator must not define its own context/input ref patterns
def test_contract_validator_imports_regex_from_recipe_parser():
    tree = _get_module_ast("contract_validator.py")
    assigns = [
        node.targets[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in ("_CONTEXT_REF_RE", "_INPUT_REF_RE")
    ]
    assert assigns == [], f"contract_validator defines its own regex patterns: {assigns}"
