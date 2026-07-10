"""IL-0 kitchen lifecycle and identity separation types.

Step 3.2 / 3.7 of #4185. Defines the typed lifecycle union and the
distinct identity values the kitchen publication lifecycle must keep
separate:

- :class:`KitchenLifecycleState` — the union of three frozen
  values: :class:`Closed`, :class:`OpenEmpty`, :class:`OpenRecipe`.
  Exactly one of these is the lifecycle at any point in time;
  ToolContext owns the value through a protocol-typed service.
- :class:`LifecycleGeneration` — monotonic counter that increases
  on every successful publication. CAS compare-and-publish uses
  the expected-vs-current generation pair.
- :class:`CampaignId`, :class:`DispatchId`, :class:`PipelineScopeId`,
  :class:`ExecutionLeaseId` — typed identities that compose with
  the Step 2 :class:`KitchenInstanceId` but never substitute for
  one another. ``campaign_id = tool_ctx.kitchen_id`` is forbidden.

Lifecycle publication (Step 3.4) stages generation-tagged
replacements and undo data while prior state remains authoritative,
revalidates CAS, applies reversible projections, and performs a
no-fail authority swap. On any failure the publisher restores exact
prior state before releasing the lock.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "Closed",
    "OpenEmpty",
    "OpenRecipe",
    "KitchenLifecycleState",
    "LifecycleGeneration",
    "CampaignId",
    "DispatchId",
    "PipelineScopeId",
    "ExecutionLeaseId",
    "FreeFormSkillScope",
    "RecipeStepKey",
]


@dataclass(frozen=True, slots=True)
class Closed:
    """Kitchen is not open. Initial state and post-close target."""

    reason: str = ""

    def __post_init__(self) -> None:
        # reason may be empty for ordinary close; preserved for diagnostics.
        pass


@dataclass(frozen=True, slots=True)
class OpenEmpty:
    """Kitchen is open but no recipe is published.

    Used for ad-hoc tool routing (e.g. fleet preflight) where a
    server boot has reserved the lifecycle but no recipe has been
    compiled and published yet.
    """

    kitchen_instance_id: Any  # KitchenInstanceId
    opened_at: float

    def __post_init__(self) -> None:
        if self.opened_at <= 0:
            raise ValueError("OpenEmpty.opened_at must be positive")


@dataclass(frozen=True, slots=True)
class OpenRecipe:
    """Kitchen is open with a published recipe compilation."""

    kitchen_instance_id: Any  # KitchenInstanceId
    recipe_name: str
    recipe_kind: str
    recipe_version: str
    compilation_key_fingerprint: str
    opened_at: float

    def __post_init__(self) -> None:
        if not self.recipe_name:
            raise ValueError("OpenRecipe.recipe_name must be non-empty")
        if not self.recipe_kind:
            raise ValueError("OpenRecipe.recipe_kind must be non-empty")
        if not self.recipe_version:
            raise ValueError("OpenRecipe.recipe_version must be non-empty")
        if not self.compilation_key_fingerprint:
            raise ValueError("OpenRecipe.compilation_key_fingerprint must be non-empty")
        if self.opened_at <= 0:
            raise ValueError("OpenRecipe.opened_at must be positive")


KitchenLifecycleState = Closed | OpenEmpty | OpenRecipe
"""Union of all legal lifecycle values for one kitchen."""


@dataclass(frozen=True, slots=True)
class LifecycleGeneration:
    """Monotonic generation counter for the kitchen lifecycle.

    Generation increases on every successful publication and never
    decreases. Compare-and-publish (CAS) compares the expected
    generation against the current generation atomically.
    """

    value: int

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError("LifecycleGeneration.value must be non-negative")

    def __hash__(self) -> int:
        return hash((self.value,))

    @classmethod
    def initial(cls) -> LifecycleGeneration:
        """Return the initial generation (0)."""
        return cls(value=0)

    def next(self) -> LifecycleGeneration:
        """Return the next generation value (one higher than this)."""
        return LifecycleGeneration(value=self.value + 1)


@dataclass(frozen=True, slots=True)
class CampaignId:
    """Typed fleet campaign identity.

    Distinct from KitchenInstanceId. Sourced exclusively from
    canonical fleet session/state — never assigned from
    ``tool_ctx.kitchen_id``.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("CampaignId.value must be non-empty")

    def __hash__(self) -> int:
        return hash((self.value,))


@dataclass(frozen=True, slots=True)
class DispatchId:
    """Typed fleet dispatch identity (preserved separately from CampaignId)."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("DispatchId.value must be non-empty")

    def __hash__(self) -> int:
        return hash((self.value,))


@dataclass(frozen=True, slots=True)
class PipelineScopeId:
    """Typed pipeline-scope identity.

    Resolved exactly once at admission as
    ``RunSkillRequest.order_id or AUTOSKILLIT_DISPATCH_ID`` and
    propagated unchanged through every lock bucket, tracker path,
    epoch snapshot, lease, receipt/resume comparison, executor
    order_id, and token/timing/audit attribution.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("PipelineScopeId.value must be non-empty")

    def __hash__(self) -> int:
        return hash((self.value,))


@dataclass(frozen=True, slots=True)
class ExecutionLeaseId:
    """Typed execution-lease identity.

    Lifecycle and precondition authorities hold exclusive leases
    identified by this value; recipe-bound calls share the lease
    with the executor through completion.
    """

    value: str
    kitchen_instance_id: Any  # KitchenInstanceId

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("ExecutionLeaseId.value must be non-empty")

    def __hash__(self) -> int:
        return hash((self.value, self.kitchen_instance_id))


@dataclass(frozen=True, slots=True)
class RecipeStepKey:
    """Discriminating key for one recipe step's lock/tracker epochs.

    Recipe-bound calls use this exact key; free-form calls use the
    distinct :class:`FreeFormSkillScope` (whose recipe lock/tracker
    epochs are explicitly non-applicable). Never suffix-match a
    step name against this key — equality is structural.
    """

    recipe_name: str
    step_name: str
    pipeline_scope: PipelineScopeId

    def __post_init__(self) -> None:
        if not self.recipe_name:
            raise ValueError("RecipeStepKey.recipe_name must be non-empty")
        if not self.step_name:
            raise ValueError("RecipeStepKey.step_name must be non-empty")

    def __hash__(self) -> int:
        return hash((self.recipe_name, self.step_name, self.pipeline_scope.value))


@dataclass(frozen=True, slots=True)
class FreeFormSkillScope:
    """Distinct scope for free-form (non-recipe-bound) skill calls.

    Recipe lock/tracker epochs are explicitly non-applicable for
    free-form calls. The publisher must never synthesize a
    :class:`RecipeStepKey` for a free-form call — that would
    incorrectly bypass the precondition epoch checks.
    """

    skill_command: str
    pipeline_scope: PipelineScopeId

    def __post_init__(self) -> None:
        if not self.skill_command:
            raise ValueError("FreeFormSkillScope.skill_command must be non-empty")

    def __hash__(self) -> int:
        return hash((self.skill_command, self.pipeline_scope.value))


# Sentinel: the SHA-256 prefix used by tests/canary code that needs
# to materialize a stable generation-0 fingerprint without invoking
# the compiler.
GENERATION_ZERO_FINGERPRINT_PREFIX: Final[str] = "gen0"
GENERATION_ZERO_FINGERPRINT: Final[str] = hashlib.sha256(
    GENERATION_ZERO_FINGERPRINT_PREFIX.encode("utf-8")
).hexdigest()
