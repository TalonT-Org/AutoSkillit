"""Orchestrator system prompt builder — shared helpers and re-export hub.

Domain-specific prompt builders live in sibling modules:
- _prompts_campaign.py   — L3 campaign dispatcher prompt
- _prompts_orchestrator.py — L1/L2 cook session prompt
- _prompts_kitchen.py    — open-kitchen and fleet-dispatch prompts

This module owns shared helpers used by multiple siblings and re-exports
all public symbols so that existing ``from autoskillit.cli._prompts import X``
statements continue to work unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from autoskillit.core import SkillExecutionRole
from autoskillit.workspace import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillProjectionContext,
    parse_frontmatter_content,
    project_agent_skill_document,
)

# ── Shared helpers (used by sibling _prompts_*.py modules) ──────────────

_PRE_DISPATCH_SCOPE = "pre_dispatch"
_POST_RECEIPT_SCOPE = "post_receipt"


class McpStartupRecoveryEventKind(Enum):
    """Closed event vocabulary for deterministic startup-recovery traces."""

    PRE_DISPATCH_FAILURE = "pre_dispatch_failure"
    RETRY = "retry"
    EXHAUSTED = "exhausted"
    TOOL_ERROR_RESULT = "tool_error_result"
    APPLICATION_RESULT = "application_result"


@dataclass(frozen=True, slots=True)
class McpStartupRecoveryEvent:
    """One deterministic transition emitted by the startup policy reducer."""

    kind: McpStartupRecoveryEventKind
    attempt: int | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class _McpStartupRecoveryClause:
    clause_id: str
    scope: str
    actor: str
    modality: str
    polarity: str
    action: str
    object: str
    text: str

    def render(self) -> str:
        return f"[{self.clause_id}] {self.text}"


@dataclass(frozen=True, slots=True)
class _McpStartupRecoverySpec:
    clauses: tuple[_McpStartupRecoveryClause, ...]
    attempt_cap: int
    exhaustion_message: str

    def __post_init__(self) -> None:
        if self.attempt_cap <= 1:
            raise ValueError("MCP startup attempt cap must be greater than one")
        if not self.exhaustion_message.strip():
            raise ValueError("MCP startup exhaustion message must not be empty")
        clause_ids = tuple(clause.clause_id for clause in self.clauses)
        if len(clause_ids) != len(set(clause_ids)):
            raise ValueError("MCP startup clause IDs must be unique")
        for clause in self.clauses:
            if clause.scope not in {_PRE_DISPATCH_SCOPE, _POST_RECEIPT_SCOPE}:
                raise ValueError("MCP startup clause has an unknown scope")
            if clause.actor != "orchestrator":
                raise ValueError("MCP startup clause has an unknown actor")
            if clause.modality != "must":
                raise ValueError("MCP startup clause must use mandatory modality")
            if clause.polarity not in {"positive", "negative"}:
                raise ValueError("MCP startup clause has an unknown polarity")
            if not clause.action.strip() or not clause.object.strip():
                raise ValueError("MCP startup clause action and object must not be empty")
            if not clause.text.strip():
                raise ValueError("MCP startup clause text must not be empty")

    def render(self) -> str:
        pre_dispatch = "\n".join(
            clause.render() for clause in self.clauses if clause.scope == _PRE_DISPATCH_SCOPE
        )
        post_receipt = "\n".join(
            clause.render() for clause in self.clauses if clause.scope == _POST_RECEIPT_SCOPE
        )
        return (
            "MCP STARTUP RECOVERY — PRE-DISPATCH:\n"
            f"{pre_dispatch}\n"
            f"[MCP-PRE-ATTEMPT-CAP] Use a bounded retry with at most "
            f"{self.attempt_cap} total open_kitchen dispatch attempts. If attempt "
            f"{self.attempt_cap} fails before a CallToolResult exists, output exactly once: "
            f'"{self.exhaustion_message}" Then end the session.\n'
            "MCP STARTUP RECOVERY — POST-RECEIPT:\n"
            f"{post_receipt}"
        )

    def validate_rendered(self, rendered: str) -> tuple[str, ...]:
        pre_header = "MCP STARTUP RECOVERY — PRE-DISPATCH:"
        post_header = "MCP STARTUP RECOVERY — POST-RECEIPT:"
        if pre_header not in rendered or post_header not in rendered:
            return tuple(clause.clause_id for clause in self.clauses)
        pre_start = rendered.index(pre_header)
        post_start = rendered.index(post_header, pre_start)
        sections = {
            _PRE_DISPATCH_SCOPE: rendered[pre_start:post_start],
            _POST_RECEIPT_SCOPE: rendered[post_start:],
        }
        return tuple(
            clause.clause_id
            for clause in self.clauses
            if rendered.count(f"[{clause.clause_id}]") != 1
            or sections[clause.scope].count(clause.render()) != 1
        )

    def reduce_pre_dispatch_failure(self, attempt: int) -> McpStartupRecoveryEvent:
        if attempt < 1 or attempt > self.attempt_cap:
            raise ValueError("MCP startup attempt is outside the configured cap")
        if attempt < self.attempt_cap:
            return McpStartupRecoveryEvent(
                kind=McpStartupRecoveryEventKind.RETRY,
                attempt=attempt,
            )
        return McpStartupRecoveryEvent(
            kind=McpStartupRecoveryEventKind.EXHAUSTED,
            attempt=attempt,
            message=self.exhaustion_message,
        )

    def reduce_received_result(self, kind: McpStartupRecoveryEventKind) -> McpStartupRecoveryEvent:
        if kind not in {
            McpStartupRecoveryEventKind.TOOL_ERROR_RESULT,
            McpStartupRecoveryEventKind.APPLICATION_RESULT,
        }:
            raise ValueError("received-result reducer requires a received result event")
        return McpStartupRecoveryEvent(kind=kind)


_MCP_STARTUP_RECOVERY_SPEC = _McpStartupRecoverySpec(
    attempt_cap=3,
    exhaustion_message="AutoSkillit MCP server did not start — ending session.",
    clauses=(
        _McpStartupRecoveryClause(
            clause_id="MCP-PRE-UNIVERSAL-RETRY",
            scope=_PRE_DISPATCH_SCOPE,
            actor="orchestrator",
            modality="must",
            polarity="positive",
            action="retry",
            object="open_kitchen",
            text=(
                "For every failure before a CallToolResult exists, retry open_kitchen "
                "directly before classifying the failure shape."
            ),
        ),
        _McpStartupRecoveryClause(
            clause_id="MCP-PRE-NO-EXPLANATION",
            scope=_PRE_DISPATCH_SCOPE,
            actor="orchestrator",
            modality="must",
            polarity="negative",
            action="explain",
            object="startup failure",
            text="During bounded retry, do not explain the startup failure to the user.",
        ),
        _McpStartupRecoveryClause(
            clause_id="MCP-PRE-NO-TROUBLESHOOTING",
            scope=_PRE_DISPATCH_SCOPE,
            actor="orchestrator",
            modality="must",
            polarity="negative",
            action="troubleshoot",
            object="startup failure",
            text="During bounded retry, do not troubleshoot the startup failure.",
        ),
        _McpStartupRecoveryClause(
            clause_id="MCP-PRE-NO-FREE-TEXT-QUESTION",
            scope=_PRE_DISPATCH_SCOPE,
            actor="orchestrator",
            modality="must",
            polarity="negative",
            action="output",
            object="free-text question",
            text="During bounded retry, do not output a free-text question.",
        ),
        _McpStartupRecoveryClause(
            clause_id="MCP-PRE-NO-ASK-USER",
            scope=_PRE_DISPATCH_SCOPE,
            actor="orchestrator",
            modality="must",
            polarity="negative",
            action="call",
            object="AskUserQuestion",
            text="During bounded retry, do not call AskUserQuestion.",
        ),
        _McpStartupRecoveryClause(
            clause_id="MCP-POST-BOUNDARY",
            scope=_POST_RECEIPT_SCOPE,
            actor="orchestrator",
            modality="must",
            polarity="positive",
            action="end",
            object="pre-dispatch recovery",
            text=(
                "Receiving any CallToolResult ends PRE-DISPATCH recovery. Do not return "
                "to discovery or start a fresh operation."
            ),
        ),
        _McpStartupRecoveryClause(
            clause_id="MCP-POST-TOOL-ERROR",
            scope=_POST_RECEIPT_SCOPE,
            actor="orchestrator",
            modality="must",
            polarity="positive",
            action="route",
            object="tool error result",
            text=(
                "An isError:true CallToolResult is a received tool error. Preserve its "
                "operation identity and follow its declared retry disposition."
            ),
        ),
        _McpStartupRecoveryClause(
            clause_id="MCP-POST-APPLICATION",
            scope=_POST_RECEIPT_SCOPE,
            actor="orchestrator",
            modality="must",
            polarity="positive",
            action="parse",
            object="application result",
            text=(
                "An isError:false MCP response is a structured application result, "
                "including when its JSON contains success:false. Parse it before "
                "applying failure rules."
            ),
        ),
        _McpStartupRecoveryClause(
            clause_id="MCP-POST-RECOVERY-MANIFEST",
            scope=_POST_RECEIPT_SCOPE,
            actor="orchestrator",
            modality="must",
            polarity="positive",
            action="complete",
            object="recipe initialization",
            text=(
                "If the application result contains a recovery manifest, process it "
                "before any generic success:false branch: preserve recipe_pull, "
                "recipe_flow, initialization_id, required_sections, page-plan and "
                "pagination identities; pull every flow_records page in order, then "
                "the entrypoint named-step pages. Forward every advertised immutable "
                "identity plus initialization_id, page_plan_sha256, part, and "
                "continuation; reject mismatched versions, digests, sizes, records, "
                "skipped parts, or changed bindings. After exact reconstruction, call "
                "complete_recipe_initialization(initialization_id) and require its "
                "receipt before the first execution or mutation tool. The completion "
                "receipt carries a recipe_execution block with execution_id and "
                "invocation_template_digests; every subsequent run_skill for a named "
                "recipe step MUST forward recipe_execution_id="
                "recipe_execution.execution_id and invocation_template_digest="
                "recipe_execution.invocation_template_digests[step_name]. On the "
                "inline delivery path the same recipe_execution block appears in the "
                "open_kitchen response (top level, or under "
                "recipe_delivery.payload_metadata when the body is attested); the same "
                "forwarding rule applies."
            ),
        ),
        _McpStartupRecoveryClause(
            clause_id="MCP-POST-TERMINAL",
            scope=_POST_RECEIPT_SCOPE,
            actor="orchestrator",
            modality="must",
            polarity="positive",
            action="terminate",
            object="nonrecoverable application error",
            text=(
                "Only a structured, nonrecoverable application error is terminal. "
                "Print its user_visible_message verbatim (or the raw application result "
                "if absent), do not call AskUserQuestion, and do not diagnose it as MCP "
                "startup failure."
            ),
        ),
    ),
)

_MCP_RETRY_INSTRUCTION: str = _MCP_STARTUP_RECOVERY_SPEC.render()


def _read_full_sous_chef(
    skill_catalog: EffectiveSkillCatalog | None = None,
    *,
    project_dir: Path | None = None,
    backend: Any | None = None,
) -> str:
    """Project the effective sous-chef contract for an orchestrator prompt."""
    effective_root = (project_dir or Path.cwd()).resolve()
    catalog = skill_catalog or DefaultSkillResolver().list_effective(
        effective_root,
        SkillExecutionRole.ORCHESTRATOR,
    )
    if catalog.execution_role is not SkillExecutionRole.ORCHESTRATOR:
        raise ValueError("sous-chef projection requires an orchestrator skill catalog")
    sous_chef = next((skill for skill in catalog.skills if skill.name == "sous-chef"), None)
    if (
        sous_chef is None
        or sous_chef.invalid_reason is not None
        or sous_chef.execution_role is not SkillExecutionRole.ORCHESTRATOR
    ):
        return ""
    projected = project_agent_skill_document(
        sous_chef,
        SkillProjectionContext(
            cwd=effective_root,
            catalog=catalog,
            backend=backend,
            conventions=getattr(backend, "conventions", None),
            gating=False,
        ),
    ).content
    parsed = parse_frontmatter_content(projected)
    return parsed.body.lstrip("\n") if parsed.is_valid else projected


def _ingredient_table_display_instruction(source: str) -> str:
    """Return the display-verbatim instruction for an ingredient table."""
    return (
        f"Display the ingredient table from {source} verbatim in your response — "
        "do not reformat or re-render it.\n"
        "Then ask for the required fields (marked with *). If the recipe has both\n"
        "a task and an issue_url ingredient, mention that a GitHub issue URL can be\n"
        "provided as the task. Keep it to one or two short sentences."
    )


def _backend_supplement(has_unguarded_filesystem_access: bool) -> str:
    if has_unguarded_filesystem_access:
        return (
            "\n\nBACKEND-SPECIFIC CONSTRAINTS (unguarded filesystem access):\n"
            "- NEVER use run_cmd to read recipe YAML files, SKILL.md files, or agent "
            "definition files from the package directory. These raw files contain "
            "unresolved metadata that does not reflect the resolved state.\n"
            "- To recall step definitions or routing, call load_recipe.\n"
            "- To load skill instructions, call the Skill tool.\n"
            "- run_cmd is for executing project-level commands only — never for reading "
            "AutoSkillit package internals."
        )
    return ""


# ── Re-exports from domain submodules ───────────────────────────────────

from autoskillit.cli._prompts_campaign import (  # noqa: E402
    _build_dynamic_dispatch_section,
    _build_fleet_campaign_prompt,
    _has_dynamic_dispatch,
    _resume_reason_guidance,
)
from autoskillit.cli._prompts_kitchen import (  # noqa: E402
    _build_fleet_dispatch_prompt,
    _build_open_kitchen_prompt,
)
from autoskillit.cli._prompts_orchestrator import (  # noqa: E402
    _COOK_GREETINGS,
    _OPEN_KITCHEN_GREETINGS,
    _build_orchestrator_prompt,
    _get_ingredients_table,
)

__all__ = [
    "_MCP_RETRY_INSTRUCTION",
    "_read_full_sous_chef",
    "_ingredient_table_display_instruction",
    "_backend_supplement",
    "_build_fleet_campaign_prompt",
    "_has_dynamic_dispatch",
    "_build_dynamic_dispatch_section",
    "_resume_reason_guidance",
    "_build_orchestrator_prompt",
    "_get_ingredients_table",
    "_COOK_GREETINGS",
    "_OPEN_KITCHEN_GREETINGS",
    "_build_open_kitchen_prompt",
    "_build_fleet_dispatch_prompt",
]
