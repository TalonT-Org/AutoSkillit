"""Shared production-shaped inputs for orchestrator prompt builders."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend
    from autoskillit.workspace import CompiledSessionSkillCatalog


def compiled_orchestrator_prompt_inputs(
    project_root: Path | None = None,
    *,
    backend_name: str = "claude-code",
) -> tuple[CompiledSessionSkillCatalog, CodingAgentBackend, Path]:
    """Return the admitted catalog and matching backend required by prompt builders."""
    from autoskillit.core import SkillExecutionRole
    from autoskillit.execution.backends import get_backend
    from autoskillit.workspace import DefaultSkillResolver, compile_session_skill_catalog

    root = (project_root or Path(__file__).resolve().parents[2]).resolve()
    backend = get_backend(backend_name)
    catalog = DefaultSkillResolver().list_effective(root, SkillExecutionRole.ORCHESTRATOR)
    return compile_session_skill_catalog(catalog, backend), backend, root


def build_orchestrator_prompt(*args: Any, **kwargs: Any) -> str:
    """Call the production builder with the admitted prompt inputs it requires."""
    from autoskillit.cli.prompts import _build_orchestrator_prompt

    compilation, backend, project_root = compiled_orchestrator_prompt_inputs()
    return _build_orchestrator_prompt(
        *args,
        skill_compilation=compilation,
        project_root=project_root,
        backend=backend,
        **kwargs,
    )


def read_full_sous_chef() -> str:
    """Read sous-chef through the compiled catalog required by production prompts."""
    from autoskillit.cli.prompts import _read_full_sous_chef

    compilation, backend, project_root = compiled_orchestrator_prompt_inputs()
    return _read_full_sous_chef(
        compilation,
        project_root=project_root,
        backend=backend,
    )


def build_open_kitchen_prompt(*args: Any, **kwargs: Any) -> str:
    """Call the production builder with the admitted prompt inputs it requires."""
    from autoskillit.cli.prompts import _build_open_kitchen_prompt

    compilation, backend, project_root = compiled_orchestrator_prompt_inputs()
    return _build_open_kitchen_prompt(
        *args,
        skill_compilation=compilation,
        project_root=project_root,
        backend=backend,
        **kwargs,
    )


def build_fleet_dispatch_prompt(*args: Any, **kwargs: Any) -> str:
    """Call the production builder with the admitted prompt inputs it requires."""
    from autoskillit.cli.prompts import _build_fleet_dispatch_prompt

    compilation, backend, project_root = compiled_orchestrator_prompt_inputs()
    return _build_fleet_dispatch_prompt(
        *args,
        skill_compilation=compilation,
        project_root=project_root,
        backend=backend,
        **kwargs,
    )
