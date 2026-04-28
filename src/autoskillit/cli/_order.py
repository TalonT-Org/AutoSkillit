"""Order command and helpers extracted from app.py."""

from __future__ import annotations

import os
import random
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    RecipeSource,
    atomic_write,
    pkg_root,
    resume_spec_from_cli,
)

if TYPE_CHECKING:
    from autoskillit.recipe import RecipeInfo

_UUID_RE = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE)


def _recipes_dir_for(info: RecipeInfo) -> Path:
    if getattr(info, "source", None) == RecipeSource.BUILTIN:
        return pkg_root() / "recipes"
    return Path.cwd() / ".autoskillit" / "recipes"


def _get_subsets_needed(recipe, disabled_subsets: frozenset[str]) -> frozenset[str]:
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


def _get_packs_needed(recipe, default_disabled_packs: frozenset[str]) -> frozenset[str]:
    """Return pack names from default_disabled_packs that are required by recipe."""
    requires = frozenset(getattr(recipe, "requires_packs", []))
    return requires & default_disabled_packs


def _enable_packs_permanently(project_dir: Path, packs: frozenset[str]) -> None:
    """Add specified packs to packs.enabled in .autoskillit/config.yaml."""
    from autoskillit.core import YAMLError, dump_yaml_str, load_yaml

    config_path = project_dir / ".autoskillit" / "config.yaml"
    try:
        data: dict = (load_yaml(config_path) or {}) if config_path.exists() else {}
    except YAMLError:
        data = {}
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
    except YAMLError:
        data = {}
    subsets_section = data.setdefault("subsets", {})
    current_disabled: list[str] = subsets_section.get("disabled", [])
    subsets_section["disabled"] = [s for s in current_disabled if s not in subsets]
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(config_path, dump_yaml_str(data, default_flow_style=False, allow_unicode=True))
    print(f"Updated {config_path}: removed {sorted(subsets)} from subsets.disabled")


