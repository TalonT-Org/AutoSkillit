"""Strict deterministic routing and cross-leaf handoff reclassification."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from autoskillit.core import (
    ExplorationApplicability,
    ExplorationRouterPlan,
    ExplorationTaskSpec,
    FrontierItem,
    ProfileActivation,
    RepositoryProfileId,
    RepositorySnapshot,
)

from ._deterministic import DeterministicGraphError, ScheduledWave, stable_kahn_waves


def reclassify_cross_leaf(
    items: Iterable[FrontierItem], *, handoffs: Mapping[str, RepositoryProfileId]
) -> tuple[FrontierItem, ...]:
    """Adapt handoffs at the parent boundary while preserving work identity and dependencies."""

    item_list = tuple(items)
    item_ids = {item.item_id for item in item_list}
    unknown = set(handoffs).difference(item_ids)
    if unknown:
        raise DeterministicGraphError(f"handoffs name unknown frontier items: {sorted(unknown)!r}")
    return tuple(
        FrontierItem(
            item_id=item.item_id,
            query=item.query,
            profile=handoffs.get(item.item_id, item.profile),
            depends_on=item.depends_on,
            scope=item.scope,
        )
        for item in sorted(item_list, key=lambda item: item.item_id)
    )


def route_frontier(
    snapshot: RepositorySnapshot,
    items: Iterable[FrontierItem],
    activations: Iterable[ProfileActivation],
) -> ExplorationRouterPlan:
    """Build a plan only when every task is explicitly applicable to this repository."""

    activation_list = tuple(activations)
    activation_by_profile = {activation.profile: activation for activation in activation_list}
    if len(activation_by_profile) != len(activation_list):
        raise DeterministicGraphError("profile activation is ambiguous")
    item_list = tuple(items)
    stable_kahn_waves(
        item_list,
        key=lambda item: item.item_id,
        dependencies=lambda item: item.depends_on,
        scope=lambda item: item.scope,
    )
    tasks: list[ExplorationTaskSpec] = []
    for item in sorted(item_list, key=lambda item: item.item_id):
        if item.query.required_profiles and item.profile not in item.query.required_profiles:
            raise DeterministicGraphError(
                f"profile {item.profile!s} is outside query scope for {item.item_id!r}"
            )
        activation = activation_by_profile.get(item.profile)
        if (
            activation is None
            or activation.applicability is not ExplorationApplicability.APPLICABLE
        ):
            raise DeterministicGraphError(
                f"profile {item.profile!s} is not applicable for {item.item_id!r}"
            )
        tasks.append(
            ExplorationTaskSpec(
                task_id=item.item_id,
                frontier_item_id=item.item_id,
                profile=item.profile,
                depends_on=item.depends_on,
                scope=item.scope,
            )
        )
    return ExplorationRouterPlan(
        snapshot=snapshot,
        tasks=tuple(tasks),
        activations=tuple(sorted(activation_list, key=lambda activation: activation.profile)),
    )


def readiness_waves(plan: ExplorationRouterPlan) -> tuple[ScheduledWave[str], ...]:
    """Return sorted Kahn readiness waves with scope-disjoint concurrent tasks."""

    return stable_kahn_waves(
        plan.tasks,
        key=lambda task: task.task_id,
        dependencies=lambda task: task.depends_on,
        scope=lambda task: task.scope,
    )
