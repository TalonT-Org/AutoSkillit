"""Order command and helpers extracted from app.py."""

from __future__ import annotations

import json
import os
import random
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never

import regex as re

from autoskillit.cli.prompts import (
    _build_orchestrator_prompt,
    _get_ingredients_table,
)
from autoskillit.cli.session._session_launch import (
    _launch_cook_session,
    _order_launch_env,
    _write_order_entry,
    render_skill_catalog_exclusions,
    render_skill_contract_composition_failure,
)
from autoskillit.core import (
    ORDER_INTERACTIVE_REQUIRED_ENV,
    FreshLaunch,
    RecipeSource,
    RestoreSession,
    ResumeWithBriefing,
    SkillContractError,
    SkillExecutionRole,
    atomic_write,
    claim_launch_for_session,
    detect_autoskillit_mcp_prefix,
    get_logger,
    pkg_root,
    release_session_claim,
    resume_spec_from_cli,
)
from autoskillit.workspace import (
    DefaultSkillResolver,
    compile_session_skill_catalog,
    validate_skill_tier_roles,
)

if TYPE_CHECKING:
    from autoskillit.recipe import Recipe, RecipeInfo

logger = get_logger(__name__)

_UUID_RE = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE)


def _recipes_dir_for(info: RecipeInfo) -> Path:
    if getattr(info, "source", None) == RecipeSource.BUILTIN:
        return pkg_root() / "recipes"
    return Path.cwd() / ".autoskillit" / "recipes"


def _get_subsets_needed(recipe: Recipe, disabled_subsets: frozenset[str]) -> frozenset[str]:
    """Return the subset names from disabled_subsets that are actually referenced in recipe."""
    from autoskillit.recipe import make_validation_context, run_semantic_rules

    ctx = make_validation_context(recipe, disabled_subsets=disabled_subsets)
    findings = run_semantic_rules(ctx)
    needed: set[str] = set()
    for f in findings:
        if f.rule not in ("subset-disabled-skill", "subset-disabled-tool"):
            continue
        m = re.search(r"disabled subset '([^']+)'", f.message)
        if m:
            needed.add(m.group(1))
    return frozenset(needed)


def _get_packs_needed(recipe: Recipe, default_disabled_packs: frozenset[str]) -> frozenset[str]:
    """Return pack names from default_disabled_packs that are required by recipe."""
    requires = frozenset(getattr(recipe, "requires_packs", []))
    return requires & default_disabled_packs


def _enable_packs_permanently(project_dir: Path, packs: frozenset[str]) -> None:
    """Add specified packs to packs.enabled in .autoskillit/config.yaml."""
    from autoskillit.core import YAMLError, dump_yaml_str, load_yaml

    config_path = project_dir / ".autoskillit" / "config.yaml"
    try:
        data: dict = (load_yaml(config_path) or {}) if config_path.exists() else {}
    except YAMLError as exc:
        logger.warning("corrupt_config_yaml", path=str(config_path), error=str(exc))
        msg = (
            f"Cannot update {config_path}: file has YAML syntax errors."
            " Fix the syntax or delete the file to reset."
        )
        raise SystemExit(msg) from exc
    packs_section = data.setdefault("packs", {})
    current_enabled: list[str] = packs_section.get("enabled", [])
    new_enabled = sorted(set(current_enabled) | packs)
    packs_section["enabled"] = new_enabled
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(config_path, dump_yaml_str(data, default_flow_style=False, allow_unicode=True))
    print(f"Updated {config_path}: added {sorted(packs)} to packs.enabled")


def _enable_subsets_permanently(project_dir: Path, subsets: frozenset[str]) -> None:
    """Remove specified subsets from subsets.disabled in .autoskillit/config.yaml."""
    from autoskillit.core import YAMLError, dump_yaml_str, load_yaml

    config_path = project_dir / ".autoskillit" / "config.yaml"
    try:
        data: dict = (load_yaml(config_path) or {}) if config_path.exists() else {}
    except YAMLError as exc:
        logger.warning("corrupt_config_yaml", path=str(config_path), error=str(exc))
        msg = (
            f"Cannot update {config_path}: file has YAML syntax errors."
            " Fix the syntax or delete the file to reset."
        )
        raise SystemExit(msg) from exc
    subsets_section = data.setdefault("subsets", {})
    current_disabled: list[str] = subsets_section.get("disabled", [])
    subsets_section["disabled"] = [s for s in current_disabled if s not in subsets]
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(config_path, dump_yaml_str(data, default_flow_style=False, allow_unicode=True))
    print(f"Updated {config_path}: removed {sorted(subsets)} from subsets.disabled")


