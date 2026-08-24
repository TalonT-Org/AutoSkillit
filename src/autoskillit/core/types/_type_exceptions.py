"""Exception types for recipe loading failures."""

from __future__ import annotations

from ._type_enums import ExplorationFailureCode
from ._type_exploration import SnapshotCaptureReason, SnapshotCaptureStatus

__all__ = [
    "BoundedDeliveryRoundTripBudgetExceededError",
    "CapabilityNotSupportedError",
    "ChildSpawnCardinalityError",
    "ExplorationBindingFailed",
    "InfrastructureFaultError",
    "PluginArtifactContentionError",
    "PluginArtifactPublicationError",
    "PluginArtifactUnavailableError",
    "PluginArtifactValidationError",
    "SkillContractError",
    "SnapshotUnavailable",
    "RecipeLoadError",
    "ProcessStaleError",
    "RecipeDeliveryBudgetError",
    "RecipeExemptionFitnessError",
    "RecipeNotFoundError",
    "StaleGeneratorError",
]


class SnapshotUnavailable(Exception):
    """A repository snapshot capture did not reach a complete, publishable state.

    Carries the same status/reason vocabulary ``exploration/snapshot.py`` emits
    so a caller two layers removed from the capture itself can still
    distinguish a truncation from a stale race from a hard failure, instead of
    parsing a formatted diagnostic string.
    """

    def __init__(
        self,
        status: SnapshotCaptureStatus,
        reason: SnapshotCaptureReason | None,
        detail: str,
    ) -> None:
        if status is SnapshotCaptureStatus.COMPLETE:
            raise ValueError("SnapshotUnavailable must not be raised for a COMPLETE status")
        if reason is None:
            raise ValueError("a non-COMPLETE SnapshotUnavailable must carry a reason")
        self.status = status
        self.reason = reason
        self.detail = detail
        super().__init__(f"{status}: {reason}: {detail}")


class InfrastructureFaultError(Exception):
    """Marker base for faults that are a property of the environment, not the work.

    Raised when the package install, a plugin artifact, or a process's own
    identity has been invalidated by something outside the running attempt —
    the install root was replaced, an artifact is contended, a filesystem read
    was transiently unavailable — never because the work being attempted was
    wrong. gRPC's ``FAILED_PRECONDITION`` states the same split canonically:
    "the client should not retry until the system state has been explicitly
    fixed." That is this error's contract, and why classifying it sets
    ``needs_retry=False`` rather than treating it as a logic crash.

    Derives from ``Exception`` only — never ``RuntimeError`` or ``OSError`` —
    so that joining this marker onto an existing hierarchy (the four
    ``PluginArtifact*Error`` classes are ``RuntimeError`` subclasses,
    ``ProcessStaleError`` is a ``RecipeLoadError`` subclass) never widens which
    pre-existing ``except RuntimeError``/``except OSError`` handlers catch it.

    Deliberately NOT marked: ``PluginArtifactValidationError`` (artifact
    content is corrupt — a genuine integrity fault with its own self-heal
    path) and ``PluginArtifactPublicationError`` (a publish failure may
    indicate a real defect, not an environment fault).
    """


class RecipeLoadError(Exception):
    """Base exception for load_and_validate failures."""


class ProcessStaleError(RecipeLoadError, InfrastructureFaultError):
    """MCP server process is running stale code — restart required."""


class StaleGeneratorError(InfrastructureFaultError):
    """The generating process's installation is stale or deleted."""


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
        backend: str,
        rendered_bytes: int,
        ceiling_bytes: int,
        margin_bytes: int,
    ) -> None:
        self.recipe = recipe
        self.surface = surface
        self.backend = backend
        self.rendered_bytes = rendered_bytes
        self.ceiling_bytes = ceiling_bytes
        self.margin_bytes = margin_bytes
        super().__init__(
            f"{recipe}/{surface}/{backend}: rendered delivery is {rendered_bytes} bytes; "
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


class ChildSpawnCardinalityError(SkillContractError):
    """A child spawn does not declare exactly one valid cardinality authority."""


class ExplorationBindingFailed(SkillContractError):
    """An explorer child's launch binding could not be minted for a named cause.

    Carries the same ``ExplorationFailureCode``/``SnapshotCaptureReason``
    vocabulary the Claude-native ``enable_exploration`` tool returns, so the
    Codex terminal-explorer path can report a named failure instead of
    degrading to an untyped crash.
    """

    def __init__(
        self,
        code: ExplorationFailureCode,
        reason: SnapshotCaptureReason | None,
        detail: str,
    ) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}: {detail}")


class PluginArtifactContentionError(RuntimeError, InfrastructureFaultError):
    """A plugin artifact cannot be read or mutated while another owner holds it."""


class PluginArtifactPublicationError(RuntimeError):
    """A complete plugin artifact incarnation could not be published."""


class PluginArtifactUnavailableError(RuntimeError, InfrastructureFaultError):
    """A plugin artifact could not be read because of an unavailable filesystem.

    Not retryable in-process: whatever the filesystem might do on a second
    attempt, the process that observed the fault cannot safely continue while
    its own install identity may be mid-replacement. The correct disposition
    is halt-and-report, not retry — see ``InfrastructureFaultError``.
    """


class PluginArtifactValidationError(RuntimeError):
    """A plugin artifact failed exact identity or content validation."""
