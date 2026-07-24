"""Shared contracts for recipe-section planning and final verification."""

from __future__ import annotations

from dataclasses import dataclass

from autoskillit.core import RecipeSectionDef
from autoskillit.server._recipe_delivery import RecipeArtifactGeneration


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
