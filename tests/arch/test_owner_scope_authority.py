"""Owner-scope authority guard: exact reference inventory, settle-before-admit

wrapping of the food-truck dispatch call, and process-scan/environ purity of
the owner-scope module itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_OWNER_SCOPE_PY = SRC_ROOT / "execution" / "process" / "_lifecycle" / "owner_scope.py"
_FOOD_TRUCK_EXECUTOR_PY = (
    SRC_ROOT / "execution" / "headless" / "_managed" / "_food_truck_executor.py"
)

# ---------------------------------------------------------------------------
# 1.6a -- exact reference inventory
# ---------------------------------------------------------------------------

_MATCH_NAMES: frozenset[str] = frozenset({"OWNER_SCOPE_ENV_VAR", "OWNER_SCOPE_DIR_ENV_VAR"})
_MATCH_LITERALS: frozenset[str] = _MATCH_NAMES | frozenset(
    {"AUTOSKILLIT_OWNER_SCOPE", "AUTOSKILLIT_OWNER_SCOPE_DIR"}
)

# The exact set of (file relative to src/autoskillit, enclosing function
# qualified name or "<module>") sites permitted to reference either symbol or
# either literal. Only .py files are scanned -- core/__init__.pyi re-exports
# the same two names, but a stub carries no runtime behavior of its own; the
# behavior lives entirely at the sites below, and the stub-completeness guard
# (tests/arch/test_pyi_stub_completeness.py) already keeps the stub in sync with
# the constants module's __all__.
_ALLOWED_OWNER_SCOPE_REFERENCES: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "core/types/constants/_type_constants_env.py",
            "<module>",
        ),  # def, __all__, private-set membership
        ("execution/process/_lifecycle/owner_scope.py", "<module>"),  # import
        ("execution/process/_lifecycle/owner_scope.py", "OwnerScope.child_env"),  # writer
        ("execution/process/_lifecycle/owned_group.py", "<module>"),  # import
        ("execution/process/_lifecycle/owned_group.py", "_resolve_owner_scope"),  # reader
        ("execution/backends/_backend_cmd_builder_base.py", "<module>"),  # import
        (
            "execution/backends/_backend_cmd_builder_base.py",
            "_add_workflow_context_env",
        ),  # propagator
    }
)


def _qualified_scope(stack: list[str]) -> str:
    return ".".join(stack) if stack else "<module>"


class _OwnerScopeReferenceCollector(ast.NodeVisitor):
    """Records every (file, enclosing scope) site touching either symbol/literal."""

    def __init__(self, relative_path: str) -> None:
        self._relative_path = relative_path
        self._stack: list[str] = []
        self.found: set[tuple[str, str]] = set()

    def _record(self) -> None:
        self.found.add((self._relative_path, _qualified_scope(self._stack)))

    def _enter_scope(self, name: str, node: ast.AST) -> None:
        self._stack.append(name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter_scope(node.name, node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter_scope(node.name, node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter_scope(node.name, node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _MATCH_NAMES:
            self._record()
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _MATCH_NAMES:
            self._record()
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and node.value in _MATCH_LITERALS:
            self._record()

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in _MATCH_NAMES or alias.asname in _MATCH_NAMES:
                self._record()
        self.generic_visit(node)


def _collect_owner_scope_references(src_root: Path) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(src_root.rglob("*.py")):
        relative = path.relative_to(src_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        collector = _OwnerScopeReferenceCollector(relative)
        collector.visit(tree)
        found |= collector.found
    return found


def test_owner_scope_references_match_exact_allowlist() -> None:
    """A reference added or removed anywhere in src/ must update this allowlist deliberately."""
    found = _collect_owner_scope_references(SRC_ROOT)
    missing = _ALLOWED_OWNER_SCOPE_REFERENCES - found
    unexpected = found - _ALLOWED_OWNER_SCOPE_REFERENCES
    assert not missing, (
        f"allowlisted owner-scope reference site no longer found: {sorted(missing)}"
    )
    assert not unexpected, (
        f"new owner-scope reference site not in the allowlist: {sorted(unexpected)}"
    )


_CANARY_NEW_REFERENCE_SOURCE = """
from autoskillit.core import OWNER_SCOPE_ENV_VAR


