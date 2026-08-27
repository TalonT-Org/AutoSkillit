"""Prompt-builder admission requirements for orchestrator guidance."""

from __future__ import annotations

import inspect

import pytest

from autoskillit.core import DIRECT_PREFIX
from tests.cli._orchestrator_prompt_helpers import compiled_orchestrator_prompt_inputs

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_prompt_builders_require_an_admitted_catalog_matching_backend_and_project_root() -> None:
    """No builder retains a default path that can resolve raw backend-bound guidance."""
    from autoskillit.cli.prompts import _build_orchestrator_prompt
    from autoskillit.cli.prompts._prompts import _read_full_sous_chef
    from autoskillit.cli.prompts._prompts_kitchen import (
        _build_fleet_dispatch_prompt,
        _build_open_kitchen_prompt,
    )

    for builder in (
        _read_full_sous_chef,
        _build_open_kitchen_prompt,
        _build_fleet_dispatch_prompt,
        _build_orchestrator_prompt,
    ):
        parameters = inspect.signature(builder).parameters
        for name in ("skill_compilation", "project_root", "backend"):
            assert parameters[name].default is inspect.Parameter.empty, (
                f"{builder.__name__} must require {name}; a default recreates the "
                "backendless/raw-catalog guidance corridor"
            )


def test_read_full_sous_chef_rejects_a_raw_catalog_before_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lower-level reader no longer owns raw effective-catalog authority."""
    from autoskillit.cli.prompts import _prompts
    from autoskillit.core import SkillExecutionRole
    from autoskillit.workspace import DefaultSkillResolver

    compilation, backend, project_root = compiled_orchestrator_prompt_inputs()
    raw_catalog = DefaultSkillResolver().list_effective(
        project_root,
        SkillExecutionRole.ORCHESTRATOR,
    )
    monkeypatch.setattr(
        _prompts,
        "project_agent_skill_document",
        lambda *_args, **_kwargs: pytest.fail("raw catalog must fail before projection"),
    )

    with pytest.raises(TypeError, match="CompiledSessionSkillCatalog"):
        _prompts._read_full_sous_chef(
            raw_catalog,
            project_root=project_root,
            backend=backend,
        )

    assert compilation.backend == backend.name


def test_read_full_sous_chef_rejects_a_backend_mismatch_before_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An admitted catalog cannot be reused for a different backend projection."""
    from autoskillit.cli.prompts import _prompts
    from autoskillit.execution.backends import get_backend

    compilation, _backend, project_root = compiled_orchestrator_prompt_inputs()
    monkeypatch.setattr(
        _prompts,
        "project_agent_skill_document",
        lambda *_args, **_kwargs: pytest.fail("mismatched backend must not project guidance"),
    )

    with pytest.raises(ValueError, match="backend"):
        _prompts._read_full_sous_chef(
            compilation,
            project_root=project_root,
            backend=get_backend("codex"),
        )


def test_prompt_builders_accept_the_shared_admitted_catalog() -> None:
    """Direct prompt construction accepts the same compiled authority at every layer."""
    from autoskillit.cli.prompts import (
        _build_orchestrator_prompt,
        _read_full_sous_chef,
    )
    from autoskillit.cli.prompts._prompts_kitchen import (
        _build_fleet_dispatch_prompt,
        _build_open_kitchen_prompt,
    )

    compilation, backend, project_root = compiled_orchestrator_prompt_inputs()
    admitted_guidance = _read_full_sous_chef(
        compilation,
        project_root=project_root,
        backend=backend,
    )

    prompts = (
        _build_open_kitchen_prompt(
            DIRECT_PREFIX,
            skill_compilation=compilation,
            project_root=project_root,
            backend=backend,
        ),
        _build_fleet_dispatch_prompt(
            DIRECT_PREFIX,
            skill_compilation=compilation,
            project_root=project_root,
            backend=backend,
        ),
        _build_orchestrator_prompt(
            "implementation",
            DIRECT_PREFIX,
            skill_compilation=compilation,
            project_root=project_root,
            backend=backend,
        ),
    )

    assert admitted_guidance
    assert all(admitted_guidance in prompt for prompt in prompts)