def _resolve_order_recipe(recipe_name: str, project_dir: Path) -> tuple[RecipeInfo, Recipe]:
    from autoskillit.core import YAMLError
    from autoskillit.recipe import (
        NON_INTERACTIVE_KINDS,
        find_recipe_by_name,
        list_recipes,
        load_recipe,
        validate_recipe_structure,
    )

    match = find_recipe_by_name(recipe_name, project_dir)
    if match is None:
        available = list_recipes(project_dir, exclude_kinds=NON_INTERACTIVE_KINDS).items
        print(f"Recipe not found: '{recipe_name}'")
        if available:
            print("Available recipes:")
            for candidate in available:
                print(f"  - {candidate.name}")
        else:
            print("No recipes found")
        sys.exit(1)

    try:
        parsed = load_recipe(match.path)
    except YAMLError as exc:
        print(f"Recipe YAML parse error: {exc}")
        sys.exit(1)
    except ValueError as exc:
        print(f"Recipe structure error: {exc}")
        sys.exit(1)

    errors = validate_recipe_structure(parsed)
    if errors:
        print(f"Recipe '{recipe_name}' failed validation:")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)
    return match, parsed


def _prompt_feature_enablement(
    *,
    feature_kind: str,
    requirement_list: str,
    label: str,
    timeout: int = 120,
) -> Literal["temporary", "permanent"] | None:
    """Prompt the user with the 1/2/3 menu used for subset and pack enablement.

    Returns ``"temporary"`` or ``"permanent"`` for option 1/2, or ``None`` on cancel.
    Shared by the subset gate (REQ-VAL-004) and the pack gate (REQ-PACK-010).
    """
    from autoskillit.cli.ui._timed_input import timed_prompt

    print(f"\nThis recipe requires {feature_kind}(s): {requirement_list}")
    print("  1. Enable temporarily (for this run only)")
    print("  2. Enable permanently (update .autoskillit/config.yaml)")
    print("  3. Cancel")
    choice = timed_prompt("Choose [1/2/3]:", default="3", timeout=timeout, label=label)
    if choice == "1":
        return "temporary"
    if choice == "2":
        return "permanent"
    return None


def _derive_order_feature_env(
    recipe: Recipe,
    *,
    automatic: bool,
    project_dir: Path,
) -> dict[str, str] | None:
    """Derive order session env overrides for subset/pack feature gates.

    Implements the subset gate (REQ-VAL-004) and the pack gate (REQ-PACK-010).
    """
    from autoskillit.config import load_config
    from autoskillit.core import PACK_REGISTRY

    config = load_config(project_dir)
    extra_env: dict[str, str] = {}

    disabled_subsets = frozenset(config.subsets.disabled)
    needed_subsets = _get_subsets_needed(recipe, disabled_subsets)
    if needed_subsets:
        subset_list = ", ".join(sorted(needed_subsets))
        if automatic:
            extra_env["AUTOSKILLIT_SUBSETS__DISABLED"] = "@json []"
            sys.stdout.write(f"Temporarily enabling required subset(s): {subset_list}\n")
        else:
            choice = _prompt_feature_enablement(
                feature_kind="subset",
                requirement_list=subset_list,
                label="autoskillit order",
            )
            if choice == "temporary":
                extra_env["AUTOSKILLIT_SUBSETS__DISABLED"] = "@json []"
            elif choice == "permanent":
                _enable_subsets_permanently(project_dir, needed_subsets)
            else:
                return None

    default_disabled = frozenset(
        tag for tag, pack_def in PACK_REGISTRY.items() if not pack_def.default_enabled
    )
    enabled_packs = frozenset(config.packs.enabled)
    needed_packs = _get_packs_needed(recipe, default_disabled - enabled_packs)
    if needed_packs:
        pack_list = ", ".join(sorted(needed_packs))
        temporary_packs = sorted(enabled_packs | needed_packs)
        if automatic:
            extra_env["AUTOSKILLIT_PACKS__ENABLED"] = "@json " + json.dumps(temporary_packs)
            sys.stdout.write(f"Temporarily enabling required pack(s): {pack_list}\n")
        else:
            choice = _prompt_feature_enablement(
                feature_kind="pack",
                requirement_list=pack_list,
                label="autoskillit order",
            )
            if choice == "temporary":
                extra_env["AUTOSKILLIT_PACKS__ENABLED"] = "@json " + json.dumps(temporary_packs)
            elif choice == "permanent":
                _enable_packs_permanently(project_dir, needed_packs)
            else:
                return None
    return extra_env


