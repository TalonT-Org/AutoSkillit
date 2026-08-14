"""Deterministic grammar-aware pagination for persisted recipe sections."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import Event, Lock, RLock
from typing import Any

from autoskillit.core import (
    DYNAMIC_RECIPE_SECTION_DEF,
    RECIPE_ARTIFACT_MAX_BLOB_BYTES,
    RECIPE_SECTION_PAGINATION_POLICY_DIGEST,
    RECIPE_SECTION_PAGINATION_VERSION,
    RECIPE_SECTION_REGISTRY,
    RECIPE_SECTION_REGISTRY_DIGEST,
    RecipeArtifactGeneration,
    RecipeSectionDef,
    canonical_recipe_section_json,
    fast_dumps,
    get_logger,
    load_yaml,
    recipe_section_plan_digest,
    resolve_recipe_section_response_bound,
)
from autoskillit.server._recipe_section_planning import (
    _render_candidate,
    plan_pages,
    recipe_section_continuation_binding,
    selected_section_sha256,
)
from autoskillit.server.recipe_section._contracts import (
    PlannedRecipeSectionPage,
    RecipeSectionBoundError,
    RecipeSectionNonConvergenceError,
    RecipeSectionPageDescriptor,
    RecipeSectionPagePlan,
    RecipeSectionPaginationError,
    RecipeSectionPlanManifest,
    RecipeSectionRequestState,
    SelectedRecipeSection,
)
from autoskillit.server.recipe_section._lifecycle import register_kitchen_retirement_callback
from autoskillit.server.recipe_section._rendering import render_recipe_section_failure
from autoskillit.server.recipe_section._verification import (
    verify_finalized_recipe_section_plan,
)

logger = get_logger(__name__)

PAGE_PLAN_CACHE_MAX_ENTRIES = 8
PAGE_PLAN_CACHE_MAX_BYTES = 32 * 1024 * 1024


def _convergence_iteration_ceiling() -> int:
    """Derive the monotone width-growth ceiling from artifact policy."""
    max_count_digits = len(str(RECIPE_ARTIFACT_MAX_BLOB_BYTES))
    # One total width plus at most one fragment width per persisted byte;
    # every nonterminal pass grows at least one width by one digit.
    return 1 + (RECIPE_ARTIFACT_MAX_BLOB_BYTES + 1) * (max_count_digits - 1)


__all__ = [
    "PAGE_PLAN_CACHE_MAX_BYTES",
    "PAGE_PLAN_CACHE_MAX_ENTRIES",
    "PagePlanCache",
    "RecipeSectionBoundError",
    "RecipeSectionNonConvergenceError",
    "RecipeSectionPageDescriptor",
    "RecipeSectionPagePlan",
    "RecipeSectionPaginationError",
    "RecipeSectionPlanManifest",
    "RecipeSectionRequestState",
    "RecipeStepExtractionError",
    "SelectedRecipeSection",
    "build_recipe_section_page_plan",
    "extract_recipe_step_bodies",
    "extract_step_body_from_persisted",
    "evict_kitchen",
    "get_or_build_recipe_section_page_plan",
    "render_recipe_section_failure",
    "render_recipe_section_page",
    "recipe_section_continuation_binding",
    "resolve_recipe_section_definition",
    "resolve_recipe_section_bound_bytes",
    "select_recipe_section",
]


class RecipeStepExtractionError(Exception):
    """A persisted recipe body could not be parsed or serialized."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _persisted_recipe_steps(persisted: dict[str, Any]) -> dict[str, Any]:
    content = persisted.get("content", "") or ""
    if not isinstance(content, str) or not content:
        return {}
    try:
        parsed = load_yaml(content)
    except Exception as exc:
        logger.warning("recipe_step_yaml_parse_failed", exc_info=True)
        raise RecipeStepExtractionError(
            "recipe_artifact_parse_failed", f"{type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise RecipeStepExtractionError(
            "recipe_artifact_parse_failed", "recipe content is not a mapping"
        )
    steps = parsed.get("steps")
    if not isinstance(steps, dict):
        raise RecipeStepExtractionError(
            "recipe_artifact_parse_failed", "recipe steps are not a mapping"
        )
    return steps