def _unregistered_reader() -> str | None:
    import os

    return os.environ.get(OWNER_SCOPE_ENV_VAR)
"""


def test_canary_collector_catches_new_owner_scope_reference() -> None:
    """The collector attributes a fresh reference to its enclosing function, not just the file."""
    tree = ast.parse(_CANARY_NEW_REFERENCE_SOURCE)
    collector = _OwnerScopeReferenceCollector("synthetic/module.py")
    collector.visit(tree)
    assert ("synthetic/module.py", "<module>") in collector.found
    assert ("synthetic/module.py", "_unregistered_reader") in collector.found
    assert not (_ALLOWED_OWNER_SCOPE_REFERENCES & collector.found)


# ---------------------------------------------------------------------------
# 1.6b -- dispatch_food_truck's single _execute_claude_headless call is
# settled-before-admitted inside its own owner_scope
# ---------------------------------------------------------------------------


def _owner_scope_binding(node: ast.AsyncWith) -> str | None:
    for item in node.items:
        expr = item.context_expr
        callee_names: set[str] = set()
        if isinstance(expr, ast.Call):
            if isinstance(expr.func, ast.Name):
                callee_names.add(expr.func.id)
            elif isinstance(expr.func, ast.Attribute):
                callee_names.add(expr.func.attr)
        if "owner_scope" in callee_names and isinstance(item.optional_vars, ast.Name):
            return item.optional_vars.id
    return None


class _OwnerScopeWrapVisitor(ast.NodeVisitor):
    """Tracks owner_scope async-with nesting to bind each execute call to its scope."""

    def __init__(self) -> None:
        self._stack: list[tuple[str, ast.AsyncWith]] = []
        self.execute_calls: list[tuple[ast.Call, tuple[str, ast.AsyncWith] | None]] = []

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        binding = _owner_scope_binding(node)
        if binding is None:
            self.generic_visit(node)
            return
        self._stack.append((binding, node))
        for stmt in node.body:
            self.visit(stmt)
        self._stack.pop()

    def visit_Await(self, node: ast.Await) -> None:
        call = node.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "_execute_claude_headless"
        ):
            self.execute_calls.append((call, self._stack[-1] if self._stack else None))
        self.generic_visit(node)


def _find_class_method(
    tree: ast.Module, class_name: str, method_name: str
) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if (
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == method_name
                ):
                    return child
    raise AssertionError(f"{class_name}.{method_name} not found")


def _verify_dispatch_food_truck_owner_scope_wrapping(tree: ast.Module) -> None:
    """Fail closed on any structural deviation -- never skip on an unresolvable shape."""
    func = _find_class_method(tree, "DefaultHeadlessExecutor", "dispatch_food_truck")
    visitor = _OwnerScopeWrapVisitor()
    for stmt in func.body:
        visitor.visit(stmt)

    assert len(visitor.execute_calls) == 1, (
        "expected exactly one _execute_claude_headless call in dispatch_food_truck, "
        f"found {len(visitor.execute_calls)}"
    )
    call, binding = visitor.execute_calls[0]
    assert binding is not None, (
        "_execute_claude_headless call is not lexically nested in an "
        "`async with owner_scope(...) as <name>:` block"
    )
    scope_name, async_with_node = binding

    admission_kw = next((kw for kw in call.keywords if kw.arg == "pre_spawn_admission"), None)
    assert admission_kw is not None, "pre_spawn_admission keyword not found on the call"
    assert isinstance(admission_kw.value, ast.Name), (
        "pre_spawn_admission must reference a plain function name"
    )
    admission_fn_name = admission_kw.value.id

    admission_def = next(
        (
            stmt
            for stmt in async_with_node.body
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
            and stmt.name == admission_fn_name
        ),
        None,
    )
    assert admission_def is not None, (
        f"{admission_fn_name} is not defined directly in the owner_scope async-with body"
    )
    assert admission_def.body, f"{admission_fn_name} has an empty body"
    first_stmt = admission_def.body[0]
    assert isinstance(first_stmt, ast.Expr) and isinstance(first_stmt.value, ast.Await), (
        f"{admission_fn_name}'s first statement must be an `await` expression"
    )
    settle_call = first_stmt.value.value
    assert (
        isinstance(settle_call, ast.Call)
        and isinstance(settle_call.func, ast.Attribute)
        and settle_call.func.attr == "settle_descendants"
        and isinstance(settle_call.func.value, ast.Name)
        and settle_call.func.value.id == scope_name
    ), (
        f"{admission_fn_name}'s first statement must be "
        f"`await {scope_name}.settle_descendants(...)`"
    )
    seal_kw = next((kw for kw in settle_call.keywords if kw.arg == "seal"), None)
    assert (
        seal_kw is not None
        and isinstance(seal_kw.value, ast.Constant)
        and seal_kw.value.value is False
    ), (
        f"{admission_fn_name}'s first statement must be "
        f"`await {scope_name}.settle_descendants(seal=False)`"
    )


def test_dispatch_food_truck_settles_owner_scope_before_admission() -> None:
    tree = ast.parse(_FOOD_TRUCK_EXECUTOR_PY.read_text(encoding="utf-8"))
    _verify_dispatch_food_truck_owner_scope_wrapping(tree)


_CANARY_MISSING_WRAPPER_SOURCE = """
class DefaultHeadlessExecutor:
    async def dispatch_food_truck(self):
        skill_result = await headless_facade._execute_claude_headless(
            build_spec,
            pre_spawn_admission=_settle_then_admit,
        )
        return skill_result
