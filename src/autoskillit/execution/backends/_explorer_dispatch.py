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
    "1. Projection and static applicability make tasks available only; rendered marker or "
    "call count is never a spawn obligation.\n"
    "2. The parent sets selected_exploration_task_ids to the relevant and affordable "
    "dependency-ready task IDs after reserving synthesis/report/validation context and "
    "checking current child capacity.\n"
    "3. Submit only selected typed task packets to the deterministic exploration router and "
    "merge API; reclassify newly discovered cross-leaf frontiers explicitly and never let "
    "leaves spawn peers.\n"
    "4. Run only selected, scope-disjoint, dependency-ready tasks concurrently and keep "
    "dependency chains sequential.\n"
    "5. For each join-required wave, declare one batch through the same gateway as the join "
    "contract, named Agent calls without name/team_name/run_in_background, and release "
    "follow-up effects only after every expected direct tool_use_id is settled. Preserve "
    "conflicts and unresolved frontiers, then merge evidence. Retain final synthesis and "
    "every artifact or repository write in the parent session.\n"
    "6. Join every dispatched leaf through the same declared batch gateway before synthesis."
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
            "Follow the parent routing contract in the exploration dispatch preamble above.",
            "Task:",
            vector.body,
        )
    )


@dataclass(frozen=True, slots=True)
class _NativeExplorationDispatchRenderer:
    conventions: ExplorationDispatchConventions

    def _native_call(
        self,
        definition: AgentDef,
        prompt: str,
    ) -> str:
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
        assignment_labels: dict[str, str] = {}
        for index, vector in enumerate(migrated):
            assert vector.role is not None
            definition = definitions[vector.role]
            definition_digest = agent_definition_digest(definition)
            assignment_label = f"explorer-{vector.task.task_id}"
            prompt = _task_prompt(
                vector,
                router_plan_digest=plan.digest,
                role_definition_digest=definition_digest,
                launch_context_ref=launch_context_ref,
            )
            native_call = self._native_call(
                definition,
                prompt,
            )
            task_id = vector.task.task_id
            replacements[vector.id] = (
                f"Candidate exploration task {task_id!r}:\n"
                f"Resolved vector assignment label: {assignment_label!r} (index {index}).\n"
                f"Execute if and only if {task_id!r} is in "
                "selected_exploration_task_ids:\n"
                f"{native_call}\n"
                "Otherwise skip this candidate; omission from the parent-selected ready set "
                "is not a failure."
            )
            definition_digests[vector.id] = definition_digest
            assignment_labels[vector.id] = assignment_label
        context_ref = launch_context_ref or "runtime-bound"
        provisioning = (
            f"\n\n{self.conventions.provisioning_preamble}"
            if self.conventions.provisioning_preamble
            else ""
        )
        preamble = (
            f"{_PARENT_ROUTING_INSTRUCTIONS}{provisioning}\n\n"
            f"Shared exploration task constants:\n"
            f"profile: {migrated[0].profile.value}\n"
            f"depends_on: none\n"
            f"scope: {','.join(migrated[0].task.scope) or 'repository'}\n"
            f"launch_context_ref: {context_ref}\n"
            f"resolved_assignment_labels: {sorted(assignment_labels.values())}"
        )
        return ExplorationDispatchMaterialization(
            replacements=replacements,
            router_plan_digest=plan.digest,
            role_definition_digests=definition_digests,
            preamble=preamble,
            launch_context_ref=launch_context_ref,
        )


CLAUDE_EXPLORATION_DISPATCH_RENDERER = _NativeExplorationDispatchRenderer(
    ExplorationDispatchConventions(
        launcher="Agent",
        role_argument="subagent_type",
        description_argument="description",
        message_argument="prompt",
        role_prefix="autoskillit:",
        provisioning_preamble=(
            "Before dispatching explorer subagents, call enable_exploration() to "
            "establish session-scoped exploration authority. The three broker tools "
            "(submit_exploration_query, get_exploration_page, resume_exploration_context) "
            "become visible only after enable_exploration succeeds."
        ),
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
