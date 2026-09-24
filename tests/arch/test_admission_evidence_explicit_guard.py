"""Production code hands launch evidence explicitly to every evidence-bearing callable.

Rule: every production call to a callable that declares ``adaptation_context`` (a
function parameter or a dataclass field) passes it explicitly. ``None`` is permitted
only where no launch evidence exists or may exist, and each such site is inventoried
in ``_NO_EVIDENCE_SITES`` with its reason. Every ``adaptation_context`` is typed as
launch evidence.

Each episode of this issue family (#4719, #5147, #5158, #5153) lost managed-join
evidence on a hop between issuance and admission, where a ``None`` default made an
omission indistinguishable from a deliberate session-invariant evaluation.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.contracts._ast_helpers import call_name

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
_EVIDENCE = "adaptation_context"
_LAUNCH_EVIDENCE_ANNOTATIONS = frozenset(
    {"SemanticAdaptationContext | None", "SemanticAdaptationContext"}
)
_MIN_RATIONALE_CHARS = 40
_KNOWN_EVIDENCE_ANCHORS = frozenset(
    {
        "adapt_skill_semantics",
        "compile_session_skill_catalog",
        "check_skill_semantic_feasibility",
        "_check_backend_compat",
        "SkillProjectionContext",
        "SkillProjectionPreparation",
        "build_fresh_projection_context",
        "catalog_projection_context",
        "configure_managed_session_dir",
    }
)

SiteKey = tuple[str, str, str]

_NO_EVIDENCE_SITES: dict[SiteKey, str] = {
    (
        "core/types/_type_skill_semantics.py",
        "adapt_session_invariant",
        "adapt_skill_semantics",
    ): (
        "The single session-invariant evaluator; evidence-dependent refusals become "
        "LaunchEvidenceDeferral, never verdicts."
    ),
    (
        "workspace/_projected_artifact/_documents.py",
        "_direct_install_projection_context",
        "SkillProjectionContext",
    ): (
        "Shared plugin projection is content-addressed across launches; launch evidence "
        "would make its semantic key per-launch."
    ),
    ("_llm_triage.py", "triage_staleness", "SkillProjectionContext"): (
        "Offline contract-staleness rendering runs outside any launch, so no managed-join "
        "evidence exists."
    ),
    ("cli/install/_marketplace.py", "_ensure_marketplace", "SkillProjectionContext"): (
        "Install-time marketplace rendering runs outside any launch, so no managed-join "
        "evidence exists."
    ),
    ("cli/install/_plugin_artifact.py", "_self_heal_republish", "SkillProjectionContext"): (
        "Republishes the session-invariant shared plugin artifact, which must never carry "
        "launch evidence."
    ),
    ("cli/prompts/_prompts.py", "_read_full_sous_chef", "SkillProjectionContext"): (
        "Renders sous-chef guidance text; sous-chef declares no semantic join, so evidence "
        "cannot change its rendering."
    ),
    (
        "workspace/session_skills/_provider.py",
        "projection_context",
        "catalog_projection_context",
    ): (
        "Serves dev-checkout readers only; there is no artifact binding and no launch that "
        "could hold evidence."
    ),
    (
        "server/tools/tools_execution/_run_skill_prepare.py",
        "_prepare_config_and_step_fallback",
        "build_fresh_projection_context",
    ): (
        "Pre-issuance build; _prepare_managed_parent_projection rebinds the context with the "
        "issued evidence before admission."
    ),
    (
        "server/tools/tools_execution/_run_skill_session.py",
        "_resolve_fresh_invocation",
        "build_fresh_projection_context",
    ): (
        "Same pre-issuance build as candidate preparation; the context is rebound with issued "
        "evidence after issuance."
    ),
    (
        "server/tools/_execution_helpers/_skill_contract.py",
        "rehydrate_skill_invocation",
        "SkillProjectionContext",
    ): (
        "Resume rehydration; evidence is re-issued under the stored lineage launch id and "
        "rebound before admission."
    ),
    (
        "server/tools/tools_kitchen/_open_kitchen/_orchestrator.py",
        "_build_anonymous_open_response",
        "project_orchestrator_guidance",
    ): "open_kitchen guidance is rendered before any launch exists, so no evidence can be held.",
    (
        "server/tools/_backend_compat.py",
        "_prepare_direct_skill_dispatch",
        "_check_backend_compat",
    ): (
        "report_bug/prepare_issue direct dispatch issues no managed-join evidence, so a "
        "join.required root is refused fail-closed as an unattested launch."
    ),
    (
        "server/tools/_backend_compat.py",
        "_prepare_direct_skill_dispatch",
        "SkillProjectionContext",
    ): (
        "Same unattested report_bug/prepare_issue direct launch; no managed-join evidence is "
        "issued for it."
    ),
}


@dataclass(frozen=True, slots=True)
class _EvidenceSlot:
    """Where one discovered callable declares ``adaptation_context``."""

    location: str
    positional_index: int | None
    annotation: str


@dataclass(slots=True)
class _Scope:
    name: str
    imports: set[str] = field(default_factory=set)
    shadowed: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _Findings:
    omissions: tuple[SiteKey, ...]
    literal_none: tuple[SiteKey, ...]


def _source_trees(root: Path) -> dict[str, ast.Module]:
    return {
        str(path.relative_to(root)): ast.parse(path.read_text(encoding="utf-8"), str(path))
        for path in sorted(root.rglob("*.py"))
    }


def _annotation_text(annotation: ast.expr | None) -> str:
    return ast.unparse(annotation) if annotation is not None else ""


def _function_slots(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    location: str,
) -> Iterator[_EvidenceSlot]:
    positional = [*node.args.posonlyargs, *node.args.args]
    if positional and positional[0].arg in {"self", "cls"}:
        positional = positional[1:]
    for index, arg in enumerate(positional):
        if arg.arg == _EVIDENCE:
            yield _EvidenceSlot(location, index, _annotation_text(arg.annotation))
    for arg in node.args.kwonlyargs:
        if arg.arg == _EVIDENCE:
            yield _EvidenceSlot(location, None, _annotation_text(arg.annotation))


def _discover(trees: Mapping[str, ast.Module]) -> dict[str, list[_EvidenceSlot]]:
    """Map each evidence-bearing callable name to its declarations."""
    discovered: dict[str, list[_EvidenceSlot]] = {}
    for relative_path, tree in trees.items():
        for node in ast.walk(tree):
            location = f"{relative_path}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                slots = list(_function_slots(node, location))
            elif isinstance(node, ast.ClassDef):
                slots = [
                    _EvidenceSlot(location, None, _annotation_text(statement.annotation))
                    for statement in node.body
                    if isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and statement.target.id == _EVIDENCE
                ]
            else:
                continue
            if slots:
                discovered.setdefault(node.name, []).extend(slots)
    return discovered


def _scope_statements(body: Iterable[ast.stmt]) -> Iterator[ast.AST]:
    """Walk one scope's nodes, yielding nested definitions without entering them."""
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            pending.extend(ast.iter_child_nodes(node))