def order(recipe: str | None = None, session_id: str | None = None, *, resume: bool = False):
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
    from autoskillit.cli._mcp_names import detect_autoskillit_mcp_prefix
    from autoskillit.cli._prompts import _build_orchestrator_prompt, _get_ingredients_table
    from autoskillit.cli._session_launch import _launch_cook_session, _write_order_entry
    from autoskillit.recipe import (
        find_recipe_by_name,
        list_recipes,
        load_recipe,
        validate_recipe,
    )

    if os.environ.get("CLAUDECODE"):
        print("ERROR: 'order' cannot run inside a Claude Code session.")
        print("Run this command in a regular terminal.")
        sys.exit(1)
    _resume = resume or (session_id is not None)
    resume_spec = resume_spec_from_cli(resume=_resume, session_id=session_id)

    if _resume and recipe is not None and session_id is None and _UUID_RE.match(recipe):
        session_id = recipe
        recipe = None
        resume_spec = resume_spec_from_cli(resume=True, session_id=session_id)

    if _resume and recipe is None:
        from autoskillit.cli._prompts import _OPEN_KITCHEN_GREETINGS
        from autoskillit.cli._session_picker import pick_session as _pick_session
        from autoskillit.core import BareResume, NamedResume, NoResume

        if isinstance(resume_spec, BareResume):
            _sel = _pick_session("order", Path.cwd())
            resume_spec = NamedResume(session_id=_sel) if _sel else NoResume()
        _launch_cook_session(
            "",
            initial_message=random.choice(_OPEN_KITCHEN_GREETINGS),
            resume_spec=resume_spec,
            extra_env=_write_order_entry(Path.cwd(), None),
        )
        return

    mcp_prefix = detect_autoskillit_mcp_prefix()

    from autoskillit.cli._timed_input import timed_prompt

    if recipe is None:
        from autoskillit.cli._prompts import (
            _OPEN_KITCHEN_CHOICE,
            _build_open_kitchen_prompt,
            _resolve_recipe_input,
        )
        from autoskillit.recipe import GROUP_LABELS, group_rank

        available = list_recipes(Path.cwd()).items
        if not available:
            print("No recipes found. Run 'autoskillit recipes list' to check.")
            sys.exit(1)

        print("Available recipes:")
        print("  0. Open kitchen (no recipe)")
        current_rank: int = -1
        for i, r in enumerate(available, 1):
            rank = group_rank(r)
            if rank != current_rank:
                current_rank = rank
                print(f"\n  {GROUP_LABELS.get(rank, str(rank))}")
            print(f"  {i}. {r.name}")
        raw = timed_prompt(
            f"Select recipe [0-{len(available)}]:",
            default="",
            timeout=120,
            label="autoskillit order",
        )
        resolved = _resolve_recipe_input(raw, available)
        if resolved is _OPEN_KITCHEN_CHOICE:
            from autoskillit.cli._prompts import _OPEN_KITCHEN_GREETINGS

            _launch_cook_session(
                _build_open_kitchen_prompt(mcp_prefix=mcp_prefix),
                initial_message=random.choice(_OPEN_KITCHEN_GREETINGS),
                resume_spec=resume_spec,
                project_dir=Path.cwd(),
                extra_env=_write_order_entry(Path.cwd(), None),
            )
            return
        elif resolved is None:
            print(f"Invalid selection: '{raw}'")
            sys.exit(1)
        else:
            if isinstance(resolved, str):
                raise TypeError(f"Expected RecipeInfo, got str: {resolved!r}")
            recipe = resolved.name

    from autoskillit.core import YAMLError

    _match = find_recipe_by_name(recipe, Path.cwd())
    if _match is None:
        available = list_recipes(Path.cwd()).items
        print(f"Recipe not found: '{recipe}'")
        if available:
            print("Available recipes:")
            for r in available:
                print(f"  - {r.name}")
        else:
            print("No recipes found")
        sys.exit(1)
    # Validate recipe before launching session
    try:
        parsed = load_recipe(_match.path)
    except YAMLError as exc:
        print(f"Recipe YAML parse error: {exc}")
        sys.exit(1)
    except ValueError as exc:
        print(f"Recipe structure error: {exc}")
        sys.exit(1)

    errors = validate_recipe(parsed)
    if errors:
        print(f"Recipe '{recipe}' failed validation:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    # Subset-disabled gate (REQ-VAL-004)
    from autoskillit.config import load_config as _load_config

    _cfg = _load_config(Path.cwd())
    _disabled = frozenset(_cfg.subsets.disabled)
    _extra_env: dict[str, str] = {}

    if _disabled:
        _needed = _get_subsets_needed(parsed, _disabled)
        if _needed:
            subset_list = ", ".join(sorted(_needed))
            print(f"\nThis recipe requires subset(s): {subset_list}")
            print("  1. Enable temporarily (for this run only)")
            print("  2. Enable permanently (update .autoskillit/config.yaml)")
            print("  3. Cancel")
            _choice = timed_prompt(
                "Choose [1/2/3]:", default="3", timeout=120, label="autoskillit order"
            )
            if _choice == "1":
                _extra_env["AUTOSKILLIT_SUBSETS__DISABLED"] = "@json []"
            elif _choice == "2":
                _enable_subsets_permanently(Path.cwd(), _needed)
            else:
                return

    # Pack gate — check default-disabled packs (REQ-PACK-010)
    from autoskillit.core import PACK_REGISTRY as _PACK_REGISTRY

    _default_disabled = frozenset(
        tag for tag, pack_def in _PACK_REGISTRY.items() if not pack_def.default_enabled
    )
    _pack_enabled = frozenset(_cfg.packs.enabled)
    _default_disabled_packs = _default_disabled - _pack_enabled

    if _default_disabled_packs:
        _packs_needed = _get_packs_needed(parsed, _default_disabled_packs)
        if _packs_needed:
            pack_list = ", ".join(sorted(_packs_needed))
            print(f"\nThis recipe requires pack(s): {pack_list}")
            print("  1. Enable temporarily (for this run only)")
            print("  2. Enable permanently (update .autoskillit/config.yaml)")
            print("  3. Cancel")
            _pack_choice = timed_prompt(
                "Choose [1/2/3]:", default="3", timeout=120, label="autoskillit order"
            )
            if _pack_choice == "1":
                import json as _json

                _extra_env["AUTOSKILLIT_PACKS__ENABLED"] = "@json " + _json.dumps(
                    sorted(_packs_needed)
                )
            elif _pack_choice == "2":
                _enable_packs_permanently(Path.cwd(), _packs_needed)
            else:
                return

    from autoskillit.cli._prompts import _COOK_GREETINGS, show_cook_preview

    _itable = _get_ingredients_table(recipe, _match, Path.cwd())
    show_cook_preview(recipe, parsed, _recipes_dir_for(_match), Path.cwd())

    from autoskillit.cli._ansi import permissions_warning

    print(permissions_warning())
    confirm = timed_prompt(
        "Launch session? [Enter/n]", default="", timeout=120, label="autoskillit order"
    )
    if confirm.lower() in ("n", "no"):
        return
    greeting = random.choice(_COOK_GREETINGS).format(recipe_name=recipe)
    _extra_env |= _write_order_entry(Path.cwd(), recipe)
    _launch_cook_session(
        _build_orchestrator_prompt(recipe, mcp_prefix=mcp_prefix, ingredients_table=_itable),
        initial_message=greeting,
        extra_env=_extra_env,
        resume_spec=resume_spec,
        project_dir=Path.cwd(),
    )
