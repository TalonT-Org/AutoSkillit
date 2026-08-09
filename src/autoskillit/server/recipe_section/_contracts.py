"""Shared contracts for recipe-section planning and final verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from autoskillit.core import (
    RECIPE_SECTION_CONTENT_FORMAT_REGISTRY,
    RECIPE_SECTION_RESPONSE_FLOOR_BYTES,
    RecipeArtifactGeneration,
    RecipeSectionDef,
)

RecipeSectionContentFormat = Literal[
    "raw-text",
    "json-scalar-page",
    "json-array-page",
    "json-element-fragment",
]
_RANGE_FIELDS_BY_FORMAT = {
    content_format: frozenset(definition.range_fields)
    for content_format, definition in RECIPE_SECTION_CONTENT_FORMAT_REGISTRY.items()
}
_RANGE_TRIPLE_BY_FORMAT = {
    "raw-text": ("byte_start", "byte_end", "byte_total"),
    "json-scalar-page": ("scalar_byte_start", "scalar_byte_end", "scalar_byte_total"),
    "json-array-page": ("element_start", "element_end", "element_total"),
    "json-element-fragment": (
        "fragment_byte_start",
        "fragment_byte_end",
        "fragment_byte_total",
    ),
}
RECIPE_SECTION_PAGE_RANGE_FIELDS = frozenset(
    field for range_fields in _RANGE_FIELDS_BY_FORMAT.values() for field in range_fields
)
_SHA256_PREFIX = "sha256:"
_LOWERCASE_HEX = frozenset("0123456789abcdef")


def _is_sha256_digest(value: object) -> bool:
    return (
        type(value) is str
        and value.startswith(_SHA256_PREFIX)
        and len(value) == len(_SHA256_PREFIX) + 64
        and all(character in _LOWERCASE_HEX for character in value[len(_SHA256_PREFIX) :])
    )


class RecipeSectionPaginationError(RuntimeError):
    """A verified immutable page plan could not be established."""


class RecipeSectionNonConvergenceError(RecipeSectionPaginationError):
    """Pagination width planning could not reach a stable state."""


class RecipeSectionBoundError(RecipeSectionPaginationError):
    """The captured request bound cannot fit one progress-making page."""


@dataclass(frozen=True, slots=True)
class RecipeSectionRequestState:
    """One captured request admission and byte-bound decision."""

    admitted: bool
    recipe_section_bound_bytes: int

    def __post_init__(self) -> None:
        """Reject bounds that cannot contain every mandatory failure response."""
        if (
            type(self.recipe_section_bound_bytes) is not int
            or self.recipe_section_bound_bytes < RECIPE_SECTION_RESPONSE_FLOOR_BYTES
        ):
            raise ValueError(
                "recipe section bound must be an integer at least "
                f"{RECIPE_SECTION_RESPONSE_FLOOR_BYTES} bytes"
            )


@dataclass(frozen=True, slots=True)
class SelectedRecipeSection:
    """A registry-selected section whose value remains typed."""

    section: str
    definition: RecipeSectionDef
    value: object
    present: bool
    initialization_id: str | None = None


@dataclass(frozen=True, slots=True)
class RecipeSectionPageDescriptor:
    """One immutable page boundary in a page-plan manifest."""

    content_format: RecipeSectionContentFormat
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

    def __post_init__(self) -> None:
        """Require a typed, ordered range family and valid content digests."""
        expected_fields = _RANGE_FIELDS_BY_FORMAT.get(self.content_format)
        if expected_fields is None:
            raise ValueError(f"unknown recipe section content format: {self.content_format!r}")
        populated_fields = {
            name for name in RECIPE_SECTION_PAGE_RANGE_FIELDS if getattr(self, name) is not None
        }
        if populated_fields != expected_fields:
            raise ValueError(
                "recipe section page descriptor range fields must exactly match content format"
            )
        if not _is_sha256_digest(self.page_content_sha256):
            raise ValueError("recipe section page content digest must be lowercase sha256")

        numeric_values: dict[str, int] = {}
        for name in expected_fields:
            value = getattr(self, name)
            if name.endswith("_sha256"):
                if not _is_sha256_digest(value):
                    raise ValueError(f"recipe section {name} must be a lowercase sha256 digest")
                continue
            if type(value) is not int or value < 0:
                raise ValueError(f"recipe section {name} must be a non-negative integer")
            numeric_values[name] = value

        start_name, end_name, total_name = _RANGE_TRIPLE_BY_FORMAT[self.content_format]
        start = numeric_values[start_name]
        end = numeric_values[end_name]
        total = numeric_values[total_name]
        if not start <= end <= total or (total > 0 and start == end):
            raise ValueError(
                "recipe section page range must make ordered progress within its total"
            )

        if self.content_format == "json-element-fragment":
            fragment_index = numeric_values["fragment_index"]
            fragment_count = numeric_values["fragment_count"]
            if fragment_count == 0 or fragment_index >= fragment_count:
                raise ValueError(
                    "recipe section fragment index must be within a positive fragment count"
                )

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
class PlannedRecipeSectionPage:
    """One descriptor paired with its exact independently decodable content."""

    descriptor: RecipeSectionPageDescriptor
    content: str


@dataclass(frozen=True, slots=True)
class RecipeSectionPlanManifest:
    """Sole non-self-referential preimage for one page-plan digest."""

    pagination_version: int
    section_registry_sha256: str
    pagination_policy_sha256: str
    generation: RecipeArtifactGeneration
    initialization_id: str | None
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

    @property
    def measured_bytes(self) -> int:
        """Return the exact UTF-8 size of the compiled wire pages."""
        return sum(len(page.encode("utf-8")) for page in self.rendered_pages)