def _imported_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {(alias.asname or alias.name).split(".")[0] for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        return {alias.asname or alias.name for alias in node.names}
    return set()


def _assigned_names(node: ast.AST) -> set[str]:
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.For, ast.AsyncFor)):
        targets = [node.target]
    elif isinstance(node, ast.withitem) and node.optional_vars is not None:
        targets = [node.optional_vars]
    elif isinstance(node, ast.ExceptHandler) and node.name is not None:
        return {node.name}
    return {
        name.id for target in targets for name in ast.walk(target) if isinstance(name, ast.Name)
    }


def _function_scope(node: ast.FunctionDef | ast.AsyncFunctionDef) -> _Scope:
    scope = _Scope(node.name)
    arguments = node.args
    scope.shadowed.update(
        arg.arg
        for arg in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *(item for item in (arguments.vararg, arguments.kwarg) if item is not None),
        )
    )
    for statement in _scope_statements(node.body):
        scope.imports.update(_imported_names(statement))
        scope.shadowed.update(_assigned_names(statement))
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope.shadowed.add(statement.name)
    scope.shadowed -= scope.imports
    return scope


def _module_bindings(tree: ast.Module) -> set[str]:
    bindings: set[str] = set()
    for statement in _scope_statements(tree.body):
        bindings.update(_imported_names(statement))
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.add(statement.name)
    return bindings


def _name_resolves(name: str, scopes: list[_Scope], module_bindings: set[str]) -> bool:
    for scope in reversed(scopes):
        if name in scope.shadowed:
            return False
        if name in scope.imports:
            return True
    return name in module_bindings


def _evidence_argument(call: ast.Call, slots: list[_EvidenceSlot]) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == _EVIDENCE:
            return keyword.value
    indices = {slot.positional_index for slot in slots}
    if len(indices) != 1:
        return None
    (index,) = indices
    if index is None or len(call.args) <= index:
        return None
    if any(isinstance(argument, ast.Starred) for argument in call.args[: index + 1]):
        return None
    return call.args[index]


