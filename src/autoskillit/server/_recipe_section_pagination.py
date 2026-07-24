"""Deterministic grammar-aware pagination for persisted recipe sections."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from threading import Lock, RLock

from autoskillit.core import (
    DYNAMIC_RECIPE_SECTION_DEF,
    RECIPE_SECTION_PAGINATION_POLICY_DIGEST,
    RECIPE_SECTION_PAGINATION_VERSION,
    RECIPE_SECTION_REGISTRY,
    RECIPE_SECTION_REGISTRY_DIGEST,
    RecipeSectionDef,
    canonical_recipe_section_json,
    get_logger,
    recipe_section_digest,
    recipe_section_element_digest,
    recipe_section_plan_digest,
)
from autoskillit.server._recipe_delivery import (
    RECIPE_ARTIFACT_MAX_BLOB_BYTES,
    RecipeArtifactGeneration,
)
from autoskillit.server.recipe_section._lifecycle import register_kitchen_retirement_callback
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
    "resolve_recipe_section_bound_bytes",
    "select_recipe_section",
]


class RecipeSectionPaginationError(RuntimeError):
    """A verified immutable page plan could not be established."""


class RecipeSectionBoundError(RecipeSectionPaginationError):
    """The captured request bound cannot fit one progress-making page."""


@dataclass(frozen=True, slots=True)
class RecipeSectionRequestState:
    """One captured request admission and byte-bound decision."""

    admitted: bool
    recipe_section_bound_bytes: int


@dataclass(frozen=True, slots=True)
class SelectedRecipeSection:
    """A registry-selected section whose value remains typed."""

    section: str
    definition: RecipeSectionDef
    value: object
    present: bool


@dataclass(frozen=True, slots=True)
class RecipeSectionPageDescriptor:
    """One immutable page boundary in a page-plan manifest."""

    content_format: str
    page_content_sha256: str
    byte_start: int | None = None
    byte_end: int | None = None
    byte_total: int | None = None
    element_start: int | None = None
    element_end: int | None = None
    element_total: int | None = None
    scalar_byte_start: int | None = None
    scalar_byte_end: int | None = None
    scalar_byte_total: int | None = None
    element_index: int | None = None
    element_sha256: str | None = None
    fragment_index: int | None = None
    fragment_count: int | None = None
    fragment_byte_start: int | None = None
    fragment_byte_end: int | None = None
    fragment_byte_total: int | None = None

    def wire_ranges(self) -> dict[str, int | str]:
        values: dict[str, int | str | None] = {
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "byte_total": self.byte_total,
            "element_start": self.element_start,
            "element_end": self.element_end,
            "element_total": self.element_total,
            "scalar_byte_start": self.scalar_byte_start,
            "scalar_byte_end": self.scalar_byte_end,
            "scalar_byte_total": self.scalar_byte_total,
            "element_index": self.element_index,
            "element_sha256": self.element_sha256,
            "fragment_index": self.fragment_index,
            "fragment_count": self.fragment_count,
            "fragment_byte_start": self.fragment_byte_start,
            "fragment_byte_end": self.fragment_byte_end,
            "fragment_byte_total": self.fragment_byte_total,
        }
        return {name: value for name, value in values.items() if value is not None}


@dataclass(frozen=True, slots=True)
class _PlannedPage:
    descriptor: RecipeSectionPageDescriptor
    content: str


@dataclass(frozen=True, slots=True)
class RecipeSectionPlanManifest:
    """Sole non-self-referential preimage for one page-plan digest."""

    pagination_version: int
    section_registry_sha256: str
    pagination_policy_sha256: str
    generation: RecipeArtifactGeneration
    section: str
    section_strategy: str
    section_sha256: str
    recipe_section_bound_bytes: int
    pages: tuple[RecipeSectionPageDescriptor, ...]


@dataclass(frozen=True, slots=True)
class RecipeSectionPagePlan:
    """A finalized immutable manifest and its exact rendered pages."""

    manifest: RecipeSectionPlanManifest
    page_plan_sha256: str
    rendered_pages: tuple[str, ...]
    cache_weight_bytes: int

    @property
    def total_parts(self) -> int:
        return len(self.rendered_pages)


@dataclass(frozen=True, slots=True)
class _PagePlanCacheKey:
    kitchen_id: str
    generation: RecipeArtifactGeneration
    section: str
    section_sha256: str
    recipe_section_bound_bytes: int
    section_registry_sha256: str
    pagination_policy_sha256: str
    pagination_version: int


class PagePlanCache:
    """Bounded thread-safe LRU storing only verified immutable plans."""

    def __init__(
        self,
        *,
        max_entries: int = PAGE_PLAN_CACHE_MAX_ENTRIES,
        max_bytes: int = PAGE_PLAN_CACHE_MAX_BYTES,
    ) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: OrderedDict[_PagePlanCacheKey, RecipeSectionPagePlan] = OrderedDict()
        self._weight_bytes = 0
        self._lock = RLock()

    def get(self, key: _PagePlanCacheKey) -> RecipeSectionPagePlan | None:
        with self._lock:
            plan = self._entries.get(key)
            if plan is not None:
                self._entries.move_to_end(key)
            return plan

    def put(self, key: _PagePlanCacheKey, plan: RecipeSectionPagePlan) -> None:
        if plan.cache_weight_bytes > self._max_bytes:
            return
        with self._lock:
            existing = self._entries.pop(key, None)
            if existing is not None:
                self._weight_bytes -= existing.cache_weight_bytes
            self._entries[key] = plan
            self._weight_bytes += plan.cache_weight_bytes
            while len(self._entries) > self._max_entries or self._weight_bytes > self._max_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._weight_bytes -= evicted.cache_weight_bytes

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._weight_bytes = 0

    def evict_kitchen(self, kitchen_id: str) -> None:
        with self._lock:
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
) -> int:
    """Resolve the deliberately conservative ordinary recipe-pull ceiling."""
    return min(response_max_bytes, conservative_general_result_limit)


def render_recipe_section_failure(
    code: str,
    *,
    bound_bytes: int,
    context: Mapping[str, object] | None = None,
) -> str:
    """Render an atomic bounded failure, dropping context rather than truncating it."""
    base: dict[str, object] = {"error": code, "success": False}
    compact = lambda value: json.dumps(  # noqa: E731
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if context:
        candidate = dict(base)
        candidate.update(
            {name: value for name, value in context.items() if name not in {"error", "success"}}
        )
        rendered = compact(candidate)
        if len(rendered.encode("utf-8")) <= bound_bytes:
            return rendered
    return compact(base)


def select_recipe_section(
    payload: Mapping[str, object],
    section: str,
    *,
    dynamic_content: str | None = None,
) -> SelectedRecipeSection:
    """Select a fixed or validated dynamic section without pre-serializing its value."""
    definition = RECIPE_SECTION_REGISTRY.get(section)
    if definition is not None:
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

    step_names = payload.get("post_prune_step_names")
    is_dynamic = (
        type(step_names) is list
        and type(section) is str
        and any(type(name) is str and name == section for name in step_names)
    )
    return SelectedRecipeSection(
        section=section,
        definition=DYNAMIC_RECIPE_SECTION_DEF,
        value=dynamic_content,
        present=is_dynamic and dynamic_content is not None,
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


def _render_candidate(
    *,
    selected: SelectedRecipeSection,
    generation: RecipeArtifactGeneration,
    section_sha256: str,
    page: _PlannedPage,
    part: int,
    total_parts: int,
    page_plan_sha256: str,
    terminal: bool,
) -> str:
    body: dict[str, object] = {
        "body_sha256": generation.body_sha256,
        "content": page.content,
        "content_format": page.descriptor.content_format,
        "has_more": not terminal,
        "page_plan_sha256": page_plan_sha256,
        "pagination_version": RECIPE_SECTION_PAGINATION_VERSION,
        "part": part,
        "payload_sha256": generation.payload_sha256,
        "section": selected.section,
        "section_registry_sha256": RECIPE_SECTION_REGISTRY_DIGEST,
        "section_sha256": section_sha256,
        "success": True,
        "total_parts": total_parts,
    }
    if not terminal:
        body["next_part"] = part + 1
    body.update(page.descriptor.wire_ranges())
    return json.dumps(
        body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _fits(
    *,
    selected: SelectedRecipeSection,
    generation: RecipeArtifactGeneration,
    section_sha256: str,
    page: _PlannedPage,
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
) -> list[_PlannedPage]:
    offsets = _utf8_prefix_offsets(value)
    pages: list[_PlannedPage] = []
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
        page = _PlannedPage(descriptor, content)
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

        def candidate_for(end: int) -> _PlannedPage:
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
            return _PlannedPage(descriptor, content)

        terminal_candidate = candidate_for(len(value))
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
        high = len(value) - 1
        accepted: _PlannedPage | None = None
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
) -> _PlannedPage:
    content = "[" + ",".join(canonical_elements[start:end]) + "]"
    return _PlannedPage(
        RecipeSectionPageDescriptor(
            content_format=definition.ordinary_content_format,
            page_content_sha256=_qualified_content_digest(content),
            element_start=start,
            element_end=end,
            element_total=len(canonical_elements),
        ),
        content,
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
) -> list[_PlannedPage]:
    offsets = _utf8_prefix_offsets(canonical_element)
    element_sha256 = recipe_section_element_digest(element)
    pages: list[_PlannedPage] = []
    start = 0
    assumed_fragment_count = (10**fragment_width) - 1
    content_format = selected.definition.oversized_content_format
    if content_format is None:
        raise ValueError("array recipe section definition requires an oversized format")
    while start < len(canonical_element):
        part = part_start + len(pages)
        fragment_index = len(pages)

        def candidate_for(end: int) -> _PlannedPage:
            content = canonical_recipe_section_json(canonical_element[start:end])
            descriptor = RecipeSectionPageDescriptor(
                content_format=content_format,
                page_content_sha256=_qualified_content_digest(content),
                element_index=element_index,
                element_sha256=element_sha256,
                fragment_index=fragment_index,
                fragment_count=assumed_fragment_count,
                fragment_byte_start=offsets[start],
                fragment_byte_end=offsets[end],
                fragment_byte_total=offsets[-1],
            )
            return _PlannedPage(descriptor, content)

        if element_index + 1 == element_total:
            terminal_candidate = candidate_for(len(canonical_element))
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
        high = (
            len(canonical_element) - 1
            if element_index + 1 == element_total
            else len(canonical_element)
        )
        accepted: _PlannedPage | None = None
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
        _PlannedPage(
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
) -> tuple[list[_PlannedPage], dict[int, int]]:
    canonical_elements = [canonical_recipe_section_json(value) for value in values]
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

    pages: list[_PlannedPage] = []
    observed_fragments: dict[int, int] = {}
    start = 0
    while start < len(values):
        part = len(pages)
        terminal_candidate = _array_page(
            definition=selected.definition,
            canonical_elements=canonical_elements,
            start=start,
            end=len(values),
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
        high = len(values) - 1
        accepted: _PlannedPage | None = None
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
) -> tuple[list[_PlannedPage], dict[int, int]]:
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
    pages: list[_PlannedPage] = []
    for _ in range(_convergence_iteration_ceiling()):
        state = (total_width, tuple(sorted(fragment_widths.items())))
        if state in seen_states:
            raise RecipeSectionPaginationError("recipe section pagination did not converge")
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
        raise RecipeSectionPaginationError("recipe section pagination did not converge")

    manifest = RecipeSectionPlanManifest(
        pagination_version=RECIPE_SECTION_PAGINATION_VERSION,
        section_registry_sha256=RECIPE_SECTION_REGISTRY_DIGEST,
        pagination_policy_sha256=RECIPE_SECTION_PAGINATION_POLICY_DIGEST,
        generation=generation,
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
    cached = cache.get(key)
    if cached is not None:
        return cached
    plan = build_recipe_section_page_plan(
        kitchen_id=kitchen_id,
        generation=generation,
        selected=selected,
        recipe_section_bound_bytes=recipe_section_bound_bytes,
    )
    cache.put(key, plan)
    return plan


def render_recipe_section_page(plan: RecipeSectionPagePlan, part: int) -> str:
    """Return one already-finalized exact page rendering."""
    return plan.rendered_pages[part]
