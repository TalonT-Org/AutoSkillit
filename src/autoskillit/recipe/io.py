"""Recipe I/O and parsing — load, list, and parse recipe YAML files."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from autoskillit.core import (
    CORE_PACKS,
    DispatchGateType,
    LoadReport,
    LoadResult,
    RecipeSource,
    get_logger,
    load_yaml,
    pkg_root,
)
from autoskillit.recipe.order import BUNDLED_RECIPE_ORDER
from autoskillit.recipe.schema import (
    AUTOSKILLIT_VERSION_KEY,
    RECIPE_VERSION_KEY,
    CampaignDispatch,
    Recipe,
    RecipeInfo,
    RecipeIngredient,
    RecipeKind,
    RecipeStep,
    StepResultCondition,
    StepResultRoute,
)

logger = get_logger(__name__)


_TEMP_PLACEHOLDER = "{{AUTOSKILLIT_TEMP}}"


def substitute_temp_placeholder(text: str, temp_dir_relpath: str) -> str:
    """Replace ``{{AUTOSKILLIT_TEMP}}`` in raw recipe/skill text.

    Validates that ``temp_dir_relpath`` is YAML-safe (no newlines or
    ``": "`` sequences); raises ``ValueError`` otherwise. Filesystem paths
    should never contain these characters, but the guard makes the failure
    loud and free.
    """
    if "\n" in temp_dir_relpath or ": " in temp_dir_relpath:
        raise ValueError(f"temp_dir_relpath is YAML-unsafe: {temp_dir_relpath!r}")
    return text.replace(_TEMP_PLACEHOLDER, temp_dir_relpath)


def load_recipe(path: Path, temp_dir_relpath: str = ".autoskillit/temp") -> Recipe:
    """Parse a YAML recipe file into a Recipe dataclass.

    Substitutes ``{{AUTOSKILLIT_TEMP}}`` in the raw text with
    ``temp_dir_relpath`` *before* YAML parsing so the resulting Recipe
    dataclass observes the resolved value uniformly.

    After parsing, ``recipe.blocks`` is populated via ``extract_blocks`` from
    ``_analysis.py``.  The import is deferred to break the circular dependency:
    ``_analysis.py`` imports ``iter_steps_with_context`` from this module, so a
    top-level import here would create a cycle.
    """
    raw_text = path.read_text(encoding="utf-8")
    substituted = substitute_temp_placeholder(raw_text, temp_dir_relpath)
    data = load_yaml(substituted)
    if not isinstance(data, dict):
        raise ValueError(f"Recipe file must contain a YAML mapping: {path}")
    recipe = _parse_recipe(data)
    from autoskillit.recipe.staleness_cache import compute_recipe_hash  # noqa: PLC0415

    recipe.content_hash = compute_recipe_hash(path)
    # Deferred import breaks the circular dependency with _analysis.py.
    from autoskillit.recipe._analysis import _build_step_graph, extract_blocks  # noqa: PLC0415

    recipe.blocks = extract_blocks(recipe, _build_step_graph(recipe))
    return recipe


GROUP_LABELS: dict[int, str] = {
    0: "Bundled Recipes",
    1: "Bundled Add-ons",
    2: "Family Recipes",
    3: "Experimental",
}


def group_rank(r: RecipeInfo) -> int:
    if r.experimental:
        return 3
    if r.source == RecipeSource.PROJECT:
        return 2
    if r.requires_packs and all(p in CORE_PACKS for p in r.requires_packs):
        return 0
    return 1


def _registry_position(r: RecipeInfo) -> int:
    """Return sort position within Group 0; 0 (no-op) for all other groups."""
    if group_rank(r) == 0:
        try:
            return BUNDLED_RECIPE_ORDER.index(r.name)
        except ValueError:
            return len(BUNDLED_RECIPE_ORDER)
    return 0


def list_recipes(
    project_dir: Path,
    exclude_kinds: frozenset[RecipeKind] = frozenset(),
) -> LoadResult[RecipeInfo]:
    """Find available recipes from project and built-in sources."""
    seen: set[str] = set()
    items: list[RecipeInfo] = []
    errors: list[LoadReport] = []

    project_recipe_dir = project_dir / ".autoskillit" / "recipes"
    _collect_recipes(RecipeSource.PROJECT, project_recipe_dir, seen, items, errors)

    builtin_dir = pkg_root() / "recipes"
    _collect_recipes(RecipeSource.BUILTIN, builtin_dir, seen, items, errors)

    filtered = [r for r in items if r.kind not in exclude_kinds] if exclude_kinds else items
    return LoadResult(
        items=sorted(filtered, key=lambda r: (group_rank(r), _registry_position(r), r.name)),
        errors=errors,
    )


def builtin_recipes_dir() -> Path:
    """Return the path to the built-in recipes directory."""
    return pkg_root() / "recipes"


def builtin_sub_recipes_dir() -> Path:
    """Return the path to the built-in sub-recipes directory."""
    return pkg_root() / "recipes" / "sub-recipes"


def find_sub_recipe_by_name(name: str, project_dir: Path) -> Path | None:
    """Find a sub-recipe YAML file by name.

    Searches project-local sub-recipes first (project takes precedence),
    then falls back to built-in sub-recipes.
    Sub-recipe files are stored in {dir}/sub-recipes/{name}.yaml and are
    NOT listed by list_recipes() (they are implementation details, not
    user-facing recipes).
    """
    project_sub_dir = project_dir / ".autoskillit" / "recipes" / "sub-recipes"
    for directory in [project_sub_dir, builtin_sub_recipes_dir()]:
        candidate = directory / f"{name}.yaml"
        if candidate.is_file():
            return candidate
    return None


def iter_steps_with_context(
    recipe: Recipe,
) -> Iterator[tuple[str, RecipeStep, frozenset[str]]]:
    """Yield (name, step, available_context) with accumulated captures."""
    available: set[str] = set()
    for step_name, step in recipe.steps.items():
        yield step_name, step, frozenset(available)
        if step.capture:
            available.update(step.capture.keys())
        if step.capture_list:
            available.update(step.capture_list.keys())


def find_recipe_by_name(name: str, project_dir: Path) -> RecipeInfo | None:
    """Find a recipe by name from project and built-in sources.

    Returns the first match (project takes precedence), or None if not found.
    """
    result = list_recipes(project_dir)
    return next((r for r in result.items if r.name == name), None)


def list_campaign_recipes(project_dir: Path) -> LoadResult[RecipeInfo]:
    """Find available campaign recipes from project and built-in sources."""
    seen: set[str] = set()
    items: list[RecipeInfo] = []
    errors: list[LoadReport] = []

    project_campaigns_dir = project_dir / ".autoskillit" / "recipes" / "campaigns"
    _collect_recipes(RecipeSource.PROJECT, project_campaigns_dir, seen, items, errors)

    builtin_campaigns_dir = pkg_root() / "recipes" / "campaigns"
    _collect_recipes(RecipeSource.BUILTIN, builtin_campaigns_dir, seen, items, errors)

    return LoadResult(
        items=sorted(items, key=lambda r: (r.source != RecipeSource.BUILTIN, r.name)),
        errors=errors,
    )


def find_campaign_by_name(name: str, project_dir: Path) -> RecipeInfo | None:
    """Find a campaign recipe by name.

    Returns the first match (project takes precedence), or None if not found.
    """
    result = list_campaign_recipes(project_dir)
    return next((r for r in result.items if r.name == name), None)


def load_campaign_recipes_in_packs(
    packs: frozenset[str],
    project_dir: Path,
    allowed_recipe_names: frozenset[str] = frozenset(),
) -> list[Recipe]:
    """Return all campaign recipes whose categories overlap with the requested packs.

    Recipes explicitly named in ``allowed_recipe_names`` are also included regardless
    of category membership, allowing callers to honour the requesting campaign's
    ``allowed_recipes`` list.
    """
    result = list_campaign_recipes(project_dir)
    matching: list[Recipe] = []
    for info in result.items:
        try:
            recipe = load_recipe(info.path)
        except Exception:
            logger.warning("Failed to load campaign recipe", path=str(info.path))
            continue
        if (set(recipe.categories) & packs) or (info.name in allowed_recipe_names):
            matching.append(recipe)
    return matching


# --- internal helpers ---

# Fields explicitly handled by _parse_step. Must match RecipeStep.__dataclass_fields__
# exactly. When a field is added to RecipeStep, add handling in _parse_step AND add the
# field name here — the assertion below will fail at import time otherwise.
_PARSE_STEP_HANDLED_FIELDS: frozenset[str] = frozenset(
    {
        "name",  # Set from YAML dict key in _parse_recipe, not from step data
        "tool",
        "action",
        "python",
        "constant",
        "with_args",
        "on_success",
        "on_failure",
        "on_context_limit",
        "on_result",
        "retries",
        "on_exhausted",
        "message",
        "note",
        "capture",
        "capture_list",
        "optional",
        "skip_when_false",
        "model",
        "provider",
        "description",
        "sub_recipe",
        "gate",
        "optional_context_refs",
        "stale_threshold",
        "idle_output_timeout",
        "block",  # Named block anchor; maps to step's block: key in YAML
    }
)
if _PARSE_STEP_HANDLED_FIELDS != frozenset(RecipeStep.__dataclass_fields__):
    raise RuntimeError(
        "_parse_step field list is out of sync with RecipeStep schema.\n"
        f"  Missing from handled: "
        f"{frozenset(RecipeStep.__dataclass_fields__) - _PARSE_STEP_HANDLED_FIELDS}\n"
        f"  Extra in handled:     "
        f"{_PARSE_STEP_HANDLED_FIELDS - frozenset(RecipeStep.__dataclass_fields__)}"
    )


def _parse_recipe(data: dict[str, Any]) -> Recipe:
    name = data.get("name", "")
    description = data.get("description", "")
    summary = data.get("summary", "")

    ingredients: dict[str, RecipeIngredient] = {}
    for inp_name, inp_data in (data.get("ingredients") or {}).items():
        if isinstance(inp_data, dict):
            ingredients[inp_name] = RecipeIngredient(
                description=inp_data.get("description", ""),
                required=inp_data.get("required", False),
                default=inp_data.get("default"),
                hidden=bool(inp_data.get("hidden", False)),
            )

    steps: dict[str, RecipeStep] = {}
    for step_name, step_data in (data.get("steps") or {}).items():
        if isinstance(step_data, dict):
            step = _parse_step(step_data)
            step.name = step_name
            steps[step_name] = step

    kitchen_rules = data.get("kitchen_rules", [])
    if not isinstance(kitchen_rules, list):
        raise ValueError(f"'kitchen_rules' must be a list, got {type(kitchen_rules).__name__!r}")

    requires_packs_raw = data.get("requires_packs") or []
    if not isinstance(requires_packs_raw, list):
        raise ValueError(
            f"'requires_packs' must be a list, got {type(requires_packs_raw).__name__!r}"
        )

    _rv = data.get(RECIPE_VERSION_KEY)
    if _rv is not None and not isinstance(_rv, str):
        raise ValueError(
            f"recipe_version must be a quoted string in YAML, got {type(_rv).__name__}: {_rv!r}. "
            f"Use recipe_version: '{_rv}' (with quotes) in your recipe file."
        )

    kind_raw = data.get("kind", "standard")
    try:
        kind = RecipeKind(kind_raw)
    except ValueError:
        kind = RecipeKind.STANDARD

    dispatches_raw = data.get("dispatches") or []
    dispatches = []
    for d in dispatches_raw:
        if isinstance(d, dict):
            d_name = d.get("name", "")
            _raw_gate = d.get("gate") or None
            try:
                d_gate: DispatchGateType | None = (
                    DispatchGateType(_raw_gate) if _raw_gate else None
                )
            except ValueError:
                d_gate = _raw_gate  # type: ignore[assignment]  # Invalid; caught by validate_recipe
            d_recipe = d.get("recipe", "")
            if not d_name:
                raise ValueError(f"Campaign dispatch is missing required 'name' field: {d!r}")
            if d_gate and d_recipe:
                raise ValueError(
                    f"Campaign dispatch {d_name!r} has both 'gate' and 'recipe' set. "
                    "A dispatch must be either a gate dispatch (gate only) or a recipe "
                    "dispatch (recipe only), not both."
                )
            if not d_gate and not d_recipe:
                raise ValueError(
                    f"Campaign dispatch is missing required 'recipe' field "
                    f"(required when 'gate' is not set): {d!r}"
                )
            dispatches.append(
                CampaignDispatch(
                    name=d_name,
                    recipe=d_recipe,
                    task=d.get("task", ""),
                    ingredients=d.get("ingredients") or {},
                    depends_on=d.get("depends_on") or [],
                    capture=d.get("capture") or {},
                    gate=d_gate,
                    message=d.get("message") or None,
                )
            )

    categories = data.get("categories") or []
    requires_recipe_packs = data.get("requires_recipe_packs") or []
    allowed_recipes = data.get("allowed_recipes") or []
    continue_on_failure = bool(data.get("continue_on_failure", False))

    return Recipe(
        name=name,
        description=description,
        summary=summary,
        ingredients=ingredients,
        steps=steps,
        kitchen_rules=kitchen_rules,
        version=data.get(AUTOSKILLIT_VERSION_KEY),
        recipe_version=_rv,
        experimental=bool(data.get("experimental", False)),
        requires_packs=requires_packs_raw,
        kind=kind,
        dispatches=dispatches,
        categories=categories,
        requires_recipe_packs=requires_recipe_packs,
        allowed_recipes=allowed_recipes,
        continue_on_failure=continue_on_failure,
    )


def _parse_step(data: dict[str, Any]) -> RecipeStep:
    if "retry" in data:
        raise ValueError(
            "The 'retry:' block is no longer supported. "
            "Use flat step-level fields: 'retries', 'on_exhausted', 'on_context_limit'."
        )

    on_result = None
    on_result_data = data.get("on_result")
    if isinstance(on_result_data, dict):
        on_result = StepResultRoute(
            field=on_result_data.get("field", ""),
            routes=on_result_data.get("routes", {}),
        )
    elif isinstance(on_result_data, list):
        conditions = []
        for item in on_result_data:
            if isinstance(item, dict):
                conditions.append(
                    StepResultCondition(
                        when=item.get("when"),
                        route=item.get("route", ""),
                    )
                )
        if conditions:
            on_result = StepResultRoute(conditions=conditions)

    return RecipeStep(
        tool=data.get("tool"),
        action=data.get("action"),
        python=data.get("python"),
        constant=data.get("constant"),
        with_args=data.get("with", {}),
        on_success=data.get("on_success"),
        on_failure=data.get("on_failure"),
        on_context_limit=data.get("on_context_limit"),
        on_result=on_result,
        retries=data.get("retries", 3),
        on_exhausted=data.get("on_exhausted", "escalate"),
        message=data.get("message"),
        note=data.get("note"),
        capture=data.get("capture", {}),
        capture_list=data.get("capture_list", {}),
        optional=bool(data.get("optional", False)),
        skip_when_false=data.get("skip_when_false"),
        model=data.get("model"),
        provider=data.get("provider"),
        description=data.get("description", ""),
        sub_recipe=data.get("sub_recipe"),
        gate=data.get("gate"),
        optional_context_refs=data.get("optional_context_refs", []),
        stale_threshold=data.get("stale_threshold"),
        idle_output_timeout=data.get("idle_output_timeout"),
        block=data.get("block"),
    )


def _collect_recipes(
    source: RecipeSource,
    directory: Path,
    seen: set[str],
    result: list[RecipeInfo],
    errors: list[LoadReport],
) -> None:
    if not directory.is_dir():
        return
    for f in sorted(directory.iterdir()):
        if f.suffix in (".yaml", ".yml") and f.is_file():
            try:
                raw = f.read_text(encoding="utf-8")
                data = load_yaml(raw)
                if not isinstance(data, dict):
                    raise ValueError("recipe must be a YAML mapping")
                recipe = _parse_recipe(data)
                if recipe.name and recipe.name not in seen:
                    seen.add(recipe.name)
                    from autoskillit.recipe.staleness_cache import (  # noqa: PLC0415
                        compute_recipe_hash as _crh,
                    )

                    result.append(
                        RecipeInfo(
                            name=recipe.name,
                            description=recipe.description,
                            source=source,
                            path=f,
                            summary=recipe.summary,
                            version=recipe.version,
                            recipe_version=recipe.recipe_version,
                            content_hash=_crh(f),
                            content=raw,
                            kind=recipe.kind,
                            experimental=recipe.experimental,
                            requires_packs=recipe.requires_packs,
                        )
                    )
            except Exception as exc:
                logger.warning("Failed to load recipe file", path=str(f), error=str(exc))
                errors.append(LoadReport(path=f, error=str(exc)))