def _show_order_ceremony_preview(
    recipe_name: str,
    recipe: Recipe,
    recipe_info: RecipeInfo,
    project_dir: Path,
) -> None:
    """Render the order-specific preview before the launch confirmation prompt."""
    from autoskillit.cli._preview import show_cook_preview
    from autoskillit.cli.ui._ansi import permissions_warning

    show_cook_preview(recipe_name, recipe, _recipes_dir_for(recipe_info), project_dir)
    print(permissions_warning())


def order(
    recipe: str | None = None, session_id: str | None = None, *, resume: bool = False
) -> None:
    """Launch an interactive Claude Code session to execute a recipe.

    Starts Claude Code with hard tool restrictions: only AskUserQuestion
    (built-in) and AutoSkillit MCP tools are available. The session
    discovers recipe content by calling load_recipe as its first action.

    Parameters
    ----------
    recipe
        Name of the recipe (from .autoskillit/recipes/). Prompts if omitted.
    session_id
        Explicit session ID to resume. Provide after the recipe name.
        Implies --resume when non-None.
    resume
        When True, attempt to restore a previous session.
    """
    from autoskillit.recipe import (
        NON_INTERACTIVE_KINDS,
        list_recipes,
    )

    if os.environ.get("CLAUDECODE"):
        print("ERROR: 'order' cannot run inside a Claude Code session.")
        print("Run this command in a regular terminal.")
        sys.exit(1)
    from autoskillit.config import load_config

    project_dir = Path.cwd()
    config = load_config(project_dir)
    is_tty = sys.stdin.isatty()
    from autoskillit.cli.session._session_backend import resolve_global_backend

    backend = resolve_global_backend(
        config.agent_backend.backend,
        codex_runtime_spec=config.codex_runtime.resolve(),
    )
    backend_caps = backend.capabilities
    mcp_prefix = detect_autoskillit_mcp_prefix(backend_caps)
    skill_resolver = DefaultSkillResolver()
    skill_visibility = config.skill_visibility_spec()
    try:
        validate_skill_tier_roles(skill_visibility, skill_resolver, project_dir)
        skill_catalog = skill_resolver.list_effective(
            project_dir,
            SkillExecutionRole.ORCHESTRATOR,
            visibility=skill_visibility,
        )
    except SkillContractError as exc:
        render_skill_contract_composition_failure(exc)
        raise SystemExit(1) from exc
    render_skill_catalog_exclusions(skill_catalog.exclusions)
    managed_join_context = None
    managed_join_parent_id: str | None = None
    if backend.name == "codex":
        from autoskillit.server._managed_join_prelaunch import (
            ManagedJoinIssuanceRefusal,
            prepare_managed_join_context,
            render_managed_join_refusal,
        )

        managed_join_parent_id = uuid.uuid4().hex[:16]
        issuance = prepare_managed_join_context(
            backend=backend,
            configured_model=config.model.model_override or config.model.default_model,
            state_root=project_dir,
            parent_id=managed_join_parent_id,
            launch_context="interactive",
        )
        if isinstance(issuance, ManagedJoinIssuanceRefusal):
            print(f"WARNING: {render_managed_join_refusal(issuance)}")
            managed_join_parent_id = None
        else:
            managed_join_context = issuance
    skill_compilation = compile_session_skill_catalog(
        skill_catalog,
        backend,
        adaptation_context=managed_join_context,
    )
    _resume = resume or (session_id is not None)
    resume_spec = resume_spec_from_cli(resume=_resume, session_id=session_id)

    if _resume and recipe is not None and session_id is None and _UUID_RE.match(recipe):
        session_id = recipe
        recipe = None
        resume_spec = resume_spec_from_cli(resume=True, session_id=session_id)

    if not _resume and recipe is None:
        from autoskillit.cli.ui._menu import SLOT_ZERO_SELECTED, run_selection_menu
        from autoskillit.recipe import GROUP_LABELS, group_rank

        available = list_recipes(
            Path.cwd(),
            exclude_kinds=NON_INTERACTIVE_KINDS,
        ).items
        if not available:
            print("No recipes found. Run 'autoskillit recipes list' to check.")
            sys.exit(1)

        resolved = run_selection_menu(
            available,
            header="Available recipes:",
            slot_zero_label="Open kitchen (no recipe)",
            group_classifier=group_rank,
            group_labels=GROUP_LABELS,
            name_key=lambda r: r.name,
            timeout=120,
            label="autoskillit order",
        )
        if resolved is None:
            print("Invalid selection.")
            sys.exit(1)
        if resolved is not SLOT_ZERO_SELECTED:
            if isinstance(resolved, str):
                raise TypeError(f"Expected RecipeInfo, got str: {resolved!r}")
            recipe = resolved.name

    from autoskillit.cli.session._session_launch_intent import (
        _run_fresh_launch_ceremony,
        prepare_resume_housekeeping,
        resolve_interactive_launch,
    )
    from autoskillit.core import NoResume

    if not isinstance(resume_spec, NoResume):
        prepare_resume_housekeeping(backend, resume_spec=resume_spec)
    launch = resolve_interactive_launch(
        resume_spec=resume_spec,
        session_type="order",
        project_dir=project_dir,
        backend=backend,
    )
    automatic = not isinstance(launch, FreshLaunch) or not is_tty

    if recipe is None:
        from autoskillit.cli.prompts import _OPEN_KITCHEN_GREETINGS, _build_open_kitchen_prompt

        if isinstance(launch, FreshLaunch):
            launch = replace(
                launch,
                system_prompt=_build_open_kitchen_prompt(
                    mcp_prefix=mcp_prefix,
                    has_unguarded_filesystem_access=backend_caps.has_unguarded_filesystem_access,
                    skill_compilation=skill_compilation,
                    project_root=project_dir,
                    backend=backend,
                ),
                initial_prompt=random.choice(_OPEN_KITCHEN_GREETINGS),
            )
        extra_env: dict[str, str] = {}
    else:
        recipe_info, parsed = _resolve_order_recipe(recipe, project_dir)
        derived_env = _derive_order_feature_env(
            parsed,
            automatic=automatic,
            project_dir=project_dir,
        )
        if derived_env is None:
            return
        extra_env = derived_env
        if not _run_fresh_launch_ceremony(
            launch=launch,
            is_tty=is_tty,
            label="autoskillit order",
            before_prompt=lambda: _show_order_ceremony_preview(
                recipe, parsed, recipe_info, project_dir
            ),
        ):
            return

        if isinstance(launch, FreshLaunch):
            from autoskillit.cli.prompts import _COOK_GREETINGS

            ingredients_table = _get_ingredients_table(recipe, recipe_info, project_dir)
            launch = replace(
                launch,
                system_prompt=_build_orchestrator_prompt(
                    recipe,
                    mcp_prefix=mcp_prefix,
                    ingredients_table=ingredients_table,
                    has_unguarded_filesystem_access=backend_caps.has_unguarded_filesystem_access,
                    skill_compilation=skill_compilation,
                    project_root=project_dir,
                    backend=backend,
                ),
                initial_prompt=random.choice(_COOK_GREETINGS).format(recipe_name=recipe),
            )

    claimed_launch_id: str | None = None
    match launch:
        case FreshLaunch():
            launch_id, launch_env = _write_order_entry(
                project_dir,
                recipe,
                managed_join_parent_id,
            )
            claimed_launch_id = launch_id
        case (
            RestoreSession(session_id=claude_session_id)
            | ResumeWithBriefing(session_id=claude_session_id)
        ):
            from autoskillit.cli.session._session_constants import SESSION_TYPE_ORDER

            launch_id = claim_launch_for_session(
                project_dir,
                claude_session_id=claude_session_id,
                session_type=SESSION_TYPE_ORDER,
                recipe_name=recipe,
            )
            claimed_launch_id = launch_id
            launch_env = _order_launch_env(launch_id, managed_join_parent_id)
        case _:
            assert_never(launch)

    launch_extra_env = {**extra_env, **launch_env}
    try:
        _launch_cook_session(
            launch=launch,
            extra_env=launch_extra_env,
            project_dir=project_dir,
            required_env=ORDER_INTERACTIVE_REQUIRED_ENV,
            backend=backend,
            skill_compilation=skill_compilation,
            launch_id=launch_id,
            default_base_branch=config.branching.default_base_branch,
            workspace_temp_dir=config.workspace.temp_dir,
            force_inactive_agent_teams=config.agent_backend.force_inactive_agent_teams,
            mcp_tool_timeout_sec=config.run_skill.mcp_tool_timeout_sec,
            adaptation_context=managed_join_context,
        )
    finally:
        if claimed_launch_id is not None:
            release_session_claim(project_dir, claimed_launch_id)
