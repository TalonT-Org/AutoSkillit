"""Orchestrator-facing instruction surface definitions and registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

__all__ = [
    "InstructionExtractionMode",
    "OrchestratorSurfaceDef",
    "ORCHESTRATOR_FACING_INSTRUCTION_SURFACES",
]


class InstructionExtractionMode(StrEnum):
    """How orchestrator-facing prose is extracted from a registered surface."""

    MARKDOWN_FULL = "markdown_full"
    """Read the file whole. Used for SKILL.md entries."""

    PYTHON_DOCSTRINGS = "python_docstrings"
    """AST-parse and concatenate module/function/class docstrings only."""

    GENERATED_OUTPUT = "generated_output"
    """Call a named zero-argument (or default-argument) producer and sweep its
    return value. Required for prose assembled from function-body string
    literals rather than docstrings — PYTHON_DOCSTRINGS would scan nothing."""


@dataclass(frozen=True, slots=True)
class OrchestratorSurfaceDef:
    """One orchestrator-facing instruction surface the prose-forwarding sweep must scan.

    ``path_glob`` is relative to ``src/autoskillit`` and drives MARKDOWN_FULL /
    PYTHON_DOCSTRINGS extraction. ``producer_module``/``producer_symbol`` name a
    zero-argument (or default-argument) callable whose return value is the
    swept text for GENERATED_OUTPUT extraction — a dotted module path and
    attribute name, resolved dynamically by the consumer so this IL-0 registry
    never imports the higher-layer producer.
    """

    name: str
    extraction_mode: InstructionExtractionMode
    delivery_channel: str
    path_glob: str | None = None
    producer_module: str | None = None
    producer_symbol: str | None = None

    def __post_init__(self) -> None:
        if self.extraction_mode is InstructionExtractionMode.GENERATED_OUTPUT:
            if not self.producer_module or not self.producer_symbol:
                raise ValueError(
                    f"OrchestratorSurfaceDef {self.name!r}: GENERATED_OUTPUT requires "
                    "producer_module and producer_symbol"
                )
            if self.path_glob is not None:
                raise ValueError(
                    f"OrchestratorSurfaceDef {self.name!r}: GENERATED_OUTPUT must not "
                    "declare path_glob"
                )
        else:
            if not self.path_glob:
                raise ValueError(
                    f"OrchestratorSurfaceDef {self.name!r}: {self.extraction_mode.value} "
                    "requires path_glob"
                )
            if self.producer_module or self.producer_symbol:
                raise ValueError(
                    f"OrchestratorSurfaceDef {self.name!r}: {self.extraction_mode.value} "
                    "must not declare producer_module/producer_symbol"
                )


_TOOL_DOCSTRING_CHANNEL = "tool docstring (MCP tool description)"

_KITCHEN_TOOL_MODULE_PATHS: Mapping[str, str] = MappingProxyType(
    {
        "server.tools._recipe_section_handler": "server/tools/_recipe_section_handler.py",
        "server.tools.tools_agents": "server/tools/tools_agents.py",
        "server.tools.tools_audit_artifacts": "server/tools/tools_audit_artifacts.py",
        "server.tools.tools_ci": "server/tools/tools_ci.py",
        "server.tools.tools_ci_merge_queue": "server/tools/tools_ci_merge_queue.py",
        "server.tools.tools_ci_watch": "server/tools/tools_ci_watch.py",
        "server.tools.tools_clone": "server/tools/tools_clone.py",
        "server.tools.tools_evidence_reader": "server/tools/tools_evidence_reader.py",
        "server.tools.tools_execution._run_cmd": "server/tools/tools_execution/_run_cmd.py",
        "server.tools.tools_execution._fixed_batch_handlers": (
            "server/tools/tools_execution/_fixed_batch_handlers.py"
        ),
        "server.tools.tools_execution._run_python": "server/tools/tools_execution/_run_python.py",
        "server.tools.tools_execution._run_skill_dispatch": (
            "server/tools/tools_execution/_run_skill_dispatch.py"
        ),
        "server.tools.tools_exploration": "server/tools/tools_exploration.py",
        "server.tools.tools_fleet_dispatch._handlers": (
            "server/tools/tools_fleet_dispatch/_handlers.py"
        ),
        "server.tools.tools_fleet_reset": "server/tools/tools_fleet_reset.py",
        "server.tools.tools_git": "server/tools/tools_git.py",
        "server.tools.tools_github": "server/tools/tools_github.py",
        "server.tools.tools_issue_composite": "server/tools/tools_issue_composite.py",
        "server.tools.tools_issue_headless": "server/tools/tools_issue_headless.py",
        "server.tools.tools_issue_labels": "server/tools/tools_issue_labels.py",
        "server.tools.tools_kitchen._close_kitchen": (
            "server/tools/tools_kitchen/_close_kitchen.py"
        ),
        "server.tools.tools_kitchen._open_kitchen": "server/tools/tools_kitchen/_open_kitchen.py",
        "server.tools.tools_pipeline_tracker._handlers": (
            "server/tools/tools_pipeline_tracker/_handlers.py"
        ),
        "server.tools.tools_pr_ops": "server/tools/tools_pr_ops.py",
        "server.tools.tools_recipe": "server/tools/tools_recipe.py",
        "server.tools.tools_session_logs": "server/tools/tools_session_logs.py",
        "server.tools.tools_status": "server/tools/tools_status.py",
        "server.tools.tools_workspace": "server/tools/tools_workspace.py",
    }
)


def _kitchen_tool_module_surfaces() -> dict[str, OrchestratorSurfaceDef]:
    return {
        name: OrchestratorSurfaceDef(
            name=name,
            extraction_mode=InstructionExtractionMode.PYTHON_DOCSTRINGS,
            delivery_channel=_TOOL_DOCSTRING_CHANNEL,
            path_glob=path,
        )
        for name, path in _KITCHEN_TOOL_MODULE_PATHS.items()
    }


ORCHESTRATOR_FACING_INSTRUCTION_SURFACES: Mapping[str, OrchestratorSurfaceDef] = MappingProxyType(
    {
        "bootstrap_skills": OrchestratorSurfaceDef(
            name="bootstrap_skills",
            extraction_mode=InstructionExtractionMode.MARKDOWN_FULL,
            delivery_channel="open_kitchen injection into every orchestrator session",
            path_glob="skills/*/SKILL.md",
        ),
        "recipe_orchestration_rules": OrchestratorSurfaceDef(
            name="recipe_orchestration_rules",
            extraction_mode=InstructionExtractionMode.GENERATED_OUTPUT,
            delivery_channel="recipe delivery (orchestration_rules section, every recipe load)",
            producer_module="autoskillit.recipe._api_orchestration",
            producer_symbol="_build_orchestration_rules",
        ),
        "parameter_forwarding_rules": OrchestratorSurfaceDef(
            name="parameter_forwarding_rules",
            extraction_mode=InstructionExtractionMode.GENERATED_OUTPUT,
            # Keep this independently registered so coverage survives any change
            # to its current orchestration-rules embedding.
            delivery_channel="recipe delivery (embedded in orchestration_rules)",
            producer_module="autoskillit.core.tool_registry",
            producer_symbol="build_parameter_forwarding_rules",
        ),
        **_kitchen_tool_module_surfaces(),
    }
)
