"""Session-registry rows have controlled, statically visible write paths.

The registry is shared process state.  New callers must use one of its
row-creation wrappers, while the two existing persistence implementations are
the only code allowed to write its JSON artifact directly.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.medium]

_REGISTRY_FILENAME = "session_registry.json"
_DIRECT_PERSISTENCE_PATHS = frozenset(
    {
        "core/runtime/session_registry.py",
        "hooks/guards/open_kitchen_guard.py",
    }
)
_ROW_WRITE_WRAPPERS = frozenset({"write_registry_entry", "claim_launch_for_session"})
_REGISTRY_PATH_FACTORIES = frozenset({"registry_path"})

_Destination = Literal["registry", "dynamic", "other"]

_EXPECTED_REGISTRY_WRITES = (
    (
        "cli/session/_session_cook.py",
        "cook",
        "claim_launch_for_session",
    ),
    (
        "cli/session/_session_cook.py",
        "cook._run_managed",
        "write_registry_entry",
    ),
    (
        "cli/session/_session_order.py",
        "order",
        "claim_launch_for_session",
    ),
    (
        "cli/session/_session_launch.py",
        "_write_order_entry",
        "write_registry_entry",
    ),
    (
        "core/runtime/session_registry.py",
        "bind_session_owner",
        "_atomic_write",
    ),
    (
        "core/runtime/session_registry.py",
        "bridge_claude_session_id",
        "_atomic_write",
    ),
    (
        "core/runtime/session_registry.py",
        "claim_launch_for_session",
        "_atomic_write",
    ),
    (
        "core/runtime/session_registry.py",
        "release_session_claim",
        "_atomic_write",
    ),
    (
        "core/runtime/session_registry.py",
        "write_registry_entry",
        "_atomic_write",
    ),
    (
        "hooks/guards/open_kitchen_guard.py",
        "_bridge_session_registry",
        "os.replace",
    ),
)


@dataclass(frozen=True)
class _RegistryWriteSite:
    path: str
    function: str
    lineno: int
    kind: str
    direct: bool
    destination: _Destination = "registry"
    mode_is_dynamic: bool = False

    @property
    def violation(self) -> str | None:
        if self.destination == "dynamic":
            return "registry destination is not statically resolvable"
        if self.mode_is_dynamic:
            return "registry open mode is not statically resolvable"
        if self.direct and self.path not in _DIRECT_PERSISTENCE_PATHS:
            allowed = ", ".join(sorted(_DIRECT_PERSISTENCE_PATHS))
            return f"direct session-registry persistence is only allowed in {allowed}"
        return None


def _direct_assignments(body: Iterable[ast.stmt]) -> dict[str, ast.expr | None]:
    """Map local assignments, treating reassignment as unresolved."""
    assignments: dict[str, ast.expr | None] = {}

    class AssignmentCollector(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = None if target.id in assignments else node.value
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if isinstance(node.target, ast.Name):
                assignments[node.target.id] = None if node.target.id in assignments else node.value
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

    collector = AssignmentCollector()
    for statement in body:
        collector.visit(statement)
    return assignments


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name):
            return f"{node.func.value.id}.{node.func.attr}"
        return node.func.attr
    return None


def _row_write_wrapper_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name) and node.func.id in _ROW_WRITE_WRAPPERS:
        return node.func.id
    if isinstance(node.func, ast.Attribute) and node.func.attr in _ROW_WRITE_WRAPPERS:
        return node.func.attr
    return None


def _argument(node: ast.Call, position: int, keyword_names: frozenset[str]) -> ast.expr | None:
    if len(node.args) > position:
        return node.args[position]
    return next(
        (keyword.value for keyword in node.keywords if keyword.arg in keyword_names),
        None,
    )


def _static_string(
    expression: ast.expr | None,
    assignments: dict[str, ast.expr | None],
    seen_names: frozenset[str] = frozenset(),
) -> str | None:
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return expression.value
    if isinstance(expression, ast.Name) and expression.id not in seen_names:
        assigned = assignments.get(expression.id)
        if assigned is not None:
            return _static_string(assigned, assignments, seen_names | {expression.id})
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
        left = _static_string(expression.left, assignments, seen_names)
        right = _static_string(expression.right, assignments, seen_names)
        if left is not None and right is not None:
            return left + right
    if isinstance(expression, ast.JoinedStr) and all(
        isinstance(value, ast.Constant) and isinstance(value.value, str)
        for value in expression.values
    ):
        return "".join(
            value.value for value in expression.values if isinstance(value, ast.Constant)
        )
    return None


def _registry_literal(
    expression: ast.expr | None, assignments: dict[str, ast.expr | None]
) -> bool:
    value = _static_string(expression, assignments)
    return value is not None and value.endswith(_REGISTRY_FILENAME)


def _combine_destinations(*destinations: _Destination) -> _Destination:
    if "dynamic" in destinations:
        return "dynamic"
    if "registry" in destinations:
        return "registry"
    return "other"


def _conditional_destination(body: _Destination, alternate: _Destination) -> _Destination:
    if body == alternate:
        return body
    return "dynamic" if "registry" in {body, alternate} else _combine_destinations(body, alternate)


def _registry_path_factory(
    expression: ast.expr,
    assignments: dict[str, ast.expr | None],
    seen_names: frozenset[str] = frozenset(),
) -> _Destination:
    """Resolve a direct or locally aliased ``registry_path`` callable."""
    if isinstance(expression, ast.Name):
        if expression.id in _REGISTRY_PATH_FACTORIES:
            return "registry"
        if expression.id in seen_names:
            return "dynamic"
        assigned = assignments.get(expression.id)
        if assigned is not None:
            return _registry_path_factory(assigned, assignments, seen_names | {expression.id})
        return "other"
    if isinstance(expression, ast.Attribute) and expression.attr == "registry_path":
        return "registry"
    if isinstance(expression, ast.IfExp):
        body = _registry_path_factory(expression.body, assignments, seen_names)
        alternate = _registry_path_factory(expression.orelse, assignments, seen_names)
        return _conditional_destination(body, alternate)
    return "other"


def _destination(
    expression: ast.expr | None,
    assignments: dict[str, ast.expr | None],
    seen_names: frozenset[str] = frozenset(),
) -> _Destination:
    if expression is None:
        return "other"
    if _registry_literal(expression, assignments):
        return "registry"
    if isinstance(expression, ast.Name):
        if expression.id in seen_names:
            return "other"
        assigned = assignments.get(expression.id)
        if assigned is not None:
            return _destination(assigned, assignments, seen_names | {expression.id})
        return "other"
    if isinstance(expression, ast.IfExp):
        body = _destination(expression.body, assignments, seen_names)
        alternate = _destination(expression.orelse, assignments, seen_names)
        return _conditional_destination(body, alternate)
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Div):
        if _registry_literal(expression.right, assignments):
            return "registry"
        left = _destination(expression.left, assignments, seen_names)
        right = _destination(expression.right, assignments, seen_names)
        if "registry" in {left, right}:
            return "dynamic"
        return _combine_destinations(left, right)
    if isinstance(expression, ast.Call):
        name = _call_name(expression)
        factory_destination = _registry_path_factory(expression.func, assignments, seen_names)
        if factory_destination != "other":
            return factory_destination
        if name in {"Path", "pathlib.Path"}:
            return _destination(_argument(expression, 0, frozenset()), assignments, seen_names)
        if isinstance(expression.func, ast.Attribute) and expression.func.attr in {
            "absolute",
            "resolve",
        }:
            return _destination(expression.func.value, assignments, seen_names)
        inputs = [
            _destination(argument, assignments, seen_names)
            for argument in (*expression.args, *(keyword.value for keyword in expression.keywords))
        ]
        return "dynamic" if _combine_destinations(*inputs) != "other" else "other"
    if isinstance(expression, (ast.Attribute, ast.Subscript)):
        base = expression.value
        base_destination = _destination(base, assignments, seen_names)
        return "dynamic" if base_destination != "other" else "other"
    return "other"


def _open_site(
    node: ast.Call, assignments: dict[str, ast.expr | None]
) -> tuple[str, _Destination, bool] | None:
    if isinstance(node.func, ast.Name) and node.func.id == "open":
        destination = _destination(_argument(node, 0, frozenset({"file"})), assignments)
        mode_expression = _argument(node, 1, frozenset({"mode"}))
    elif isinstance(node.func, ast.Attribute) and node.func.attr == "open":
        is_static_path_open = (
            isinstance(node.func.value, ast.Name) and node.func.value.id == "Path"
        ) or (isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "Path")
        if is_static_path_open:
            destination = _destination(_argument(node, 0, frozenset()), assignments)
            mode_expression = _argument(node, 1, frozenset({"mode"}))
        else:
            destination = _destination(node.func.value, assignments)
            mode_expression = _argument(node, 0, frozenset({"mode"}))
    else:
        return None

    if destination == "other":
        return None
    if mode_expression is None:
        return None
    mode = _static_string(mode_expression, assignments)
    if mode is None:
        return "write-mode open", destination, True
    if any(flag in mode for flag in "wax+"):
        return "write-mode open", destination, False
    return None


def _direct_persistence_site(
    node: ast.Call, assignments: dict[str, ast.expr | None]
) -> tuple[str, _Destination, bool] | None:
    if isinstance(node.func, ast.Attribute) and node.func.attr.startswith("write_"):
        destination = _destination(node.func.value, assignments)
        return None if destination == "other" else (f"Path.{node.func.attr}", destination, False)

    open_site = _open_site(node, assignments)
    if open_site is not None:
        return open_site

    if (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and node.func.attr in {"replace", "rename"}
    ):
        destination = _destination(_argument(node, 1, frozenset({"dst"})), assignments)
        return None if destination == "other" else (f"os.{node.func.attr}", destination, False)

    name = _call_name(node)
    if name is not None and "atomic" in name.lower() and "write" in name.lower():
        destination = _destination(
            _argument(node, 0, frozenset({"destination", "path", "target"})), assignments
        )
        return None if destination == "other" else (name, destination, False)
    return None


class _RegistryWriteCollector(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self._path = path
        self._functions: list[str] = []
        self._assignments: list[dict[str, ast.expr | None]] = [{}]
        self.sites: list[_RegistryWriteSite] = []

    def visit_Module(self, node: ast.Module) -> None:
        self._assignments[-1] = _direct_assignments(node.body)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._functions.append(node.name)
        assignments = self._assignments[-1] | _direct_assignments(node.body)
        self._assignments.append(assignments)
        self.generic_visit(node)
        self._assignments.pop()
        self._functions.pop()

    def visit_Call(self, node: ast.Call) -> None:
        function = ".".join(self._functions) or "<module>"
        wrapper_name = _row_write_wrapper_name(node)
        if wrapper_name is not None:
            self.sites.append(
                _RegistryWriteSite(
                    path=self._path,
                    function=function,
                    lineno=node.lineno,
                    kind=wrapper_name,
                    direct=False,
                )
            )
        else:
            site = _direct_persistence_site(node, self._assignments[-1])
            if site is not None:
                kind, destination, mode_is_dynamic = site
                self.sites.append(
                    _RegistryWriteSite(
                        path=self._path,
                        function=function,
                        lineno=node.lineno,
                        kind=kind,
                        direct=True,
                        destination=destination,
                        mode_is_dynamic=mode_is_dynamic,
                    )
                )
        self.generic_visit(node)


def _scan_tree(tree: ast.AST, path: str) -> tuple[_RegistryWriteSite, ...]:
    collector = _RegistryWriteCollector(path)
    collector.visit(tree)
    return tuple(collector.sites)


def _scan_source() -> tuple[_RegistryWriteSite, ...]:
    sites: list[_RegistryWriteSite] = []
    for source_path in sorted(SRC_ROOT.rglob("*.py")):
        relative_path = source_path.relative_to(SRC_ROOT).as_posix()
        sites.extend(_scan_tree(ast.parse(source_path.read_text(encoding="utf-8")), relative_path))
    return tuple(sites)


def _inventory_key(site: _RegistryWriteSite) -> tuple[str, str, str]:
    return site.path, site.function, site.kind


def test_session_registry_row_writes_use_controlled_paths() -> None:
    violations = [site for site in _scan_source() if site.violation is not None]
    details = "\n".join(
        f"  {site.path}:{site.lineno} ({site.function}): {site.violation}" for site in violations
    )
    assert not violations, f"Session-registry writes must use controlled paths:\n{details}"


def test_session_registry_write_inventory_is_complete() -> None:
    """New row writes must consciously join the registry-write inventory."""
    observed = sorted(_inventory_key(site) for site in _scan_source())
    assert observed == sorted(_EXPECTED_REGISTRY_WRITES)


@pytest.mark.parametrize("wrapper", sorted(_ROW_WRITE_WRAPPERS))
def test_qualified_row_write_wrapper_is_inventoried(wrapper: str) -> None:
    sites = _scan_tree(ast.parse(f"def write():\n    registry.{wrapper}()\n"), "synthetic.py")
    assert [(site.kind, site.direct) for site in sites] == [(wrapper, False)]


@pytest.mark.parametrize(
    ("source", "kind"),
    (
        (
            "def write(project_dir):\n"
            "    path = registry_path(project_dir)\n"
            "    path.write_text('{}')\n",
            "Path.write_text",
        ),
        (
            "def write(project_dir):\n    path = registry_path(project_dir)\n    path.open('w')\n",
            "write-mode open",
        ),
        (
            "def write(project_dir, tmp):\n"
            "    path = registry_path(project_dir)\n"
            "    os.rename(tmp, path)\n",
            "os.rename",
        ),
        (
            "def write(project_dir):\n"
            "    path = registry_path(project_dir)\n"
            "    _atomic_write(path, '{}')\n",
            "_atomic_write",
        ),
    ),
)
def test_direct_registry_persistence_outside_authorized_modules_is_caught(
    source: str, kind: str
) -> None:
    site = _scan_tree(ast.parse(source), "synthetic.py")[0]
    assert site.kind == kind
    assert site.violation is not None
    assert "only allowed" in site.violation


def test_resolvable_registry_alias_is_not_flagged_as_dynamic() -> None:
    sites = _scan_tree(
        ast.parse(
            "def write(project_dir):\n"
            "    path = registry_path(project_dir)\n"
            "    destination = path\n"
            "    destination.write_text('{}')\n"
        ),
        "core/runtime/session_registry.py",
    )
    assert [site.violation for site in sites] == [None]


def test_registry_destination_assigned_in_try_block_is_inventoried() -> None:
    sites = _scan_tree(
        ast.parse(
            "def write(project_dir):\n"
            "    try:\n"
            "        path = registry_path(project_dir)\n"
            "        _atomic_write(path, '{}')\n"
            "    except OSError:\n"
            "        return\n"
        ),
        "core/runtime/session_registry.py",
    )
    assert [(site.kind, site.violation) for site in sites] == [("_atomic_write", None)]


def test_dynamic_registry_destination_fails_closed() -> None:
    sites = _scan_tree(
        ast.parse(
            "def write(project_dir, choose):\n"
            "    path = registry_path(project_dir)\n"
            "    destination = choose(path)\n"
            "    destination.write_text('{}')\n"
        ),
        "core/runtime/session_registry.py",
    )
    assert [site.violation for site in sites] == [
        "registry destination is not statically resolvable"
    ]


def test_dynamic_registry_factory_alias_fails_closed() -> None:
    sites = _scan_tree(
        ast.parse(
            "def write(project_dir, use_registry, other_factory):\n"
            "    factory = registry_path if use_registry else other_factory\n"
            "    destination = factory(project_dir)\n"
            "    destination.write_text('{}')\n"
        ),
        "core/runtime/session_registry.py",
    )
    assert [site.violation for site in sites] == [
        "registry destination is not statically resolvable"
    ]


def test_dynamic_registry_open_mode_fails_closed() -> None:
    sites = _scan_tree(
        ast.parse(
            "def write(project_dir, mode):\n"
            "    path = registry_path(project_dir)\n"
            "    path.open(mode)\n"
        ),
        "core/runtime/session_registry.py",
    )
    assert [site.violation for site in sites] == [
        "registry open mode is not statically resolvable"
    ]
