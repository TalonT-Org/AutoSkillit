"""Page boundary planning for recipe section pagination.

This module implements the page-fitting engine: given a section's content and
a dual byte/char bound, it plans page boundaries using binary search and renders
candidate pages to check fitness.  Extracted from ``_recipe_section_pagination``
to keep both modules under the 750-line structural limit.
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import replace

from autoskillit.core import (
    RECIPE_SECTION_PAGINATION_POLICY_DIGEST,
    RECIPE_SECTION_PAGINATION_VERSION,
    RECIPE_SECTION_REGISTRY_DIGEST,
    RecipeArtifactGeneration,
    RecipeSectionDef,
    canonical_recipe_section_json,
    client_serialized_char_len,
    recipe_section_digest,
    recipe_section_element_digest,
)
from autoskillit.server._recipe_initialization import recipe_initialization_receipt
from autoskillit.server.recipe_section._contracts import (
    PlannedRecipeSectionPage,
    RecipeSectionBoundError,
    RecipeSectionPageDescriptor,
    SelectedRecipeSection,
)

_PLAN_DIGEST_PLACEHOLDER = "sha256:" + ("0" * 64)


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


def _decode_flow_record_elements(content: list[object]) -> list[object]:
    """Decode canonical flow-record object strings while preserving other values."""
    records: list[object] = []
    for element in content:
        if isinstance(element, str):
            try:
                decoded = json.loads(element)
            except (json.JSONDecodeError, TypeError):
                records.append(element)
                continue
            records.append(decoded if isinstance(decoded, dict) else element)
        else:
            records.append(element)
    return records


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
    if page.descriptor.content_format == "json-array-page":
        # Flatten: parse the canonical array string into a structured list.
        # The single json.dumps below serializes everything once — the
        # string-in-string layer disappears here.
        content = json.loads(page.content)
        if selected.section == "flow_records":
            content = _decode_flow_record_elements(content)
        body["content"] = content
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
    char_ceiling: int | None = None,
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
    if len(rendered.encode("utf-8")) > bound_bytes:
        return False
    if char_ceiling is not None and client_serialized_char_len(rendered).value > char_ceiling:
        return False
    return True


def _raw_or_scalar_pages(
    *,
    selected: SelectedRecipeSection,
    generation: RecipeArtifactGeneration,
    section_sha256: str,
    value: str,
    total_width: int,
    bound_bytes: int,
    scalar: bool,
    char_ceiling: int | None = None,
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
            char_ceiling=char_ceiling,
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
                char_ceiling=char_ceiling,
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
                char_ceiling=char_ceiling,
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
    char_ceiling: int | None = None,
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
                char_ceiling=char_ceiling,
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
                char_ceiling=char_ceiling,
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
    char_ceiling: int | None = None,
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
            char_ceiling=char_ceiling,
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
                char_ceiling=char_ceiling,
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
                char_ceiling=char_ceiling,
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
            char_ceiling=char_ceiling,
        )
        pages.extend(fragments)
        observed_fragments[start] = len(fragments)
        start += 1
    return pages, observed_fragments


def selected_section_sha256(selected: SelectedRecipeSection) -> str:
    """Compute the content digest of a selected recipe section."""
    if not selected.present:
        raise ValueError(f"recipe section {selected.section!r} is absent")
    raw = selected.definition.section_strategy == "raw"
    return recipe_section_digest(selected.value, raw=raw)


def plan_pages(
    *,
    selected: SelectedRecipeSection,
    generation: RecipeArtifactGeneration,
    section_sha256: str,
    total_width: int,
    fragment_widths: Mapping[int, int],
    bound_bytes: int,
    char_ceiling: int | None = None,
) -> tuple[list[PlannedRecipeSectionPage], dict[int, int]]:
    """Plan page boundaries for one recipe section."""
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
                char_ceiling=char_ceiling,
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
            char_ceiling=char_ceiling,
        )
    raise ValueError(f"unknown recipe section strategy: {strategy}")