def _scan_module(
    relative_path: str,
    tree: ast.Module,
    discovered: Mapping[str, list[_EvidenceSlot]],
) -> tuple[list[SiteKey], list[SiteKey]]:
    omissions: list[SiteKey] = []
    literal_none: list[SiteKey] = []
    module_bindings = _module_bindings(tree)

    def visit(node: ast.AST, scopes: list[_Scope]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes = [*scopes, _function_scope(node)]
        if isinstance(node, ast.Call):
            name = call_name(node)
            if name in discovered and (
                isinstance(node.func, ast.Attribute)
                or _name_resolves(name, scopes, module_bindings)
            ):
                key = (relative_path, scopes[-1].name if scopes else "<module>", name)
                argument = _evidence_argument(node, discovered[name])
                if argument is None:
                    omissions.append(key)
                elif isinstance(argument, ast.Constant) and argument.value is None:
                    literal_none.append(key)
        for child in ast.iter_child_nodes(node):
            visit(child, scopes)

    visit(tree, [])
    return omissions, literal_none


def _scan(trees: Mapping[str, ast.Module]) -> _Findings:
    discovered = _discover(trees)
    omissions: list[SiteKey] = []
    literal_none: list[SiteKey] = []
    for relative_path, tree in trees.items():
        module_omissions, module_literal_none = _scan_module(relative_path, tree, discovered)
        omissions.extend(module_omissions)
        literal_none.extend(module_literal_none)
    return _Findings(tuple(sorted(omissions)), tuple(sorted(literal_none)))


def _production_trees() -> dict[str, ast.Module]:
    return _source_trees(_SRC_ROOT)


def test_discovery_finds_known_evidence_anchors() -> None:
    discovered = _discover(_production_trees())
    missing = sorted(_KNOWN_EVIDENCE_ANCHORS - set(discovered))
    assert not missing, f"evidence discovery lost known anchors: {missing}"


_SYNTHETIC_SOURCE = """
from evidence import SemanticAdaptationContext


def admit(plan, adaptation_context: SemanticAdaptationContext | None = None):
    return plan


def omitted():
    return admit("plan")


def imported_inside():
    from elsewhere import admit
    return admit("plan")


def splat(kwargs):
    return admit(**kwargs)


def passed(ctx):
    return admit("plan", adaptation_context=ctx)


def passed_positionally(ctx):
    return admit("plan", ctx)


def literal_none():
    return admit("plan", adaptation_context=None)


def shadowed(admit):
    return admit("plan")
"""


def test_detector_flags_synthetic_omission() -> None:
    findings = _scan({"synthetic.py": ast.parse(_SYNTHETIC_SOURCE)})

    assert findings.omissions == (
        ("synthetic.py", "imported_inside", "admit"),
        ("synthetic.py", "omitted", "admit"),
        ("synthetic.py", "splat", "admit"),
    )
    assert findings.literal_none == (("synthetic.py", "literal_none", "admit"),)


def test_every_evidence_parameter_is_typed_as_launch_evidence() -> None:
    untyped = sorted(
        f"{slot.location}: {slot.annotation or '<missing>'}"
        for slots in _discover(_production_trees()).values()
        for slot in slots
        if slot.annotation not in _LAUNCH_EVIDENCE_ANNOTATIONS
    )
    assert not untyped, (
        f"every {_EVIDENCE} must be typed as launch evidence "
        f"({sorted(_LAUNCH_EVIDENCE_ANNOTATIONS)}): {untyped}"
    )


def test_every_production_call_passes_evidence_explicitly() -> None:
    omissions = _scan(_production_trees()).omissions
    assert not omissions, (
        f"calls to evidence-bearing callables must pass {_EVIDENCE} explicitly: pass the "
        "in-scope launch evidence, or a literal None registered in _NO_EVIDENCE_SITES "
        f"with its reason: {list(omissions)}"
    )


def test_literal_none_evidence_only_at_registered_sites() -> None:
    unregistered = sorted(set(_scan(_production_trees()).literal_none) - set(_NO_EVIDENCE_SITES))
    assert not unregistered, (
        f"literal-None {_EVIDENCE} requires a reasoned _NO_EVIDENCE_SITES entry: {unregistered}"
    )


def _registry_literal_keys() -> list[SiteKey]:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for statement in tree.body:
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "_NO_EVIDENCE_SITES"
            and isinstance(statement.value, ast.Dict)
        ):
            return [ast.literal_eval(key) for key in statement.value.keys if key is not None]
    raise AssertionError("_NO_EVIDENCE_SITES must be a literal dict")


def test_no_evidence_registry_is_current_and_reasoned() -> None:
    live = set(_scan(_production_trees()).literal_none)
    stale = sorted(set(_NO_EVIDENCE_SITES) - live)
    thin = sorted(
        key
        for key, rationale in _NO_EVIDENCE_SITES.items()
        if len(rationale.strip()) < _MIN_RATIONALE_CHARS
    )
    literal_keys = _registry_literal_keys()
    duplicates = sorted({key for key in literal_keys if literal_keys.count(key) > 1})
    assert not stale, f"_NO_EVIDENCE_SITES entries no longer match a literal None: {stale}"
    assert not thin, f"_NO_EVIDENCE_SITES rationales are too thin: {thin}"
    assert not duplicates, f"_NO_EVIDENCE_SITES repeats sites: {duplicates}"
