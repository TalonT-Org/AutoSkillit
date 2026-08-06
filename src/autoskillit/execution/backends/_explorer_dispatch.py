"""Backend-native rendering for canonical exploration-vector router plans."""

from __future__ import annotations

import json
from dataclasses import dataclass

from autoskillit.core import (
    AgentDef,
    ExplorationDispatchConventions,
    ExplorationDispatchMaterialization,
    ExplorationRouterPlan,
    ExplorationVectorDef,
    ExplorationVectorDisposition,
    agent_definition_digest,
    load_bundled_agent_definitions,
)

_PARENT_ROUTING_INSTRUCTIONS = (
    "Parent routing contract:\n"
    "1. Submit this typed task packet to the deterministic exploration router and merge API.\n"
    "2. Reclassify every newly discovered cross-leaf frontier explicitly; do not let leaves "
    "spawn peers.\n"
    "3. Run only scope-disjoint ready tasks concurrently and keep dependency chains sequential.\n"
    "4. Wait for every dispatched leaf, preserve conflicts and unresolved frontiers, then "
    "merge evidence.\n"
    "5. Retain final synthesis and every artifact or repository write in the parent session."
)


def _canonical_definitions(vectors: tuple[ExplorationVectorDef, ...]) -> dict[str, AgentDef]:
    definitions = {definition.name: definition for definition in load_bundled_agent_definitions()}
    roles: set[str] = set()
    for vector in vectors:
        if vector.role is None:
            raise ValueError("native exploration dispatch requires a role for every vector")
        roles.add(vector.role)
    missing = roles.difference(definitions)
    if missing:
        raise ValueError(f"native exploration dispatch names unknown roles: {sorted(missing)!r}")
    return {role: definitions[role] for role in sorted(roles)}


def _task_prompt(
    vector: ExplorationVectorDef,
    *,
    router_plan_digest: str,
    role_definition_digest: str,
    launch_context_ref: str | None,
) -> str:
    task = vector.task
    relationships = ",".join(item.value for item in vector.relationship_classes)
    dependencies = ",".join(task.depends_on) or "none"
    scope = ",".join(task.scope) or "repository"
    context_ref = launch_context_ref or "runtime-bound"
    return "\n".join(
        (
            "AutoSkillit typed exploration task packet",
            f"router_plan_digest: {router_plan_digest}",
            f"role_definition_digest: {role_definition_digest}",
            f"vector_digest: {vector.digest}",
            f"launch_context_ref: {context_ref}",
            f"task_id: {task.task_id}",
            f"frontier_item_id: {task.frontier_item_id}",
            f"profile: {vector.profile.value}",
            f"relationship_classes: {relationships}",
            f"depends_on: {dependencies}",
            f"scope: {scope}",
            f"max_results: {vector.max_results}",
            f"max_report_bytes: {vector.max_report_bytes}",
            f"evidence_version: {vector.evidence_version}",
            "Task:",
            vector.body,
        )
    )


@dataclass(frozen=True, slots=True)
class _NativeExplorationDispatchRenderer:
    conventions: ExplorationDispatchConventions

    def _native_call(self, definition: AgentDef, prompt: str) -> str:
        role = f"{self.conventions.role_prefix}{definition.name}"
        arguments = [f"{self.conventions.role_argument}={json.dumps(role)}"]
        if self.conventions.description_argument is not None:
            arguments.append(
                f"{self.conventions.description_argument}={json.dumps(definition.description)}"
            )
        arguments.append(f"{self.conventions.message_argument}={json.dumps(prompt)}")
        return f"{self.conventions.launcher}({', '.join(arguments)})"

    def render(
        self,
        plan: ExplorationRouterPlan,
        vectors: tuple[ExplorationVectorDef, ...],
        *,
        launch_context_ref: str | None = None,
    ) -> ExplorationDispatchMaterialization:
        migrated = tuple(
            sorted(
                (
                    vector
                    for vector in vectors
                    if vector.disposition is ExplorationVectorDisposition.MIGRATED
                ),
                key=lambda vector: vector.task.task_id,
            )
        )
        if not migrated:
            raise ValueError("native exploration dispatch requires migrated vectors")
        if tuple(vector.task for vector in migrated) != plan.tasks:
            raise ValueError("native exploration vectors do not match the canonical router plan")
        definitions = _canonical_definitions(migrated)
        replacements: dict[str, str] = {}
        definition_digests: dict[str, str] = {}
        for vector in migrated:
            assert vector.role is not None
            definition = definitions[vector.role]
            definition_digest = agent_definition_digest(definition)
            prompt = _task_prompt(
                vector,
                router_plan_digest=plan.digest,
                role_definition_digest=definition_digest,
                launch_context_ref=launch_context_ref,
            )
            replacements[vector.id] = (
                f"{_PARENT_ROUTING_INSTRUCTIONS}\n\n{self._native_call(definition, prompt)}"
            )
            definition_digests[vector.id] = definition_digest
        return ExplorationDispatchMaterialization(
            replacements=replacements,
            router_plan_digest=plan.digest,
            role_definition_digests=definition_digests,
            launch_context_ref=launch_context_ref,
        )


CLAUDE_EXPLORATION_DISPATCH_RENDERER = _NativeExplorationDispatchRenderer(
    ExplorationDispatchConventions(
        launcher="Agent",
        role_argument="subagent_type",
        description_argument="description",
        message_argument="prompt",
        role_prefix="autoskillit:",
    )
)

CODEX_EXPLORATION_DISPATCH_RENDERER = _NativeExplorationDispatchRenderer(
    ExplorationDispatchConventions(
        launcher="spawn_agent",
        role_argument="agent_type",
        message_argument="message",
    )
)


__all__ = [
    "CLAUDE_EXPLORATION_DISPATCH_RENDERER",
    "CODEX_EXPLORATION_DISPATCH_RENDERER",
]
