"""Bundled consumers must be able to call tools in their actual session catalogs.

Each exempted violation in EXEMPTIONS cites the decision or guard that makes
the call safe; the assertion failure message explains the remedy order for
adding a new exemption.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    GATED_TOOLS,
    RecipeSource,
    SkillExecutionRole,
    all_tool_names,
    resolve_skill_name,
)
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
)
from autoskillit.workspace.skills import DefaultSkillResolver
from tests.server._session_catalogs import CatalogContext, SessionCatalog, build_session_catalog

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

ExemptionKey = tuple[str, str, CatalogContext]

EXEMPTIONS: dict[ExemptionKey, str] = {
    (
        "skill:audit-claims",
        "post_pr_review",
        CatalogContext.INTERACTIVE_SKILL,
    ): "#4448 (ba2ccd424): review publication is headless-only",
    (
        "skill:resolve-review",
        "post_pr_review",
        CatalogContext.INTERACTIVE_SKILL,
    ): "#4448 (ba2ccd424): review publication is headless-only",
    (
        "skill:review-pr",
        "post_pr_review",
        CatalogContext.INTERACTIVE_SKILL,
    ): "#4448 (ba2ccd424): review publication is headless-only",
    (
        "skill:review-research-pr",
        "post_pr_review",
        CatalogContext.INTERACTIVE_SKILL,
    ): "#4448 (ba2ccd424): review publication is headless-only",
    (
        "skill:review-pr",
        "run_python",
        CatalogContext.HEADLESS_SKILL,
    ): "review-pr/SKILL.md:284-296 exits before this call when AUTOSKILLIT_HEADLESS=1",
    (
        "skill:review-pr",
        "run_python",
        CatalogContext.HEADLESS_SKILL_AUTO_GATE,
    ): "review-pr/SKILL.md:284-296 exits before this call when AUTOSKILLIT_HEADLESS=1",
    (
        "skill:resolve-failures",
        "run_cmd",
        CatalogContext.HEADLESS_SKILL,
    ): "resolve-failures/SKILL.md:165 says not via Bash or run_cmd",
    (
        "skill:resolve-failures",
        "run_cmd",
        CatalogContext.HEADLESS_SKILL_AUTO_GATE,
    ): "resolve-failures/SKILL.md:165 says not via Bash or run_cmd",
    (
        "skill:audit-impl",
        "run_python",
        CatalogContext.HEADLESS_SKILL,
    ): "#5177: move the Step 3.4 probe to the parent-side floor check",
    (
        "skill:audit-impl",
        "run_python",
        CatalogContext.HEADLESS_SKILL_AUTO_GATE,
    ): "#5177: move the Step 3.4 probe to the parent-side floor check",
    (
        "skill:process-issues",
        "batch_cleanup_clones",
        CatalogContext.INTERACTIVE_ORCHESTRATOR,
    ): "#5179: fleet mutation tool is hidden from interactive orchestrators",
    (
        "recipe:implement-findings:batch_cleanup",
        "batch_cleanup_clones",
        CatalogContext.INTERACTIVE_ORCHESTRATOR,
    ): "#5179: fleet mutation tool is hidden from interactive orchestrators",
    (
        "recipe:research:check_review_posted",
        "verify_review_receipt",
        CatalogContext.FOOD_TRUCK,
    ): "#5178: recipe does not declare the github pack",
    (
        "recipe:research:check_audit_review_posted",
        "verify_review_receipt",
        CatalogContext.FOOD_TRUCK,
    ): "#5178: recipe does not declare the github pack",
    (
        "recipe:research-review:check_review_posted",
        "verify_review_receipt",
        CatalogContext.FOOD_TRUCK,
    ): "#5178: recipe does not declare the github pack",
    (
        "recipe:research-review:check_audit_review_posted",
        "verify_review_receipt",
        CatalogContext.FOOD_TRUCK,
    ): "#5178: recipe does not declare the github pack",
}


@pytest.fixture
def bundled_recipes(tmp_path: Path) -> tuple[tuple[RecipeInfo, Recipe], ...]:
    infos = (
        info for info in list_recipe_infos(tmp_path).items if info.source is RecipeSource.BUILTIN
    )
    return tuple((info, load_recipe_file(info.path)) for info in infos)


def _skill_tool_operations(content: str) -> list[tuple[str, int]]:
    operations: list[tuple[str, int]] = []
    tool_names = sorted(all_tool_names())
    for logical in _logical_lines(_source_lines(content)):
        text = _LOGICAL_CONTINUATION_RE.sub(" ", "\n".join(line.text for line in logical))
        for tool in tool_names:
            if _has_tool_operation(text, tool):
                evidence = _evidence(tool, logical)
                if evidence.is_genuine:
                    operations.append((tool, evidence.source_span[0]))
    return operations


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
    scoped = {
        key for key in EXEMPTIONS if key[0].startswith(consumer_prefix) and key[2] in contexts
    }
    stale = sorted(scoped - violations)
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
    bundled_recipes: tuple[tuple[RecipeInfo, Recipe], ...],
    monkeypatch: pytest.MonkeyPatch,
    build_ctx,
) -> None:
    child_skills = {
        name
        for _, recipe in bundled_recipes
        for step in recipe.steps.values()
        if step.tool == "run_skill"
        if (name := resolve_skill_name(str(step.with_args.get("skill_command", "")))) is not None
    }
    catalog = await build_session_catalog(context, monkeypatch=monkeypatch, build_ctx=build_ctx)
    callable_tools = catalog.callable_tools()
    unexpected: list[str] = []
    violations: set[ExemptionKey] = set()

    for skill in DefaultSkillResolver().list_all():
        assert skill.execution_role is not None, (
            f"{skill.name}: missing execution role; invalidities={skill.invalidities}"
        )
        match skill.execution_role:
            case SkillExecutionRole.SESSION:
                contexts = {CatalogContext.INTERACTIVE_SKILL}
                if skill.name in child_skills:
                    contexts.update(
                        {CatalogContext.HEADLESS_SKILL, CatalogContext.HEADLESS_SKILL_AUTO_GATE}
                    )
            case SkillExecutionRole.ORCHESTRATOR:
                contexts = {CatalogContext.INTERACTIVE_ORCHESTRATOR}
            case SkillExecutionRole.FLEET:
                contexts = {CatalogContext.FLEET_DISPATCH}
        if context not in contexts:
            continue
        consumer = f"skill:{skill.name}"
        for tool, line in _skill_tool_operations(skill.canonical_content):
            if tool in callable_tools:
                continue
            key = (consumer, tool, context)
            violations.add(key)
            if key not in EXEMPTIONS:
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
                if key not in EXEMPTIONS:
                    unexpected.append(_violation(consumer, step.tool, line, context, catalog))

    _assert_contract(
        unexpected,
        violations,
        consumer_prefix="recipe:",
        contexts={CatalogContext.INTERACTIVE_ORCHESTRATOR, CatalogContext.FOOD_TRUCK},
    )
