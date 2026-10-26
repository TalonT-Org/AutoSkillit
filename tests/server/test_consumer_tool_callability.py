"""Bundled consumers must be able to call tools in their actual session catalogs.

Every SESSION skill is checked in both headless contexts unless it declares
``interactive_only: true``. Safe-by-design exceptions are typed and verified;
known defects are tracked as dated deferrals with regression tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import pytest

from autoskillit.core import (
    GATED_TOOLS,
    RecipeSource,
    SkillExecutionRole,
    all_tool_names,
    resolve_skill_name,
)
from autoskillit.core.tool_registry import get_tool_def
from autoskillit.recipe.io import (
    list_recipes as list_recipe_infos,
)
from autoskillit.recipe.io import (
    load_recipe as load_recipe_file,
)
from autoskillit.recipe.io import (
    step_byte_ranges_from_yaml,
)
from autoskillit.recipe.schema import Recipe, RecipeInfo
from autoskillit.server.lifecycle._session_scope import TOOL_SESSION_SCOPES
from autoskillit.workspace.skill_capabilities._scanner import (
    _LOGICAL_CONTINUATION_RE,
    _evidence,
    _has_tool_operation,
    _logical_lines,
    _source_lines,
    _SourceLine,
)
from autoskillit.workspace.skills import DefaultSkillResolver, SkillInfo
from tests.arch._deferred_debt import (
    TrackedDeferral,
    assert_deferrals_have_regression_tests,
    assert_not_stale,
    assert_rationale_present,
)
from tests.server._session_catalogs import CatalogContext, SessionCatalog, build_session_catalog

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

ExemptionKey = tuple[str, str, CatalogContext]


@dataclass(frozen=True, slots=True)
class ConsumerExemption:
    kind: Literal["decision", "guard"]
    rationale: str
    issue: int | None = None
    anchor: str | None = None

    def __post_init__(self) -> None:
        if len(self.rationale.strip()) < 20:
            raise ValueError("exemption rationale must explain why the call is safe")
        if self.kind == "decision" and self.issue is None:
            raise ValueError("decision exemption must cite the deciding issue number")
        if self.kind == "guard" and not (self.anchor and self.anchor.strip()):
            raise ValueError("guard exemption must quote the guarding sentence as anchor")


_REVIEW_HEADLESS_GUARD = (
    "stop with `verdict=needs_human`, print `%%REVIEW_GATE::CLEAR%%`, and exit in a "
    "headless session"
)
_WRITE_RECIPE_HEADLESS_GUARD = (
    "In a headless session, skip every step that asks the user or calls an MCP kitchen "
    "tool; apply the format rules in this document, save the YAML, and return — the "
    "recipe is validated when it is next loaded."
)

EXEMPTIONS: dict[ExemptionKey, ConsumerExemption] = {
    (
        "skill:audit-claims",
        "post_pr_review",
        CatalogContext.INTERACTIVE_SKILL,
    ): ConsumerExemption(
        "decision",
        issue=4448,
        rationale="review publication is headless-only by decision (ba2ccd424)",
    ),
    (
        "skill:resolve-review",
        "post_pr_review",
        CatalogContext.INTERACTIVE_SKILL,
    ): ConsumerExemption(
        "decision",
        issue=4448,
        rationale="review publication is headless-only by decision (ba2ccd424)",
    ),
    (
        "skill:review-pr",
        "post_pr_review",
        CatalogContext.INTERACTIVE_SKILL,
    ): ConsumerExemption(
        "decision",
        issue=4448,
        rationale="review publication is headless-only by decision (ba2ccd424)",
    ),
    (
        "skill:review-research-pr",
        "post_pr_review",
        CatalogContext.INTERACTIVE_SKILL,
    ): ConsumerExemption(
        "decision",
        issue=4448,
        rationale="review publication is headless-only by decision (ba2ccd424)",
    ),
    (
        "skill:review-pr",
        "run_python",
        CatalogContext.HEADLESS_SKILL,
    ): ConsumerExemption(
        "guard",
        rationale="review-pr exits before annotation tooling in headless sessions",
        anchor=_REVIEW_HEADLESS_GUARD,
    ),
    (
        "skill:review-pr",
        "run_python",
        CatalogContext.HEADLESS_SKILL_AUTO_GATE,
    ): ConsumerExemption(
        "guard",
        rationale="review-pr exits before annotation tooling in headless sessions",
        anchor=_REVIEW_HEADLESS_GUARD,
    ),
    (
        "skill:resolve-failures",
        "run_cmd",
        CatalogContext.HEADLESS_SKILL,
    ): ConsumerExemption(
        "guard",
        rationale="negated mention in the test_check instruction, not a run_cmd call",
        anchor="not via Bash or `run_cmd`",
    ),
    (
        "skill:resolve-failures",
        "run_cmd",
        CatalogContext.HEADLESS_SKILL_AUTO_GATE,
    ): ConsumerExemption(
        "guard",
        rationale="negated mention in the test_check instruction, not a run_cmd call",
        anchor="not via Bash or `run_cmd`",
    ),
    (
        "skill:write-recipe",
        "kitchen_status",
        CatalogContext.HEADLESS_SKILL,
    ): ConsumerExemption(
        "guard",
        rationale="the document-wide mode rule skips kitchen tools in headless sessions",
        anchor=_WRITE_RECIPE_HEADLESS_GUARD,
    ),
    (
        "skill:write-recipe",
        "load_recipe",
        CatalogContext.HEADLESS_SKILL,
    ): ConsumerExemption(
        "guard",
        rationale="the document-wide mode rule skips kitchen tools in headless sessions",
        anchor=_WRITE_RECIPE_HEADLESS_GUARD,
    ),
}

DEFERRED_VIOLATIONS: dict[ExemptionKey, TrackedDeferral] = {
    (
        "skill:process-issues",
        "batch_cleanup_clones",
        CatalogContext.INTERACTIVE_ORCHESTRATOR,
    ): TrackedDeferral(
        issue=5179,
        added_date=date(2026, 9, 25),
        rationale="fleet mutation tool is hidden from interactive orchestrators",
        regression_test=(
            "tests/server/test_consumer_tool_callability.py::"
            "test_skill_tool_operations_are_callable"
        ),
    ),
    (
        "recipe:implement-findings:batch_cleanup",
        "batch_cleanup_clones",
        CatalogContext.INTERACTIVE_ORCHESTRATOR,
    ): TrackedDeferral(
        issue=5179,
        added_date=date(2026, 9, 25),
        rationale="fleet mutation tool is hidden from interactive orchestrators",
        regression_test=(
            "tests/server/test_consumer_tool_callability.py::test_recipe_tool_steps_are_callable"
        ),
    ),
}


@pytest.fixture
def bundled_recipes(tmp_path: Path) -> tuple[tuple[RecipeInfo, Recipe], ...]:
    infos = (
        info for info in list_recipe_infos(tmp_path).items if info.source is RecipeSource.BUILTIN
    )
    return tuple((info, load_recipe_file(info.path)) for info in infos)


def _logical_text(logical: tuple[_SourceLine, ...]) -> str:
    return _LOGICAL_CONTINUATION_RE.sub(" ", "\n".join(line.text for line in logical))


def _pseudo_call_operation(
    logicals: tuple[tuple[_SourceLine, ...], ...],
    index: int,
    tool_names: set[str],
) -> tuple[str, int] | None:
    logical = logicals[index]
    text = _logical_text(logical)
    match = re.fullmatch(r"(?P<tool>[a-z_]+):\s*", text.strip())
    if match is None or (tool := match.group("tool")) not in tool_names:
        return None
    parameter_logical = next(
        (
            candidate
            for candidate in logicals[index + 1 :]
            if (candidate_text := _logical_text(candidate)).strip()
            and not candidate_text.lstrip().startswith("#")
        ),
        None,
    )
    if parameter_logical is None:
        return None
    parameter_text = _logical_text(parameter_logical)
    parameter = re.match(r"^\s+(?P<param>[a-z_]+)\s*:", parameter_text)
    header_indent = len(text) - len(text.lstrip())
    parameter_indent = len(parameter_text) - len(parameter_text.lstrip())
    definition = get_tool_def(tool)
    if (
        parameter is None
        or parameter_indent <= header_indent
        or definition is None
        or parameter.group("param") not in {item.name for item in definition.params}
    ):
        return None
    evidence = _evidence(tool, logical + parameter_logical)
    return (tool, evidence.source_span[0]) if evidence.is_genuine else None


def _skill_tool_operations(content: str) -> list[tuple[str, int]]:
    operations: list[tuple[str, int]] = []
    tool_names = set(all_tool_names())
    logicals = _logical_lines(_source_lines(content))
    for index, logical in enumerate(logicals):
        text = _logical_text(logical)
        for tool in sorted(tool_names):
            validate_imperative = re.search(
                rf"\bvalidate\s+(?:via|using|with)\s+`?\b{re.escape(tool)}\b",
                text,
                re.IGNORECASE,
            )
            if _has_tool_operation(text, tool) or validate_imperative:
                evidence = _evidence(tool, logical)
                if evidence.is_genuine:
                    operations.append((tool, evidence.source_span[0]))
        pseudo_call = _pseudo_call_operation(logicals, index, tool_names)
        if pseudo_call is not None:
            operations.append(pseudo_call)
    return operations


def _is_exempt(key: ExemptionKey) -> bool:
    return key in EXEMPTIONS or key in DEFERRED_VIOLATIONS


def _skill_contexts(skill: SkillInfo) -> set[CatalogContext]:
    role = skill.execution_role
    assert role is not None, f"{skill.name}: missing execution role; {skill.invalidities=}"
    match role:
        case SkillExecutionRole.SESSION:
            if skill.interactive_only:
                return {CatalogContext.INTERACTIVE_SKILL}
            return {
                CatalogContext.INTERACTIVE_SKILL,
                CatalogContext.HEADLESS_SKILL,
                CatalogContext.HEADLESS_SKILL_AUTO_GATE,
            }
        case SkillExecutionRole.ORCHESTRATOR:
            return {CatalogContext.INTERACTIVE_ORCHESTRATOR}
        case SkillExecutionRole.FLEET:
            return {CatalogContext.FLEET_DISPATCH}
    raise AssertionError(f"unknown skill execution role: {role!r}")


def _violation(
    consumer: str,
    tool: str,
    line: int,
    context: CatalogContext,
    catalog: SessionCatalog,
) -> str:
    visible = tool in catalog.tools
    scope = TOOL_SESSION_SCOPES.get(tool)
    admitted = scope is not None and scope.admits(catalog.shape)
    gate = catalog.gate_open or tool not in GATED_TOOLS
    return (
        f"{consumer}:{line} {tool} {context.name} "
        f"visible={visible} admitted={admitted} gate={gate}"
    )


def _assert_contract(
    unexpected: list[str],
    violations: set[ExemptionKey],
    *,
    consumer_prefix: str,
    contexts: set[CatalogContext],
) -> None:
    registries = {"EXEMPTIONS": EXEMPTIONS, "DEFERRED_VIOLATIONS": DEFERRED_VIOLATIONS}
    stale = sorted(
        f"{registry_name}: {key!r}"
        for registry_name, registry in registries.items()
        for key in registry
        if key[0].startswith(consumer_prefix) and key[2] in contexts and key not in violations
    )
    assert not unexpected and not stale, (
        "Bundled tool callability violations:\n"
        + "\n".join(unexpected)
        + f"\nStale exemptions: {stale}\n"
        + "Remedy order: fix an intended INSPECTION tool's tags or tier; "
        "exempt only with a cited decision or guard; otherwise file an issue and cite it."
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "context",
    [
        CatalogContext.INTERACTIVE_SKILL,
        CatalogContext.INTERACTIVE_ORCHESTRATOR,
        CatalogContext.HEADLESS_SKILL,
        CatalogContext.HEADLESS_SKILL_AUTO_GATE,
        CatalogContext.FLEET_DISPATCH,
    ],
)
async def test_skill_tool_operations_are_callable(
    context: CatalogContext,
    monkeypatch: pytest.MonkeyPatch,
    build_ctx,
) -> None:
    catalog = await build_session_catalog(context, monkeypatch=monkeypatch, build_ctx=build_ctx)
    callable_tools = catalog.callable_tools()
    unexpected: list[str] = []
    violations: set[ExemptionKey] = set()

    for skill in DefaultSkillResolver().list_all():
        if context not in _skill_contexts(skill):
            continue
        consumer = f"skill:{skill.name}"
        for tool, line in _skill_tool_operations(skill.canonical_content):
            if tool in callable_tools:
                continue
            key = (consumer, tool, context)
            violations.add(key)
            if not _is_exempt(key):
                unexpected.append(_violation(consumer, tool, line, context, catalog))

    _assert_contract(unexpected, violations, consumer_prefix="skill:", contexts={context})


@pytest.mark.anyio
async def test_recipe_tool_steps_are_callable(
    bundled_recipes: tuple[tuple[RecipeInfo, Recipe], ...],
    monkeypatch: pytest.MonkeyPatch,
    build_ctx,
) -> None:
    interactive = await build_session_catalog(
        CatalogContext.INTERACTIVE_ORCHESTRATOR,
        monkeypatch=monkeypatch,
        build_ctx=build_ctx,
    )
    food_trucks: dict[tuple[str, ...], SessionCatalog] = {}
    unexpected: list[str] = []
    violations: set[ExemptionKey] = set()

    for info, recipe in bundled_recipes:
        packs = tuple(sorted(recipe.requires_packs)) or ("kitchen-core",)
        if packs not in food_trucks:
            food_trucks[packs] = await build_session_catalog(
                CatalogContext.FOOD_TRUCK,
                monkeypatch=monkeypatch,
                build_ctx=build_ctx,
                packs=packs,
            )
        content = info.path.read_bytes()
        spans = step_byte_ranges_from_yaml(content.decode("utf-8"))
        for step_name, step in recipe.steps.items():
            if step.tool is None:
                continue
            consumer = f"recipe:{recipe.name}:{step_name}"
            line = content[: spans[step_name][0]].count(b"\n") + 1
            for context, catalog in (
                (CatalogContext.INTERACTIVE_ORCHESTRATOR, interactive),
                (CatalogContext.FOOD_TRUCK, food_trucks[packs]),
            ):
                if step.tool in catalog.callable_tools():
                    continue
                key = (consumer, step.tool, context)
                violations.add(key)
                if not _is_exempt(key):
                    unexpected.append(_violation(consumer, step.tool, line, context, catalog))

    _assert_contract(
        unexpected,
        violations,
        consumer_prefix="recipe:",
        contexts={CatalogContext.INTERACTIVE_ORCHESTRATOR, CatalogContext.FOOD_TRUCK},
    )


def test_tool_operation_detection_recognizes_pseudo_call() -> None:
    content = (
        "# Workflow\n\n1. Persist the failure:\n\n  run_python:\n    callable: module.record\n"
    )
    assert _skill_tool_operations(content) == [("run_python", 5)]


def test_tool_operation_detection_recognizes_validate_imperative() -> None:
    content = "# Workflow\n\n1. Validate via `validate_recipe` before declaring success\n"
    assert _skill_tool_operations(content) == [("validate_recipe", 3)]


def test_tool_operation_detection_ignores_unrelated_validate_reference() -> None:
    content = "# Workflow\n\n1. Validate the plan before `commit_files`\n"
    assert _skill_tool_operations(content) == []


def test_tool_operation_detection_rejects_unregistered_parameter() -> None:
    content = "# Workflow\n\n1. Run the check:\n\n  test_check:\n    command: task test\n"
    assert _skill_tool_operations(content) == []


def test_tool_operation_detection_rejects_same_indent_parameter() -> None:
    content = "1. Run the callable:\n\nrun_python:\ncallable: module.record\n"
    assert _skill_tool_operations(content) == []


def test_guard_exemption_anchors_exist() -> None:
    skills = {skill.name: skill for skill in DefaultSkillResolver().list_all()}
    for (consumer, _tool, _context), exemption in EXEMPTIONS.items():
        if exemption.kind != "guard":
            continue
        assert consumer.startswith("skill:")
        skill_name = consumer.removeprefix("skill:")
        anchor = " ".join((exemption.anchor or "").split())
        assert anchor in " ".join(skills[skill_name].canonical_content.split())


def test_deferred_violations_are_tracked() -> None:
    assert_not_stale(DEFERRED_VIOLATIONS, registry_name="DEFERRED_VIOLATIONS")
    assert_rationale_present(DEFERRED_VIOLATIONS, registry_name="DEFERRED_VIOLATIONS")
    assert_deferrals_have_regression_tests(
        DEFERRED_VIOLATIONS,
        registry_name="DEFERRED_VIOLATIONS",
    )


def test_bundled_run_skill_targets_are_not_interactive_only(
    bundled_recipes: tuple[tuple[RecipeInfo, Recipe], ...],
) -> None:
    skills = {skill.name: skill for skill in DefaultSkillResolver().list_all()}
    targets = {
        name
        for _, recipe in bundled_recipes
        for step in recipe.steps.values()
        if step.tool == "run_skill"
        if (name := resolve_skill_name(str(step.with_args.get("skill_command", "")))) is not None
    }
    interactive_targets = sorted(name for name in targets if skills[name].interactive_only)
    assert not interactive_targets, (
        f"bundled recipes dispatch interactive-only skills: {interactive_targets}"
    )
