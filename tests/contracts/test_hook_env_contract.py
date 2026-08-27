"""Contract tests for the hook-process environment authority surface.

The hook registry is the only declaration of which environment variables hook
scripts consume.  These tests keep that declaration in lock-step with direct
``os.environ``/``os.getenv`` reads and require every AutoSkillit-owned value to
have concrete producer and delivery evidence.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOK_ENV_CONTRACT, HookEnvVarDef
from autoskillit.hook_registry._env_contract import _validate_hook_env_contract
from tests._ambient_env_surface import production_env_write_surface
from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.small]

_HOOKS_ROOT = SRC_ROOT / "hooks"


@dataclass(frozen=True, slots=True)
class _DirectHookEnvRead:
    var: str
    file: str
    line: int


def _is_os_environ(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _is_os_getenv(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "getenv"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _literal_key_arg(node: ast.Call) -> ast.expr | None:
    for keyword in node.keywords:
        if keyword.arg == "key":
            return keyword.value
    return node.args[0] if node.args else None


def _module_string_constants(
    source_root: Path,
) -> tuple[dict[Path, dict[str, str]], dict[str, str]]:
    per_module: dict[Path, dict[str, str]] = {}
    flat: dict[str, str] = {}
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants: dict[str, str] = {}
        for statement in tree.body:
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target, value = statement.targets[0], statement.value
            elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
                target, value = statement.target, statement.value
            if (
                isinstance(target, ast.Name)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                constants[target.id] = value.value
        per_module[path] = constants
        flat.update(constants)
    return per_module, flat


def _literal_direct_hook_env_reads(
    hooks_root: Path,
) -> tuple[tuple[_DirectHookEnvRead, ...], tuple[str, ...], tuple[str, ...]]:
    """Return direct literal reads, dynamic-key violations, and os-root aliases.

    The registry has no way to discover ``environment = os.environ`` or
    ``getenv = os.getenv`` aliases safely.  Rejecting those aliases keeps the
    source scan exhaustive without turning it into a general data-flow engine.
    Likewise, a variable key, concatenation, f-string, or computed constant
    must fail visibly rather than falling outside the contract surface.
    """
    reads: list[_DirectHookEnvRead] = []
    dynamic_keys: list[str] = []
    aliases: list[str] = []
    module_constants, flat_constants = _module_string_constants(hooks_root.parent)
    for path in sorted(hooks_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(hooks_root).as_posix()
        constants = module_constants[path]
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "os":
                for imported in node.names:
                    if imported.name in {"environ", "getenv"}:
                        aliases.append(f"{rel}:{node.lineno}:{imported.name}")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if value is None or not (_is_os_environ(value) or _is_os_getenv(value)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                for target in targets:
                    if isinstance(target, ast.Name):
                        aliases.append(f"{rel}:{node.lineno}:{target.id}")
            elif isinstance(node, ast.Call):
                key: ast.expr | None = None
                if _is_os_getenv(node.func):
                    key = _literal_key_arg(node)
                elif (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and _is_os_environ(node.func.value)
                ):
                    key = _literal_key_arg(node)
                if key is not None:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        reads.append(_DirectHookEnvRead(key.value, rel, node.lineno))
                    elif isinstance(key, ast.Name) and key.id in (constants | flat_constants):
                        value = constants.get(key.id) or flat_constants[key.id]
                        reads.append(_DirectHookEnvRead(value, rel, node.lineno))
                    else:
                        dynamic_keys.append(f"{rel}:{node.lineno}:{ast.unparse(key)}")
            elif isinstance(node, ast.Subscript) and _is_os_environ(node.value):
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    reads.append(_DirectHookEnvRead(node.slice.value, rel, node.lineno))
                elif isinstance(node.slice, ast.Name) and node.slice.id in (
                    constants | flat_constants
                ):
                    value = constants.get(node.slice.id) or flat_constants[node.slice.id]
                    reads.append(_DirectHookEnvRead(value, rel, node.lineno))
                else:
                    dynamic_keys.append(f"{rel}:{node.lineno}:{ast.unparse(node.slice)}")
    return tuple(reads), tuple(dynamic_keys), tuple(aliases)


def _producer_source_file(producer: str) -> str:
    module, separator, _qualname = producer.partition(":")
    assert separator and module.startswith("autoskillit."), (
        f"HookEnvVarDef.producer must be an autoskillit module:qualname, got {producer!r}"
    )
    return f"{module.removeprefix('autoskillit.').replace('.', '/')}.py"


def _resolve_function(reference: str) -> object:
    module_name, separator, qualname = reference.partition(":")
    assert separator and qualname, f"Expected module:qualname reference, got {reference!r}"
    value: object = importlib.import_module(module_name)
    for component in qualname.split("."):
        value = getattr(value, component)
    assert inspect.isfunction(value) or inspect.ismethod(value), (
        f"Hook env contract reference is not a function: {reference}"
    )
    return value


def _function_node(reference: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    module_name, _separator, qualname = reference.partition(":")
    path = SRC_ROOT / f"{module_name.removeprefix('autoskillit.').replace('.', '/')}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    body: list[ast.stmt] = tree.body
    found: ast.AST | None = None
    for component in qualname.split("."):
        found = next(
            (
                node
                for node in body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == component
            ),
            None,
        )
        assert found is not None, f"Cannot resolve AST function {reference}"
        body = found.body  # type: ignore[union-attr]
    assert isinstance(found, (ast.FunctionDef, ast.AsyncFunctionDef))
    return found


def _walk_without_nested_callables(node: ast.AST) -> Iterator[ast.AST]:
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield from _walk_without_nested_callables(child)


def _reachable_nodes(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ast.AST, ...]:
    reachable: list[ast.AST] = []

    def visit_body(body: list[ast.stmt]) -> None:
        for statement in body:
            if isinstance(statement, ast.If) and isinstance(statement.test, ast.Constant):
                reachable.append(statement)
                visit_body(statement.body if bool(statement.test.value) else statement.orelse)
            else:
                reachable.extend(_walk_without_nested_callables(statement))
            if isinstance(statement, (ast.Raise, ast.Return)):
                break

    visit_body(function.body)
    return tuple(reachable)


def _literal_call_reaches(
    entrypoint: ast.FunctionDef | ast.AsyncFunctionDef,
    producer_name: str,
) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == producer_name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == producer_name)
        )
        for node in _reachable_nodes(entrypoint)
    )


def test_hook_env_contract_is_bidirectional_with_direct_literal_hook_reads() -> None:
    reads, _dynamic_keys, _aliases = _literal_direct_hook_env_reads(_HOOKS_ROOT)

    declared = {entry.var for entry in HOOK_ENV_CONTRACT}
    consumed = {read.var for read in reads}
    missing = consumed - declared
    stale = declared - consumed
    assert not missing, (
        f"Literal hook environment reads absent from HOOK_ENV_CONTRACT: {sorted(missing)}"
    )
    assert not stale, f"HOOK_ENV_CONTRACT entries without a literal hook read: {sorted(stale)}"


def test_hook_direct_environment_reads_use_literal_keys_without_aliases() -> None:
    _reads, dynamic_keys, aliases = _literal_direct_hook_env_reads(_HOOKS_ROOT)
    assert not dynamic_keys, (
        "Direct hook environment reads must use a literal or bare literal-string constant, "
        "not a dynamic/computed key: " + ", ".join(sorted(dynamic_keys))
    )
    assert not aliases, (
        "Direct hook environment reads must use os.environ/os.getenv rather than aliases: "
        + ", ".join(sorted(aliases))
    )


def test_direct_hook_literal_discipline_catches_computed_keys_and_aliases(tmp_path: Path) -> None:
    hooks_root = tmp_path / "hooks"
    hooks_root.mkdir()
    (hooks_root / "synthetic_hook.py").write_text(
        "import os\n\n"
        'LITERAL_KEY = "SYNTHETIC_LITERAL_KEY"\n\n'
        "def read(suffix: str) -> None:\n"
        "    runtime_key = 'SYNTHETIC_' + suffix\n"
        "    os.environ.get(LITERAL_KEY)\n"
        "    os.environ.get(runtime_key)\n"
        "    os.environ.get(f'SYNTHETIC_{suffix}')\n"
        "    environment = os.environ\n"
        "    environment.get('SYNTHETIC_ALIASED_KEY')\n"
    )
    reads, dynamic_keys, aliases = _literal_direct_hook_env_reads(hooks_root)
    assert {read.var for read in reads} == {"SYNTHETIC_LITERAL_KEY"}
    assert any(key.endswith(":runtime_key") for key in dynamic_keys)
    assert any("f'SYNTHETIC_{suffix}'" in key for key in dynamic_keys)
    assert aliases == ("synthetic_hook.py:10:environment",)


def test_hook_env_contract_entries_have_one_shape_and_substantive_rationale() -> None:
    vars_seen = [entry.var for entry in HOOK_ENV_CONTRACT]
    assert len(vars_seen) == len(set(vars_seen)), f"Duplicate HOOK_ENV_CONTRACT vars: {vars_seen}"
    for entry in HOOK_ENV_CONTRACT:
        assert isinstance(entry, HookEnvVarDef)
        assert entry.var and entry.var == entry.var.upper()
        assert entry.provenance in {"autoskillit", "harness", "operator"}
        assert len(entry.justification) >= 40, entry
        if entry.provenance == "autoskillit":
            assert entry.producer and entry.entrypoint, entry
            _producer_source_file(entry.producer)


@pytest.mark.parametrize("provenance", ("harness", "operator"))
@pytest.mark.parametrize(
    ("producer", "entrypoint"),
    (("autoskillit.example:producer", None), (None, "autoskillit.example:entrypoint")),
)
def test_external_hook_env_channels_reject_internal_authority_metadata(
    provenance: str, producer: str | None, entrypoint: str | None
) -> None:
    entry = HookEnvVarDef(
        "AUTOSKILLIT_EXTERNAL_TEST",
        provenance,
        producer,
        entrypoint,
        "External channels must not claim AutoSkillit-owned delivery authority.",
    )

    with pytest.raises(AssertionError, match="must not declare producer or entrypoint"):
        _validate_hook_env_contract((entry,))


def test_autoskillit_contract_vars_have_producer_and_boundary_evidence() -> None:
    surface = production_env_write_surface(SRC_ROOT)
    assert not surface.unparseable_files, surface.unparseable_files

    failures: list[str] = []
    for entry in HOOK_ENV_CONTRACT:
        if entry.provenance != "autoskillit":
            continue
        assert entry.producer is not None
        assert entry.entrypoint is not None
        _resolve_function(entry.producer)
        _resolve_function(entry.entrypoint)
        producer_file = _producer_source_file(entry.producer)
        producer_node = _function_node(entry.producer)
        reachable_lines = {
            node.lineno for node in _reachable_nodes(producer_node) if hasattr(node, "lineno")
        }
        evidence = [
            write
            for write in surface.writes
            if write.var == entry.var
            and write.file == producer_file
            and write.line in reachable_lines
        ]
        if not evidence:
            failures.append(f"{entry.var} from {entry.producer}")
            continue
        assert all(write.carrier and write.boundary for write in evidence), evidence
        if entry.entrypoint != entry.producer:
            entrypoint_node = _function_node(entry.entrypoint)
            producer_name = entry.producer.rpartition(":")[2].rpartition(".")[2]
            assert _literal_call_reaches(entrypoint_node, producer_name), (
                f"{entry.entrypoint} does not reach declared producer {entry.producer}"
            )
    assert not failures, (
        "AutoSkillit-owned hook env vars need a literal producer write whose carrier reaches "
        "a frozen subprocess/backend environment boundary: " + ", ".join(failures)
    )


def test_producer_reachability_rejects_dead_literal_calls() -> None:
    tree = ast.parse(
        "def producer():\n"
        "    pass\n\n"
        "def never_calls():\n"
        "    pass\n\n"
        "def always_false():\n"
        "    if False:\n"
        "        producer()\n\n"
        "def after_return():\n"
        "    return\n"
        "    producer()\n\n"
        "def direct():\n"
        "    producer()\n"
    )
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert not _literal_call_reaches(functions["never_calls"], "producer")
    assert not _literal_call_reaches(functions["always_false"], "producer")
    assert not _literal_call_reaches(functions["after_return"], "producer")
    assert _literal_call_reaches(functions["direct"], "producer")