"""


def test_canary_missing_owner_scope_wrapper_fails_closed() -> None:
    tree = ast.parse(_CANARY_MISSING_WRAPPER_SOURCE)
    with pytest.raises(AssertionError):
        _verify_dispatch_food_truck_owner_scope_wrapping(tree)


_CANARY_WRONG_FIRST_STATEMENT_SOURCE = """
class DefaultHeadlessExecutor:
    async def dispatch_food_truck(self):
        async with owner_scope(token) as scope:

            async def _settle_then_admit(contract):
                return await _admit_finalized_launch(contract)

            skill_result = await headless_facade._execute_claude_headless(
                build_spec,
                pre_spawn_admission=_settle_then_admit,
            )
        return skill_result
"""


def test_canary_admission_function_missing_settle_first_fails_closed() -> None:
    tree = ast.parse(_CANARY_WRONG_FIRST_STATEMENT_SOURCE)
    with pytest.raises(AssertionError):
        _verify_dispatch_food_truck_owner_scope_wrapping(tree)


# ---------------------------------------------------------------------------
# 1.6c -- owner_scope.py never scans processes or touches os.environ directly
# ---------------------------------------------------------------------------

_FORBIDDEN_OWNER_SCOPE_MODULE_ATTRS: frozenset[str] = frozenset(
    {"process_iter", "cmdline", "environ", "environb"}
)


def test_owner_scope_module_never_scans_processes_or_touches_environ() -> None:
    """Selection is registration-based (tethers), never an environment or process-table scan."""
    tree = ast.parse(_OWNER_SCOPE_PY.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_OWNER_SCOPE_MODULE_ATTRS:
            hits.append(f"Attribute.{node.attr}@{node.lineno}")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_OWNER_SCOPE_MODULE_ATTRS:
            hits.append(f"Name.{node.id}@{node.lineno}")
    forbidden = sorted(_FORBIDDEN_OWNER_SCOPE_MODULE_ATTRS)
    assert not hits, f"owner_scope.py must never reference {forbidden}: {hits}"
