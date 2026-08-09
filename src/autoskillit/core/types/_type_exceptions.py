"""Exception types for recipe loading failures."""

from __future__ import annotations

__all__ = [
    "BoundedDeliveryRoundTripBudgetExceededError",
    "CapabilityNotSupportedError",
    "PluginArtifactContentionError",
    "PluginArtifactPublicationError",
    "PluginArtifactUnavailableError",
    "PluginArtifactValidationError",
    "SkillContractError",
    "RecipeLoadError",
    "ProcessStaleError",
    "RecipeDeliveryBudgetError",
    "RecipeExemptionFitnessError",
    "RecipeNotFoundError",
]


class RecipeLoadError(Exception):
    """Base exception for load_and_validate failures."""


class ProcessStaleError(RecipeLoadError):
    """MCP server process is running stale code — restart required."""


class RecipeNotFoundError(RecipeLoadError):
    """Named recipe could not be found in any scan directory."""


class RecipeDeliveryBudgetError(RuntimeError):
    """A compiled recipe delivery cannot satisfy its declared byte/call budget."""


class RecipeExemptionFitnessError(RecipeDeliveryBudgetError):
    """An inline recipe has drifted too close to its registered exemption ceiling."""

    def __init__(
        self,
        *,
        recipe: str,
        surface: str,
        rendered_bytes: int,
        ceiling_bytes: int,
        margin_bytes: int,
    ) -> None:
        self.recipe = recipe
        self.surface = surface
        self.rendered_bytes = rendered_bytes
        self.ceiling_bytes = ceiling_bytes
        self.margin_bytes = margin_bytes
        super().__init__(
            f"{recipe}/{surface}: rendered delivery is {rendered_bytes} bytes; "
            f"the {ceiling_bytes}-byte exemption requires {margin_bytes} bytes of margin"
        )


class BoundedDeliveryRoundTripBudgetExceededError(RecipeDeliveryBudgetError):
    """A compiled bounded delivery needs more MCP calls than its fixed budget."""

    def __init__(
        self,
        *,
        recipe: str,
        backend: str,
        planned_calls: int,
        budget: int,
    ) -> None:
        self.recipe = recipe
        self.backend = backend
        self.planned_calls = planned_calls
        self.budget = budget
        super().__init__(
            f"{recipe}/{backend}: compiled bounded delivery needs {planned_calls} calls; "
            f"budget is {budget}"
        )


class CapabilityNotSupportedError(Exception):
    """Backend does not support the requested capability."""

    def __init__(self, capability: str, backend_name: str) -> None:
        self.capability = capability
        self.backend_name = backend_name
        super().__init__(f"{backend_name!r} does not support capability {capability!r}")


class SkillContractError(ValueError):
    """A skill machine contract is malformed or exceeds its execution role."""


class PluginArtifactContentionError(RuntimeError):
    """A plugin artifact cannot be read or mutated while another owner holds it."""


class PluginArtifactPublicationError(RuntimeError):
    """A complete plugin artifact incarnation could not be published."""


class PluginArtifactUnavailableError(RuntimeError):
    """A plugin artifact could not be read because of a retryable filesystem error."""


class PluginArtifactValidationError(RuntimeError):
    """A plugin artifact failed exact identity or content validation."""
