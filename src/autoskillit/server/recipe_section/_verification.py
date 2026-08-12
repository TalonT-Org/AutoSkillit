"""Final invariant proof for immutable recipe-section page plans."""

from __future__ import annotations

import hashlib
import json

from autoskillit.core import (
    RecipeArtifactGeneration,
    client_serialized_char_len,
    recipe_section_element_digest,
)
from autoskillit.server.recipe_section._contracts import (
    RECIPE_SECTION_PAGE_RANGE_FIELDS,
    PlannedRecipeSectionPage,
    RecipeSectionPaginationError,
    SelectedRecipeSection,
)


def _pagination_error(message: str, *, cause: Exception | None = None) -> Exception:
    error = RecipeSectionPaginationError(message)
    if cause is not None:
        error.__cause__ = cause
    return error


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise _pagination_error(message)


def _decode(content: str, message: str) -> object:
    try:
        return json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise _pagination_error(message, cause=exc)


def _content_digest(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


def _verify_reconstruction(
    selected: SelectedRecipeSection,
    pages: list[PlannedRecipeSectionPage],
) -> None:
    strategy = selected.definition.section_strategy
    reconstructed: object
    if strategy == "raw":
        reconstructed = "".join(page.content for page in pages)
    elif strategy == "scalar":
        reconstructed = "".join(json.loads(page.content) for page in pages)
    else:
        reconstructed_values: list[object] = []
        index = 0
        while index < len(pages):
            page = pages[index]
            descriptor = page.descriptor
            if descriptor.content_format == selected.definition.ordinary_content_format:
                reconstructed_values.extend(json.loads(page.content))
                index += 1
                continue
            count = descriptor.fragment_count or 0
            canonical = "".join(
                json.loads(pages[index + offset].content) for offset in range(count)
            )
            reconstructed_values.append(json.loads(canonical))
            index += count
        reconstructed = reconstructed_values
    _require(
        reconstructed == selected.value,
        "recipe section reconstruction mismatch",
    )


def _verify_string_descriptors(
    selected: SelectedRecipeSection,
    pages: list[PlannedRecipeSectionPage],
) -> None:
    strategy = selected.definition.section_strategy
    value = selected.value
    if type(value) is not str:
        raise _pagination_error("string recipe section strategy requires a string")
    expected_format = selected.definition.ordinary_content_format
    range_prefix = "byte" if strategy == "raw" else "scalar_byte"
    total = len(value.encode("utf-8"))
    expected_start = 0
    for page in pages:
        descriptor = page.descriptor
        start = getattr(descriptor, f"{range_prefix}_start")
        end = getattr(descriptor, f"{range_prefix}_end")
        descriptor_total = getattr(descriptor, f"{range_prefix}_total")
        decoded = (
            page.content
            if strategy == "raw"
            else _decode(
                page.content,
                "recipe section string page is not independently decodable",
            )
        )
        _require(
            descriptor.content_format == expected_format,
            "recipe section page format changed during finalization",
        )
        if type(decoded) is not str:
            raise _pagination_error("recipe section string page is not independently decodable")
        if (
            type(start) is not int
            or type(end) is not int
            or start != expected_start
            or end < start
        ):
            raise _pagination_error("recipe section string ranges are not contiguous")
        _require(
            end > start or (total == 0 and len(pages) == 1),
            "recipe section string page makes no progress",
        )
        _require(
            descriptor_total == total
            and end <= total
            and end - start == len(decoded.encode("utf-8")),
            "recipe section string range does not match its content",
        )
        expected_start = end
    _require(
        expected_start == total,
        "recipe section string ranges do not reconstruct the section",
    )


def _verify_array_descriptors(
    selected: SelectedRecipeSection,
    pages: list[PlannedRecipeSectionPage],
) -> None:
    values = selected.value
    if type(values) is not list:
        raise _pagination_error("array recipe section strategy requires a list")
    expected_element = 0
    fragment_chunks: list[str] = []
    fragment_count: int | None = None
    fragment_total: int | None = None
    fragment_digest: str | None = None
    fragment_end = 0
    for page in pages:
        descriptor = page.descriptor
        if descriptor.content_format == selected.definition.ordinary_content_format:
            _require(
                fragment_count is None,
                "complete array page interrupts an element fragment",
            )
            decoded = _decode(page.content, "array page is not independently decodable")
            if type(decoded) is not list:
                raise _pagination_error("array page is not independently decodable")
            element_end = descriptor.element_end
            if (
                descriptor.element_start != expected_element
                or type(element_end) is not int
                or element_end < expected_element
            ):
                raise _pagination_error("array element ranges are not contiguous")
            _require(
                element_end > expected_element or (not values and len(pages) == 1),
                "array page makes no progress",
            )
            _require(
                descriptor.element_total == len(values)
                and element_end <= len(values)
                and element_end - expected_element == len(decoded),
                "array range does not match its content",
            )
            expected_element = element_end
            continue

        _require(
            descriptor.content_format == selected.definition.oversized_content_format,
            "array plan contains an unknown page format",
        )
        decoded_fragment = _decode(
            page.content,
            "array element fragment is not independently decodable",
        )
        if type(decoded_fragment) is not str:
            raise _pagination_error("array element fragment is not independently decodable")
        _require(
            descriptor.element_index == expected_element and expected_element < len(values),
            "array element fragment ordering is not contiguous",
        )
        if fragment_count is None:
            next_count = descriptor.fragment_count
            next_total = descriptor.fragment_byte_total
            if (
                descriptor.fragment_index != 0
                or type(next_count) is not int
                or next_count <= 0
                or type(next_total) is not int
                or next_total <= 0
                or not isinstance(descriptor.element_sha256, str)
            ):
                raise _pagination_error("array element fragment identity is invalid")
            fragment_count = next_count
            fragment_total = next_total
            fragment_digest = descriptor.element_sha256
            fragment_end = 0
        if fragment_total is None:
            raise _pagination_error("array element fragment identity is invalid")
        _require(
            descriptor.fragment_count == fragment_count
            and descriptor.fragment_byte_total == fragment_total
            and descriptor.element_sha256 == fragment_digest
            and descriptor.fragment_index == len(fragment_chunks),
            "array element fragment identity changed",
        )
        fragment_byte_end = descriptor.fragment_byte_end
        if (
            descriptor.fragment_byte_start != fragment_end
            or type(fragment_byte_end) is not int
            or fragment_byte_end <= fragment_end
        ):
            raise _pagination_error("array element fragment ranges are not contiguous")
        _require(
            fragment_byte_end - fragment_end == len(decoded_fragment.encode("utf-8"))
            and fragment_byte_end <= fragment_total,
            "array element fragment range does not match its content",
        )
        fragment_chunks.append(decoded_fragment)
        fragment_end = fragment_byte_end
        if len(fragment_chunks) == fragment_count:
            canonical_element = "".join(fragment_chunks)
            reconstructed_element = _decode(
                canonical_element,
                "array element fragments do not reconstruct their element",
            )
            _require(
                fragment_end == fragment_total
                and recipe_section_element_digest(reconstructed_element) == fragment_digest
                and reconstructed_element == values[expected_element],
                "array element fragments do not reconstruct their element",
            )
            expected_element += 1
            fragment_chunks = []
            fragment_count = None
            fragment_total = None
            fragment_digest = None
            fragment_end = 0
    _require(
        fragment_count is None and expected_element == len(values),
        "array ranges do not reconstruct the section",
    )


def verify_finalized_recipe_section_plan(
    *,
    selected: SelectedRecipeSection,
    generation: RecipeArtifactGeneration,
    pages: list[PlannedRecipeSectionPage],
    rendered_pages: tuple[str, ...],
    page_plan_sha256: str,
    bound_bytes: int,
    pagination_version: int,
    pagination_policy_sha256: str,
    section_registry_sha256: str,
    section_sha256: str,
    char_ceiling: int | None = None,
) -> None:
    """Validate finalized descriptors and renderings after digest injection."""
    _require(bool(pages), "recipe section plan has no pages")
    strategy = selected.definition.section_strategy
    if strategy in {"raw", "scalar"}:
        _verify_string_descriptors(selected, pages)
    else:
        _require(strategy == "array", "unknown recipe section strategy")
        _verify_array_descriptors(selected, pages)
    _verify_reconstruction(selected, pages)
    _require(
        len(rendered_pages) == len(pages),
        "final recipe section rendering count changed",
    )
    for part, (page, rendered) in enumerate(zip(pages, rendered_pages, strict=True)):
        _require(
            len(rendered.encode("utf-8")) <= bound_bytes,
            "final recipe section page exceeds captured bound",
        )
        if char_ceiling is not None:
            _require(
                client_serialized_char_len(rendered).value <= char_ceiling,
                "final recipe section page exceeds captured char ceiling",
            )
        response = _decode(rendered, "final recipe section page is not valid JSON")
        if type(response) is not dict:
            raise _pagination_error("final recipe section page is not a JSON object")
        terminal = part + 1 == len(pages)
        expected_ranges = page.descriptor.wire_ranges()
        actual_ranges = {
            name: response[name] for name in RECIPE_SECTION_PAGE_RANGE_FIELDS if name in response
        }
        if page.descriptor.content_format == "json-array-page":
            expected_content = json.loads(page.content)
            if selected.section == "flow_records":
                parsed_expected: list[object] = []
                for elem in expected_content:
                    if isinstance(elem, str):
                        try:
                            obj = json.loads(elem)
                        except (json.JSONDecodeError, TypeError):
                            parsed_expected.append(elem)
                            continue
                        parsed_expected.append(obj if isinstance(obj, dict) else elem)
                    else:
                        parsed_expected.append(elem)
                expected_content = parsed_expected
            content_matches = response.get("content") == expected_content
        else:
            content_matches = response.get("content") == page.content
        _require(
            response.get("success") is True
            and response.get("pagination_version") == pagination_version
            and response.get("pagination_policy_sha256") == pagination_policy_sha256
            and response.get("section_registry_sha256") == section_registry_sha256
            and response.get("section") == selected.section
            and response.get("section_sha256") == section_sha256
            and response.get("page_plan_sha256") == page_plan_sha256
            and response.get("producer_tool") == generation.producer_tool
            and response.get("recipe_name") == generation.recipe_name
            and response.get("descriptor_version") == generation.descriptor_version
            and response.get("schema_version") == generation.schema_version
            and response.get("payload_sha256") == generation.payload_sha256
            and response.get("artifact_blob_sha256") == generation.artifact_blob_sha256
            and response.get("artifact_blob_size_bytes") == generation.artifact_blob_size_bytes
            and response.get("body_sha256") == generation.body_sha256
            and response.get("body_size_bytes") == generation.body_size_bytes
            and response.get("flow_schema_version") == generation.flow_schema_version
            and response.get("flow_sha256") == generation.flow_sha256
            and response.get("flow_size_bytes") == generation.flow_size_bytes
            and response.get("flow_record_count") == generation.flow_record_count
            and response.get("initialization_id") == selected.initialization_id
            and response.get("part") == part
            and response.get("total_parts") == len(pages)
            and response.get("has_more") is not terminal
            and response.get("content_format") == page.descriptor.content_format
            and content_matches
            and actual_ranges == expected_ranges,
            "final recipe section rendering changed plan identity or boundaries",
        )
        _require(
            _content_digest(page.content) == page.descriptor.page_content_sha256,
            "recipe section page content digest changed",
        )
        _require(
            (
                response.get("next_part") == part + 1
                and isinstance(response.get("continuation"), str)
                and response["continuation"].startswith("sha256:")
            )
            if not terminal
            else "next_part" not in response and "continuation" not in response,
            "recipe section continuation metadata is inconsistent",
        )
