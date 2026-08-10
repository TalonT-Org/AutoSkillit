"""Pure kitchen-scoped recipe initialization lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TypeAlias

from autoskillit.core import (
    InstallationVersion,
    InstalledRecipeExecution,
    RecipeArtifactGeneration,
    RecipeExecutionSnapshot,
    RecipeFlowGeneration,
)

__all__ = [
    "InitializingRecipe",
    "NoActiveRecipe",
    "ReadyRecipe",
    "RecipeInitializationProgress",
    "RecipeInitializationRequirement",
    "RecipeInitializationState",
    "initialization_is_complete",
    "record_initialization_page",
    "replace_ready_execution",
    "start_recipe_initialization",
    "transition_recipe_ready",
]

_READY_RECIPE_TRANSITION_TOKEN = object()


@dataclass(frozen=True, slots=True)
class RecipeInitializationRequirement:
    """One immutable section page plan required before READY."""

    section: str
    page_plan_sha256: str
    total_parts: int
    compiled_bytes: int = 0

    def __post_init__(self) -> None:
        if not self.section or not self.page_plan_sha256:
            raise ValueError("recipe initialization requirement identity is incomplete")
        if type(self.total_parts) is not int or self.total_parts <= 0:
            raise ValueError("recipe initialization requirement must have positive parts")
        if type(self.compiled_bytes) is not int or self.compiled_bytes < 0:
            raise ValueError("recipe initialization compiled bytes must be non-negative")


@dataclass(frozen=True, slots=True)
class RecipeInitializationProgress:
    """Committed in-order progress for one required page plan."""

    section: str
    page_plan_sha256: str
    next_part: int
    total_parts: int

    def __post_init__(self) -> None:
        if not self.section or not self.page_plan_sha256:
            raise ValueError("recipe initialization progress identity is incomplete")
        if (
            type(self.next_part) is not int
            or type(self.total_parts) is not int
            or not 0 <= self.next_part <= self.total_parts
            or self.total_parts <= 0
        ):
            raise ValueError("recipe initialization progress range is invalid")


@dataclass(frozen=True, slots=True)
class NoActiveRecipe:
    """No named recipe currently owns execution authority."""


@dataclass(frozen=True, slots=True)
class InitializingRecipe:
    """A generation delivered successfully but not yet reconstructed."""

    kitchen_id: str
    recipe_name: str
    artifact_generation: RecipeArtifactGeneration
    flow_generation: RecipeFlowGeneration
    initialization_id: str
    staged_snapshot: RecipeExecutionSnapshot
    installation_version: InstallationVersion
    requirements: tuple[RecipeInitializationRequirement, ...]
    progress: tuple[RecipeInitializationProgress, ...]
    generation_store_key: str
    completion_receipt: str | None = None

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (
                self.kitchen_id,
                self.recipe_name,
                self.initialization_id,
                self.generation_store_key,
            )
        ):
            raise ValueError("recipe initialization identity is incomplete")
        if not isinstance(self.installation_version, InstallationVersion):
            raise ValueError("recipe initialization installation version is invalid")
        requirements = tuple(self.requirements)
        progress = tuple(self.progress)
        if len(requirements) != len(progress):
            raise ValueError("recipe initialization requirements and progress differ")
        requirement_keys = tuple((item.section, item.page_plan_sha256) for item in requirements)
        progress_keys = tuple((item.section, item.page_plan_sha256) for item in progress)
        if (
            len(requirement_keys) != len(set(requirement_keys))
            or requirement_keys != progress_keys
            or any(
                requirement.total_parts != current.total_parts
                for requirement, current in zip(requirements, progress, strict=True)
            )
        ):
            raise ValueError("recipe initialization page-plan identities differ")
        object.__setattr__(self, "requirements", requirements)
        object.__setattr__(self, "progress", progress)


@dataclass(frozen=True, slots=True)
class ReadyRecipe:
    """The sole authority for executing one immutable recipe generation."""

    kitchen_id: str
    recipe_name: str
    artifact_generation: RecipeArtifactGeneration
    flow_generation: RecipeFlowGeneration
    initialization_id: str
    installed_execution: InstalledRecipeExecution
    generation_store_key: str
    completion_receipt: str
    _transition_token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._transition_token is not _READY_RECIPE_TRANSITION_TOKEN:
            raise ValueError("ready recipe requires a completed initialization transition")
        if not all(
            isinstance(value, str) and value
            for value in (
                self.kitchen_id,
                self.recipe_name,
                self.initialization_id,
                self.generation_store_key,
                self.completion_receipt,
            )
        ):
            raise ValueError("ready recipe identity and completion receipt must be non-empty")
        if (
            self.artifact_generation.recipe_name != self.recipe_name
            or self.installed_execution.snapshot.recipe_name != self.recipe_name
        ):
            raise ValueError("ready recipe generation identity differs from installed snapshot")
        if (
            self.artifact_generation.flow_schema_version != self.flow_generation.schema_version
            or self.artifact_generation.flow_sha256 != self.flow_generation.flow_sha256
            or self.artifact_generation.flow_size_bytes != self.flow_generation.flow_size_bytes
            or self.artifact_generation.flow_record_count != self.flow_generation.record_count
        ):
            raise ValueError("ready recipe artifact and flow generation differ")


RecipeInitializationState: TypeAlias = NoActiveRecipe | InitializingRecipe | ReadyRecipe


def start_recipe_initialization(
    *,
    kitchen_id: str,
    recipe_name: str,
    artifact_generation: RecipeArtifactGeneration,
    flow_generation: RecipeFlowGeneration,
    initialization_id: str,
    staged_snapshot: RecipeExecutionSnapshot,
    installation_version: InstallationVersion,
    requirements: tuple[RecipeInitializationRequirement, ...],
    generation_store_key: str,
) -> InitializingRecipe:
    """Start a fresh generation and discard all prior authority."""
    progress = tuple(
        RecipeInitializationProgress(
            section=requirement.section,
            page_plan_sha256=requirement.page_plan_sha256,
            next_part=0,
            total_parts=requirement.total_parts,
        )
        for requirement in requirements
    )
    return InitializingRecipe(
        kitchen_id=kitchen_id,
        recipe_name=recipe_name,
        artifact_generation=artifact_generation,
        flow_generation=flow_generation,
        initialization_id=initialization_id,
        staged_snapshot=staged_snapshot,
        installation_version=installation_version,
        requirements=requirements,
        progress=progress,
        generation_store_key=generation_store_key,
    )


def record_initialization_page(
    state: RecipeInitializationState,
    *,
    initialization_id: str,
    section: str,
    page_plan_sha256: str,
    part: int,
) -> RecipeInitializationState:
    """Commit one exact in-order page; exact replay is idempotent."""
    if not isinstance(state, InitializingRecipe):
        raise ValueError("recipe initialization is not active")
    if initialization_id != state.initialization_id:
        raise ValueError("recipe initialization ID is stale or altered")
    progress = list(state.progress)
    index = next(
        (
            offset
            for offset, item in enumerate(progress)
            if item.section == section and item.page_plan_sha256 == page_plan_sha256
        ),
        None,
    )
    if index is None:
        raise ValueError("recipe initialization page plan is not required")
    if any(item.next_part != item.total_parts for item in progress[:index]):
        raise ValueError("recipe initialization sections are out of order")
    current = progress[index]
    if part < current.next_part:
        return state
    if part != current.next_part or part >= current.total_parts:
        raise ValueError("recipe initialization page is skipped or out of order")
    progress[index] = replace(current, next_part=current.next_part + 1)
    return replace(state, progress=tuple(progress))


def initialization_is_complete(state: RecipeInitializationState) -> bool:
    return isinstance(state, InitializingRecipe) and all(
        item.next_part == item.total_parts for item in state.progress
    )


def transition_recipe_ready(
    state: InitializingRecipe,
    *,
    installed_execution: InstalledRecipeExecution,
    completion_receipt: str,
) -> ReadyRecipe:
    """Transition a completely reconstructed generation to READY."""
    if not initialization_is_complete(state):
        raise ValueError("recipe initialization reconstruction is incomplete")
    if installed_execution.snapshot != state.staged_snapshot:
        raise ValueError("installed recipe snapshot differs from staged generation")
    if installed_execution.installation_version != state.installation_version:
        raise ValueError("installed recipe occurrence differs from staged generation")
    if not completion_receipt:
        raise ValueError("recipe initialization completion receipt is required")
    return ReadyRecipe(
        kitchen_id=state.kitchen_id,
        recipe_name=state.recipe_name,
        artifact_generation=state.artifact_generation,
        flow_generation=state.flow_generation,
        initialization_id=state.initialization_id,
        installed_execution=installed_execution,
        generation_store_key=state.generation_store_key,
        completion_receipt=completion_receipt,
        _transition_token=_READY_RECIPE_TRANSITION_TOKEN,
    )


def replace_ready_execution(
    state: RecipeInitializationState,
    installed_execution: InstalledRecipeExecution,
) -> RecipeInitializationState:
    """Replace runtime-only execution metadata without changing generation identity."""
    if not isinstance(state, ReadyRecipe):
        raise ValueError("recipe execution is not READY")
    if installed_execution.snapshot != state.installed_execution.snapshot:
        raise ValueError("replacement recipe execution crosses generations")
    if installed_execution.installation_version != state.installed_execution.installation_version:
        raise ValueError("replacement recipe execution crosses installation occurrences")
    return replace(state, installed_execution=installed_execution)
