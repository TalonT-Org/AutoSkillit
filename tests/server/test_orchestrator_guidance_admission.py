"""Admission coverage for backend-aware orchestrator guidance projections."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
import structlog

from autoskillit.config import AutomationConfig
from autoskillit.execution.backends import get_backend
from autoskillit.workspace import DefaultSkillResolver

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _guidance_context(*, backend: object | None) -> SimpleNamespace:
    """Build the production-shaped ORCHESTRATOR resolution context."""
    return SimpleNamespace(
        skill_resolver=DefaultSkillResolver(),
        project_dir=_REPO_ROOT,
        config=AutomationConfig(),
        active_recipe_packs=frozenset(),
        active_recipe_features=frozenset(),
        backend=backend,
    )


class _ResolverThatMustNotRun:
    def list_effective(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("backendless guidance must return before resolving skills")


def _server_guidance(
    context: object,
    backend: object | None,
) -> str:
    from autoskillit.server.tools._serve_helpers import project_orchestrator_guidance

    return project_orchestrator_guidance(context, backend=backend)


def _fleet_guidance(
    context: object,
    backend: object | None,
) -> str:
    from autoskillit.server.tools.tools_fleet_dispatch._campaign_state import (
        _project_food_truck_sous_chef,
    )

    return _project_food_truck_sous_chef(context, backend)


def _install_projection_spies(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Capture the skill document selected by either live guidance helper."""
    from autoskillit.server.tools import _serve_helpers
    from autoskillit.server.tools.tools_fleet_dispatch import _campaign_state

    selected: list[str] = []

    def project(skill: object, _context: object) -> SimpleNamespace:
        selected.append(getattr(skill, "name"))
        return SimpleNamespace(content=f"projected:{getattr(skill, 'name')}")

    monkeypatch.setattr(_serve_helpers, "project_agent_skill_document", project)
    monkeypatch.setattr(_campaign_state, "project_agent_skill_document", project)
    return selected


@pytest.mark.parametrize(
    ("project_guidance", "guidance_name"),
    [
        (_server_guidance, "anonymous-kitchen"),
        (_fleet_guidance, "food-truck"),
    ],
)
@pytest.mark.parametrize("backend_source", ["explicit", "context"])
def test_orchestrator_guidance_projects_only_admitted_sous_chef_and_logs_refusal(
    monkeypatch: pytest.MonkeyPatch,
    project_guidance: Callable[[object, object | None], str],
    guidance_name: str,
    backend_source: str,
) -> None:
    """Live guidance compiles before choosing sous-chef and preserves its refusals."""
    backend = get_backend("codex")
    context = _guidance_context(backend=backend if backend_source == "context" else None)
    selected = _install_projection_spies(monkeypatch)

    with structlog.testing.capture_logs() as logs:
        guidance = project_guidance(
            context,
            backend if backend_source == "explicit" else None,
        )

    assert guidance.endswith("projected:sous-chef")
    assert selected == ["sous-chef"], f"{guidance_name} guidance selected a raw catalog entry"

    refusals = [record for record in logs if record.get("skill") == "process-issues"]
    assert len(refusals) == 1
    refusal = refusals[0]
    assert refusal["backend"] == "codex"
    assert refusal["operation"] == "required_join"
    assert isinstance(refusal["diagnostic"], str) and refusal["diagnostic"]


@pytest.mark.parametrize("project_guidance", [_server_guidance, _fleet_guidance])
def test_orchestrator_guidance_without_backend_does_not_resolve_or_project(
    project_guidance: Callable[[object, object | None], str],
) -> None:
    """No backend means no admission authority, so guidance is empty before resolution."""
    context = _guidance_context(backend=None)
    context.skill_resolver = _ResolverThatMustNotRun()

    assert project_guidance(context, None) == ""
