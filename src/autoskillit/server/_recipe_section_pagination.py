"""Deterministic grammar-aware pagination for persisted recipe sections."""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from threading import Event, Lock, RLock

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
    get_logger,
    recipe_section_digest,
    recipe_section_element_digest,
    recipe_section_plan_digest,
)
from autoskillit.server._recipe_initialization import recipe_initialization_receipt
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
_PLAN_DIGEST_PLACEHOLDER = "sha256:" + ("0" * 64)


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
    "SelectedRecipeSection",
    "build_recipe_section_page_plan",
    "evict_kitchen",
    "get_or_build_recipe_section_page_plan",
    "render_recipe_section_failure",
    "render_recipe_section_page",
    "recipe_section_continuation_binding",
    "resolve_recipe_section_definition",
    "resolve_recipe_section_bound_bytes",
    "select_recipe_section",
]


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
    from autoskillit.core._delivery_bounds import resolve_recipe_section_response_bound

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


def _qualified_content_digest(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


def _utf8_prefix_offsets(value: str) -> list[int]:
    offsets = [0]
    total = 0
    for character in value:
        total += len(character.encode("utf-8"))
        offsets.append(total)
    return offsets


def _max_utf8_prefix_end(
    offsets: list[int],
    *,
    start: int,
    bound_bytes: int,
) -> int:
    """Return the largest end whose content bytes alone can fit the response bound."""
    return (
        bisect_right(
            offsets,
            offsets[start] + bound_bytes,
            lo=start + 1,
        )
        - 1
    )


def _render_candidate(
    *,
    selected: SelectedRecipeSection,
    generation: RecipeArtifactGeneration,
    section_sha256: str,
    page: PlannedRecipeSectionPage,
    part: int,
    total_parts: int,
    page_plan_sha256: str,
    terminal: bool,
) -> str:
    body: dict[str, object] = {
        "content": page.content,
        "content_format": page.descriptor.content_format,
        "has_more": not terminal,
        "page_plan_sha256": page_plan_sha256,
        "pagination_policy_sha256": RECIPE_SECTION_PAGINATION_POLICY_DIGEST,
        "pagination_version": RECIPE_SECTION_PAGINATION_VERSION,
        "part": part,
        "section": selected.section,
        "section_registry_sha256": RECIPE_SECTION_REGISTRY_DIGEST,
        "section_sha256": section_sha256,
        "success": True,
        "total_parts": total_parts,
    }
    body.update(generation.pull_identity())
    body.pop("pull_tool")
    if selected.initialization_id is not None:
        body["initialization_id"] = selected.initialization_id
        body["completed_parts"] = part + 1
        body["remaining_section_pulls"] = total_parts - part - 1
    if terminal and selected.completion_response is not None:
        completion_response = dict(selected.completion_response)
        content_sha256 = page.descriptor.page_content_sha256
        completion_response["content_sha256"] = content_sha256
        assert selected.initialization_id is not None
        completion_response["completion_receipt"] = recipe_initialization_receipt(
            selected.initialization_id,
            generation,
            content_sha256=content_sha256,
        )
        body.update(completion_response)
    if not terminal:
        body["next_part"] = part + 1
        body["continuation"] = recipe_section_continuation_binding(
            generation=generation,
            initialization_id=selected.initialization_id,
            section=selected.section,
            section_sha256=section_sha256,
            page_plan_sha256=page_plan_sha256,
            next_part=part + 1,
        )
    body.update(page.descriptor.wire_ranges())
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def recipe_section_continuation_binding(
    *,
    generation: RecipeArtifactGeneration,
    initialization_id: str | None,
    section: str,
    section_sha256: str,
    page_plan_sha256: str,
    next_part: int,
) -> str:
    """Bind the next part to the complete immutable request without a cursor."""
    material = json.dumps(
        {
            "generation": generation.pull_identity(),
            "initialization_id": initialization_id,
            "next_part": next_part,
            "page_plan_sha256": page_plan_sha256,
            "section": section,
            "section_sha256": section_sha256,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return (
        "sha256:"
        + hashlib.sha256(b"autoskillit.recipe-section-continuation.v1\0" + material).hexdigest()
    )


def _fits(
    *,
    selected: SelectedRecipeSection,
    generation: RecipeArtifactGeneration,
    section_sha256: str,
    page: PlannedRecipeSectionPage,
    part: int,
    total_width: int,
    terminal: bool,
    bound_bytes: int,
) -> bool:
    rendered = _render_candidate(
        selected=selected,
        generation=generation,
        section_sha256=section_sha256,
        page=page,
        part=part,
        total_parts=(10**total_width) - 1,
        page_plan_sha256=_PLAN_DIGEST_PLACEHOLDER,
        terminal=terminal,
    )
    return len(rendered.encode("utf-8")) <= bound_bytes


def _raw_or_scalar_pages(
    *,
    selected: SelectedRecipeSection,
    generation: RecipeArtifactGeneration,
    section_sha256: str,
    value: str,
    total_width: int,
    bound_bytes: int,
    scalar: bool,
) -> list[PlannedRecipeSectionPage]:
    offsets = _utf8_prefix_offsets(value)
    pages: list[PlannedRecipeSectionPage] = []
    if not value:
        content = canonical_recipe_section_json("") if scalar else ""
        if scalar:
            descriptor = RecipeSectionPageDescriptor(
                content_format=selected.definition.ordinary_content_format,
                page_content_sha256=_qualified_content_digest(content),
                scalar_byte_start=0,
                scalar_byte_end=0,
                scalar_byte_total=0,
            )
        else:
            descriptor = RecipeSectionPageDescriptor(
                content_format=selected.definition.ordinary_content_format,
                page_content_sha256=_qualified_content_digest(content),
                byte_start=0,
                byte_end=0,
                byte_total=0,
            )
        page = PlannedRecipeSectionPage(descriptor, content)
        if not _fits(
            selected=selected,
            generation=generation,
            section_sha256=section_sha256,
            page=page,
            part=0,
            total_width=total_width,
            terminal=True,
            bound_bytes=bound_bytes,
        ):
            raise RecipeSectionBoundError("recipe section bound cannot fit an empty page")
        return [page]

    start = 0
    while start < len(value):
        part = len(pages)

        def candidate_for(end: int) -> PlannedRecipeSectionPage:
            chunk = value[start:end]
            content = canonical_recipe_section_json(chunk) if scalar else chunk
            if scalar:
                descriptor = RecipeSectionPageDescriptor(
                    content_format=selected.definition.ordinary_content_format,
                    page_content_sha256=_qualified_content_digest(content),
                    scalar_byte_start=offsets[start],
                    scalar_byte_end=offsets[end],
                    scalar_byte_total=offsets[-1],
                )
            else:
                descriptor = RecipeSectionPageDescriptor(
                    content_format=selected.definition.ordinary_content_format,
                    page_content_sha256=_qualified_content_digest(content),
                    byte_start=offsets[start],
                    byte_end=offsets[end],
                    byte_total=offsets[-1],
                )
            return PlannedRecipeSectionPage(descriptor, content)

        max_end = _max_utf8_prefix_end(
            offsets,
            start=start,
            bound_bytes=bound_bytes,
        )
        if max_end == len(value):
            terminal_candidate = candidate_for(max_end)
            if _fits(
                selected=selected,
                generation=generation,
                section_sha256=section_sha256,
                page=terminal_candidate,
                part=part,
                total_width=total_width,
                terminal=True,
                bound_bytes=bound_bytes,
            ):
                pages.append(terminal_candidate)
                break

        low = start + 1
        high = min(max_end, len(value) - 1)
        accepted: PlannedRecipeSectionPage | None = None
        accepted_end = start
        while low <= high:
            end = (low + high) // 2
            candidate = candidate_for(end)
            if _fits(
                selected=selected,
                generation=generation,
                section_sha256=section_sha256,
                page=candidate,
                part=part,
                total_width=total_width,
                terminal=False,
                bound_bytes=bound_bytes,
            ):
                accepted = candidate
                accepted_end = end
                low = end + 1
            else:
                high = end - 1
        if accepted is None or accepted_end == start:
            raise RecipeSectionBoundError("recipe section bound cannot fit progress")
        pages.append(accepted)
        start = accepted_end
    return pages


def _array_page(
    *,
    definition: RecipeSectionDef,
    canonical_elements: list[str],
    start: int,
    end: int,
) -> PlannedRecipeSectionPage:
    content = "[" + ",".join(canonical_elements[start:end]) + "]"
    return PlannedRecipeSectionPage(
        RecipeSectionPageDescriptor(
            content_format=definition.ordinary_content_format,
            page_content_sha256=_qualified_content_digest(content),
            element_start=start,
            element_end=end,
            element_total=len(canonical_elements),
        ),
        content,
    )


def _canonical_array_prefix_bytes(canonical_elements: list[str]) -> list[int]:
    prefix_bytes = [0]
    for element in canonical_elements:
        prefix_bytes.append(prefix_bytes[-1] + len(element.encode("utf-8")) + 1)
    return prefix_bytes


def _max_array_page_end(
    prefix_bytes: list[int],
    *,
    start: int,
    bound_bytes: int,
) -> int:
    """Bound one candidate by the bytes of its JSON array content alone."""
    return (
        bisect_right(
            prefix_bytes,
            prefix_bytes[start] + bound_bytes - 1,
            lo=start + 1,
        )
        - 1
    )


def _fragment_pages(
    *,
    selected: SelectedRecipeSection,
    generation: RecipeArtifactGeneration,
    section_sha256: str,
    element: object,
    canonical_element: str,
    element_index: int,
    element_total: int,
    part_start: int,
    total_width: int,
    fragment_width: int,
    bound_bytes: int,
) -> list[PlannedRecipeSectionPage]:
    offsets = _utf8_prefix_offsets(canonical_element)
    element_sha256 = recipe_section_element_digest(element)
    pages: list[PlannedRecipeSectionPage] = []
    start = 0
    assumed_fragment_count = (10**fragment_width) - 1
    content_format = selected.definition.oversized_content_format
    if content_format is None:
        raise ValueError("array recipe section definition requires an oversized format")
    while start < len(canonical_element):
        part = part_start + len(pages)
        fragment_index = len(pages)

        def candidate_for(end: int) -> PlannedRecipeSectionPage:
            content = canonical_recipe_section_json(canonical_element[start:end])
            descriptor = RecipeSectionPageDescriptor(
                content_format=content_format,
                page_content_sha256=_qualified_content_digest(content),
                element_index=element_index,
                element_sha256=element_sha256,
                fragment_index=fragment_index,
                fragment_count=max(assumed_fragment_count, fragment_index + 1),
                fragment_byte_start=offsets[start],
                fragment_byte_end=offsets[end],
                fragment_byte_total=offsets[-1],
            )
            return PlannedRecipeSectionPage(descriptor, content)

        max_end = _max_utf8_prefix_end(
            offsets,
            start=start,
            bound_bytes=bound_bytes,
        )
        is_final_element = element_index + 1 == element_total
        if is_final_element and max_end == len(canonical_element):
            terminal_candidate = candidate_for(max_end)
            if _fits(
                selected=selected,
                generation=generation,
                section_sha256=section_sha256,
                page=terminal_candidate,
                part=part,
                total_width=total_width,
                terminal=True,
                bound_bytes=bound_bytes,
            ):
                pages.append(terminal_candidate)
                break

        low = start + 1
        high = min(max_end, len(canonical_element) - 1) if is_final_element else max_end
        accepted: PlannedRecipeSectionPage | None = None
        accepted_end = start
        while low <= high:
            end = (low + high) // 2
            candidate = candidate_for(end)
            if _fits(
                selected=selected,
                generation=generation,
                section_sha256=section_sha256,
                page=candidate,
                part=part,
                total_width=total_width,
                terminal=False,
                bound_bytes=bound_bytes,
            ):
                accepted = candidate
                accepted_end = end
                low = end + 1
            else:
                high = end - 1
        if accepted is None or accepted_end == start:
            raise RecipeSectionBoundError("recipe section bound cannot fit element fragment")
        pages.append(accepted)
        start = accepted_end
    fragment_count = len(pages)
    return [
        PlannedRecipeSectionPage(
            replace(page.descriptor, fragment_count=fragment_count),
            page.content,
        )
        for page in pages
    ]


def _array_pages(
    *,
    selected: SelectedRecipeSection,
    generation: RecipeArtifactGeneration,
    section_sha256: str,
    values: list[object],
    total_width: int,
    fragment_widths: Mapping[int, int],
    bound_bytes: int,
) -> tuple[list[PlannedRecipeSectionPage], dict[int, int]]:
    canonical_elements = [canonical_recipe_section_json(value) for value in values]
    prefix_bytes = _canonical_array_prefix_bytes(canonical_elements)
    if not values:
        page = _array_page(
            definition=selected.definition,
            canonical_elements=canonical_elements,
            start=0,
            end=0,
        )
        if not _fits(
            selected=selected,
            generation=generation,
            section_sha256=section_sha256,
            page=page,
            part=0,
            total_width=total_width,
            terminal=True,
            bound_bytes=bound_bytes,
        ):
            raise RecipeSectionBoundError("recipe section bound cannot fit an empty array")
        return [page], {}

    pages: list[PlannedRecipeSectionPage] = []
    observed_fragments: dict[int, int] = {}
    start = 0
    while start < len(values):
        part = len(pages)
        max_end = _max_array_page_end(
            prefix_bytes,
            start=start,
            bound_bytes=bound_bytes,
        )
        if max_end == len(values):
            terminal_candidate = _array_page(
                definition=selected.definition,
                canonical_elements=canonical_elements,
                start=start,
                end=max_end,
            )
            if _fits(
                selected=selected,
                generation=generation,
                section_sha256=section_sha256,
                page=terminal_candidate,
                part=part,
                total_width=total_width,
                terminal=True,
                bound_bytes=bound_bytes,
            ):
                pages.append(terminal_candidate)
                break

        low = start + 1
        high = min(max_end, len(values) - 1)
        accepted: PlannedRecipeSectionPage | None = None
        accepted_end = start
        while low <= high:
            end = (low + high) // 2
            candidate = _array_page(
                definition=selected.definition,
                canonical_elements=canonical_elements,
                start=start,
                end=end,
            )
            if _fits(
                selected=selected,
                generation=generation,
                section_sha256=section_sha256,
                page=candidate,
                part=part,
                total_width=total_width,
                terminal=False,
                bound_bytes=bound_bytes,
            ):
                accepted = candidate
                accepted_end = end
                low = end + 1
            else:
                high = end - 1
        if accepted is not None:
            pages.append(accepted)
            start = accepted_end
            continue

        fragments = _fragment_pages(
            selected=selected,
            generation=generation,
            section_sha256=section_sha256,
            element=values[start],
            canonical_element=canonical_elements[start],
            element_index=start,
            element_total=len(values),
            part_start=len(pages),
            total_width=total_width,
            fragment_width=fragment_widths.get(start, 1),
            bound_bytes=bound_bytes,
        )
        pages.extend(fragments)
        observed_fragments[start] = len(fragments)
        start += 1
    return pages, observed_fragments


def _selected_section_sha256(selected: SelectedRecipeSection) -> str:
    if not selected.present:
        raise ValueError(f"recipe section {selected.section!r} is absent")
    raw = selected.definition.section_strategy == "raw"
    return recipe_section_digest(selected.value, raw=raw)


def _plan_pages(
    *,
    selected: SelectedRecipeSection,
    generation: RecipeArtifactGeneration,
    section_sha256: str,
    total_width: int,
    fragment_widths: Mapping[int, int],
    bound_bytes: int,
) -> tuple[list[PlannedRecipeSectionPage], dict[int, int]]:
    strategy = selected.definition.section_strategy
    if strategy in {"raw", "scalar"}:
        if type(selected.value) is not str:
            raise TypeError(f"{strategy} recipe section strategy requires a string")
        return (
            _raw_or_scalar_pages(
                selected=selected,
                generation=generation,
                section_sha256=section_sha256,
                value=selected.value,
                total_width=total_width,
                bound_bytes=bound_bytes,
                scalar=strategy == "scalar",
            ),
            {},
        )
    if strategy == "array":
        if type(selected.value) is not list:
            raise TypeError("array recipe section strategy requires a list")
        return _array_pages(
            selected=selected,
            generation=generation,
            section_sha256=section_sha256,
            values=selected.value,
            total_width=total_width,
            fragment_widths=fragment_widths,
            bound_bytes=bound_bytes,
        )
    raise ValueError(f"unknown recipe section strategy: {strategy}")


def build_recipe_section_page_plan(
    *,
    kitchen_id: str,
    generation: RecipeArtifactGeneration,
    selected: SelectedRecipeSection,
    recipe_section_bound_bytes: int,
) -> RecipeSectionPagePlan:
    """Build, finalize, and verify one deterministic immutable page plan."""
    del kitchen_id  # Identity participates in the cache key, not the manifest.
    if recipe_section_bound_bytes <= 0:
        raise RecipeSectionBoundError("recipe section bound must be positive")
    section_sha256 = _selected_section_sha256(selected)
    total_width = 1
    fragment_widths: dict[int, int] = {}
    seen_states: set[tuple[int, tuple[tuple[int, int], ...]]] = set()
    pages: list[PlannedRecipeSectionPage] = []
    for _ in range(_convergence_iteration_ceiling()):
        state = (total_width, tuple(sorted(fragment_widths.items())))
        if state in seen_states:
            raise RecipeSectionNonConvergenceError("recipe section pagination did not converge")
        seen_states.add(state)
        pages, observed_fragments = _plan_pages(
            selected=selected,
            generation=generation,
            section_sha256=section_sha256,
            total_width=total_width,
            fragment_widths=fragment_widths,
            bound_bytes=recipe_section_bound_bytes,
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
) -> _PagePlanCacheKey:
    return _PagePlanCacheKey(
        kitchen_id=kitchen_id,
        generation=generation,
        initialization_id=selected.initialization_id,
        section=selected.section,
        section_sha256=_selected_section_sha256(selected),
        recipe_section_bound_bytes=recipe_section_bound_bytes,
        section_registry_sha256=RECIPE_SECTION_REGISTRY_DIGEST,
        pagination_policy_sha256=RECIPE_SECTION_PAGINATION_POLICY_DIGEST,
        pagination_version=RECIPE_SECTION_PAGINATION_VERSION,
    )


def get_or_build_recipe_section_page_plan(
    *,
    kitchen_id: str,
    generation: RecipeArtifactGeneration,
    selected: SelectedRecipeSection,
    recipe_section_bound_bytes: int,
) -> RecipeSectionPagePlan:
    """Return a verified cached plan or build and admit one."""
    key = _cache_key(
        kitchen_id=kitchen_id,
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=recipe_section_bound_bytes,
    )
    cache = _page_plan_cache()
    return cache.get_or_build(
        key,
        lambda: build_recipe_section_page_plan(
            kitchen_id=kitchen_id,
            generation=generation,
            selected=selected,
            recipe_section_bound_bytes=recipe_section_bound_bytes,
        ),
    )


def render_recipe_section_page(plan: RecipeSectionPagePlan, part: int) -> str:
    """Return one already-finalized exact page rendering."""
    return plan.rendered_pages[part]