def extract_recipe_step_bodies(
    persisted: dict[str, Any],
    ordered_step_names: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    """Extract canonical compact YAML bodies for ordered persisted steps."""
    steps = _persisted_recipe_steps(persisted)
    bodies: list[tuple[str, str]] = []
    for step_name in ordered_step_names:
        step_obj = steps.get(step_name)
        if step_obj is None:
            continue
        if not isinstance(step_obj, dict):
            raise RecipeStepExtractionError(
                "recipe_section_serialization_failed", "recipe step is not a mapping"
            )
        try:
            bodies.append((step_name, fast_dumps({step_name: step_obj})))
        except Exception as exc:
            logger.warning(
                "recipe_step_yaml_serialize_failed",
                step_name=step_name,
                exc_info=True,
            )
            raise RecipeStepExtractionError(
                "recipe_section_serialization_failed", f"{type(exc).__name__}: {exc}"
            ) from exc
    return tuple(bodies)


def extract_step_body_from_persisted(persisted: dict[str, Any], step_name: str) -> str:
    """Extract one compact YAML step body from a persisted recipe artifact."""
    if not step_name:
        return ""
    bodies = extract_recipe_step_bodies(persisted, (step_name,))
    return bodies[0][1] if bodies else ""


@dataclass(frozen=True, slots=True)
class _PagePlanCacheKey:
    kitchen_id: str
    generation: RecipeArtifactGeneration
    initialization_id: str | None
    section: str
    section_sha256: str
    recipe_section_bound_bytes: int
    section_registry_sha256: str
    pagination_policy_sha256: str
    pagination_version: int
    char_ceiling: int | None


@dataclass(slots=True)
class _PagePlanBuildState:
    """One shared in-flight page-plan build for a cache key."""

    completed: Event = field(default_factory=Event)
    plan: RecipeSectionPagePlan | None = None
    error: BaseException | None = None


class PagePlanCache:
    """Bounded thread-safe LRU storing only verified immutable plans."""

    def __init__(
        self,
        *,
        max_entries: int = PAGE_PLAN_CACHE_MAX_ENTRIES,
        max_bytes: int = PAGE_PLAN_CACHE_MAX_BYTES,
    ) -> None:
        if max_entries < 0:
            raise ValueError("page-plan cache max_entries must not be negative")
        if max_bytes < 0:
            raise ValueError("page-plan cache max_bytes must not be negative")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: OrderedDict[_PagePlanCacheKey, RecipeSectionPagePlan] = OrderedDict()
        self._weight_bytes = 0
        self._builds: dict[_PagePlanCacheKey, _PagePlanBuildState] = {}
        self._retired_kitchens: set[str] = set()
        self._lock = RLock()

    def get(self, key: _PagePlanCacheKey) -> RecipeSectionPagePlan | None:
        with self._lock:
            plan = self._entries.get(key)
            if plan is not None:
                self._entries.move_to_end(key)
            return plan

    def _put_locked(self, key: _PagePlanCacheKey, plan: RecipeSectionPagePlan) -> None:
        if plan.cache_weight_bytes > self._max_bytes:
            return
        existing = self._entries.pop(key, None)
        if existing is not None:
            self._weight_bytes -= existing.cache_weight_bytes
        self._entries[key] = plan
        self._weight_bytes += plan.cache_weight_bytes
        while len(self._entries) > self._max_entries or self._weight_bytes > self._max_bytes:
            _, evicted = self._entries.popitem(last=False)
            self._weight_bytes -= evicted.cache_weight_bytes

    def put(self, key: _PagePlanCacheKey, plan: RecipeSectionPagePlan) -> None:
        with self._lock:
            if key.kitchen_id not in self._retired_kitchens:
                self._put_locked(key, plan)

    def get_or_build(
        self,
        key: _PagePlanCacheKey,
        builder: Callable[[], RecipeSectionPagePlan],
    ) -> RecipeSectionPagePlan:
        """Share a key build and admit it only while its kitchen remains active."""
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached
            build_state = self._builds.get(key)
            build_here = build_state is None
            if build_state is None:
                build_state = _PagePlanBuildState()
                self._builds[key] = build_state

        if build_here:
            try:
                plan = builder()
            except BaseException as exc:
                with self._lock:
                    build_state.error = exc
                    self._builds.pop(key, None)
                    build_state.completed.set()
                raise
            with self._lock:
                if key.kitchen_id not in self._retired_kitchens:
                    self._put_locked(key, plan)
                build_state.plan = plan
                self._builds.pop(key, None)
                build_state.completed.set()
            return plan

        build_state.completed.wait()
        if build_state.error is not None:
            raise build_state.error
        if build_state.plan is None:
            raise RuntimeError("page-plan build completed without a plan")
        return build_state.plan

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._weight_bytes = 0
            self._retired_kitchens.clear()

    def evict_kitchen(self, kitchen_id: str) -> None:
        with self._lock:
            self._retired_kitchens.add(kitchen_id)
            keys = [key for key in self._entries if key.kitchen_id == kitchen_id]
            for key in keys:
                self._weight_bytes -= self._entries.pop(key).cache_weight_bytes


_PAGE_PLAN_CACHE: PagePlanCache | None = None
_PAGE_PLAN_CACHE_LOCK = Lock()


def _page_plan_cache() -> PagePlanCache:
    global _PAGE_PLAN_CACHE
    with _PAGE_PLAN_CACHE_LOCK:
        if _PAGE_PLAN_CACHE is None:
            _PAGE_PLAN_CACHE = PagePlanCache()
        return _PAGE_PLAN_CACHE


def evict_kitchen(kitchen_id: str) -> None:
    """Best-effort idempotent eviction for a retired kitchen namespace."""
    try:
        with _PAGE_PLAN_CACHE_LOCK:
            cache = _PAGE_PLAN_CACHE
        if cache is not None:
            cache.evict_kitchen(kitchen_id)
    except Exception:
        logger.warning(
            "recipe_section_cache_eviction_failed", kitchen_id=kitchen_id, exc_info=True
        )


def _evict_retired_kitchen(kitchen_id: str) -> None:
    evict_kitchen(kitchen_id)


register_kitchen_retirement_callback(_evict_retired_kitchen)


def resolve_recipe_section_bound_bytes(
    response_max_bytes: int,
    conservative_general_result_limit: int,
    page_max_bytes_override: int | None = None,
    *,
    exemption_ceiling_bytes: int | None = None,
) -> int:
    """Delegate to core resolver — thin compatibility wrapper."""
    return resolve_recipe_section_response_bound(
        response_max_bytes=response_max_bytes,
        conservative_general_result_limit=conservative_general_result_limit,
        page_max_bytes_override=page_max_bytes_override,
        exemption_ceiling_bytes=exemption_ceiling_bytes,
    )


def resolve_recipe_section_definition(
    payload: Mapping[str, object],
    section: str,
) -> RecipeSectionDef | None:
    """Resolve the sole fixed-or-dynamic definition for a pullable section."""
    definition = RECIPE_SECTION_REGISTRY.get(section)
    if definition is not None:
        return definition
    step_names = payload.get("post_prune_step_names")
    if (
        type(step_names) is list
        and type(section) is str
        and any(type(name) is str and name == section for name in step_names)
    ):
        return DYNAMIC_RECIPE_SECTION_DEF
    return None


def select_recipe_section(
    payload: Mapping[str, object],
    section: str,
    *,
    dynamic_content: str | None = None,
    dynamic_content_loader: Callable[[str], str | None] | None = None,
) -> SelectedRecipeSection:
    """Select a fixed or validated dynamic section without pre-serializing its value."""
    definition = resolve_recipe_section_definition(payload, section)
    if definition is None:
        return SelectedRecipeSection(
            section=section,
            definition=DYNAMIC_RECIPE_SECTION_DEF,
            value=None,
            present=False,
        )
    if definition is not DYNAMIC_RECIPE_SECTION_DEF:
        if section not in payload:
            if definition.missing_behavior == "default":
                return SelectedRecipeSection(
                    section=section,
                    definition=definition,
                    value=list(definition.default_value or ()),
                    present=True,
                )
            return SelectedRecipeSection(section, definition, None, False)
        value = payload[section]
        if value is None and definition.none_behavior == "absent":
            return SelectedRecipeSection(section, definition, None, False)
        return SelectedRecipeSection(section, definition, value, True)

    if dynamic_content_loader is not None:
        dynamic_content = dynamic_content_loader(section)
    return SelectedRecipeSection(
        section=section,
        definition=DYNAMIC_RECIPE_SECTION_DEF,
        value=dynamic_content,
        present=bool(dynamic_content),
    )


def build_recipe_section_page_plan(
    *,
    kitchen_id: str,
    generation: RecipeArtifactGeneration,
    selected: SelectedRecipeSection,
    recipe_section_bound_bytes: int,
    char_ceiling: int | None = None,
) -> RecipeSectionPagePlan:
    """Build, finalize, and verify one deterministic immutable page plan."""
    del kitchen_id  # Identity participates in the cache key, not the manifest.
    if recipe_section_bound_bytes <= 0:
        raise RecipeSectionBoundError("recipe section bound must be positive")
    section_sha256 = selected_section_sha256(selected)
    total_width = 1
    fragment_widths: dict[int, int] = {}
    seen_states: set[tuple[int, tuple[tuple[int, int], ...]]] = set()
    pages: list[PlannedRecipeSectionPage] = []
    for _ in range(_convergence_iteration_ceiling()):
        state = (total_width, tuple(sorted(fragment_widths.items())))
        if state in seen_states:
            raise RecipeSectionNonConvergenceError("recipe section pagination did not converge")
        seen_states.add(state)
        pages, observed_fragments = plan_pages(
            selected=selected,
            generation=generation,
            section_sha256=section_sha256,
            total_width=total_width,
            fragment_widths=fragment_widths,
            bound_bytes=recipe_section_bound_bytes,
            char_ceiling=char_ceiling,
        )
        required_total_width = len(str(len(pages)))
        required_fragment_widths = {
            index: len(str(count)) for index, count in observed_fragments.items()
        }
        grew = required_total_width > total_width
        if grew:
            total_width = required_total_width
        for index, width in required_fragment_widths.items():
            if width > fragment_widths.get(index, 1):
                fragment_widths[index] = width
                grew = True
        if not grew:
            break
    else:
        raise RecipeSectionNonConvergenceError("recipe section pagination did not converge")

    manifest = RecipeSectionPlanManifest(
        pagination_version=RECIPE_SECTION_PAGINATION_VERSION,
        section_registry_sha256=RECIPE_SECTION_REGISTRY_DIGEST,
        pagination_policy_sha256=RECIPE_SECTION_PAGINATION_POLICY_DIGEST,
        generation=generation,
        initialization_id=selected.initialization_id,
        section=selected.section,
        section_strategy=selected.definition.section_strategy,
        section_sha256=section_sha256,
        recipe_section_bound_bytes=recipe_section_bound_bytes,
        pages=tuple(page.descriptor for page in pages),
    )
    page_plan_sha256 = recipe_section_plan_digest(manifest)
    rendered_pages = tuple(
        _render_candidate(
            selected=selected,
            generation=generation,
            section_sha256=section_sha256,
            page=page,
            part=index,
            total_parts=len(pages),
            page_plan_sha256=page_plan_sha256,
            terminal=index + 1 == len(pages),
        )
        for index, page in enumerate(pages)
    )
    verify_finalized_recipe_section_plan(
        selected=selected,
        generation=generation,
        pages=pages,
        rendered_pages=rendered_pages,
        page_plan_sha256=page_plan_sha256,
        bound_bytes=recipe_section_bound_bytes,
        pagination_version=RECIPE_SECTION_PAGINATION_VERSION,
        pagination_policy_sha256=RECIPE_SECTION_PAGINATION_POLICY_DIGEST,
        section_registry_sha256=RECIPE_SECTION_REGISTRY_DIGEST,
        section_sha256=section_sha256,
        char_ceiling=char_ceiling,
    )
    cache_weight = sum(len(rendered.encode("utf-8")) for rendered in rendered_pages)
    cache_weight += len(canonical_recipe_section_json(manifest).encode("utf-8"))
    cache_weight += sum(len(page.content.encode("utf-8")) for page in pages)
    return RecipeSectionPagePlan(
        manifest=manifest,
        page_plan_sha256=page_plan_sha256,
        rendered_pages=rendered_pages,
        cache_weight_bytes=cache_weight,
    )


def _cache_key(
    *,
    kitchen_id: str,
    generation: RecipeArtifactGeneration,
    selected: SelectedRecipeSection,
    recipe_section_bound_bytes: int,
    char_ceiling: int | None = None,
) -> _PagePlanCacheKey:
    return _PagePlanCacheKey(
        kitchen_id=kitchen_id,
        generation=generation,
        initialization_id=selected.initialization_id,
        section=selected.section,
        section_sha256=selected_section_sha256(selected),
        recipe_section_bound_bytes=recipe_section_bound_bytes,
        section_registry_sha256=RECIPE_SECTION_REGISTRY_DIGEST,
        pagination_policy_sha256=RECIPE_SECTION_PAGINATION_POLICY_DIGEST,
        pagination_version=RECIPE_SECTION_PAGINATION_VERSION,
        char_ceiling=char_ceiling,
    )


def get_or_build_recipe_section_page_plan(
    *,
    kitchen_id: str,
    generation: RecipeArtifactGeneration,
    selected: SelectedRecipeSection,
    recipe_section_bound_bytes: int,
    char_ceiling: int | None = None,
) -> RecipeSectionPagePlan:
    """Return a verified cached plan or build and admit one."""
    key = _cache_key(
        kitchen_id=kitchen_id,
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=recipe_section_bound_bytes,
        char_ceiling=char_ceiling,
    )
    cache = _page_plan_cache()
    return cache.get_or_build(
        key,
        lambda: build_recipe_section_page_plan(
            kitchen_id=kitchen_id,
            generation=generation,
            selected=selected,
            recipe_section_bound_bytes=recipe_section_bound_bytes,
            char_ceiling=char_ceiling,
        ),
    )


def render_recipe_section_page(plan: RecipeSectionPagePlan, part: int) -> str:
    """Return one already-finalized exact page rendering."""
    return plan.rendered_pages[part]
