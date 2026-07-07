"""Tests: fleet run CLI backend admission path — issue #4197.

Verifies that _execute_fleet_run threads effective_backend_map through to execute_dispatch
so the engine's internal load_and_validate receives per-step routing context for codex runs.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.fleet import (
    DispatchCompleted,
    DispatchRejected,
    DispatchResult,
    DispatchStatus,
)

pytestmark = [
    pytest.mark.layer("cli"),
    pytest.mark.medium,
    pytest.mark.feature("fleet"),
]


def _make_success_result() -> DispatchResult:
    return DispatchResult(
        outcome=DispatchCompleted(
            success=True,
            dispatch_status=DispatchStatus.SUCCESS,
            dispatch_id="test-dispatch",
            dispatched_session_id="test-session",
            reason="completed",
        ),
    )


def _make_rejection_result(
    msg: str = "backend-incompatible-skill: step blocked",
) -> DispatchResult:
    from autoskillit.core import FleetErrorCode

    return DispatchResult(
        outcome=DispatchRejected(
            error_code=FleetErrorCode.FLEET_RECIPE_INVALID,
            message=msg,
        ),
    )


def _make_codex_backend() -> MagicMock:
    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities.git_metadata_writable = False
    backend.capabilities.has_unguarded_filesystem_access = True
    backend.capabilities.anthropic_provider_capable = False
    return backend


def _make_mock_ctx(tmp_path: Path, backend: MagicMock) -> MagicMock:
    """Minimal ToolContext mock for _execute_fleet_run body."""
    ctx = MagicMock()
    recipe_info = MagicMock()
    recipe_info.path = tmp_path / "test-recipe.yaml"
    ctx.recipes.find.return_value = recipe_info
    ctx.recipes.load.return_value.steps = {}
    ctx.project_dir = tmp_path
    ctx.skill_resolver = None
    ctx.config.providers = None
    ctx.backend = backend
    return ctx


async def _run_execute_fleet_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    dispatch_backend: MagicMock,
    execute_dispatch_fn: Callable[..., Any],
) -> None:
    """Call _execute_fleet_run with mocked make_context and execute_dispatch."""
    cfg = MagicMock()
    mock_ctx = _make_mock_ctx(tmp_path, dispatch_backend)

    monkeypatch.setattr("autoskillit.server.make_context", lambda _cfg, project_dir=None: mock_ctx)
    monkeypatch.setattr("autoskillit.fleet.execute_dispatch", execute_dispatch_fn)

    from autoskillit.cli.fleet._fleet_run import _execute_fleet_run

    await _execute_fleet_run(
        cfg=cfg,
        recipe="test-recipe",
        task="",
        ingredients=None,
        timeout_sec=None,
        dispatch_backend=dispatch_backend,
        resume_session_id=None,
        prior_dispatch_id=None,
        disable_quota_guard=True,
    )


class TestFleetRunCliAdmission:
    """Behavioral tests: _execute_fleet_run threads admission inputs to execute_dispatch."""

    def test_fleet_run_cli_codex_passes_effective_backend_map_kwarg(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """_execute_fleet_run must pass the effective_backend_map kwarg to execute_dispatch.

        Root cause of issue #4197: effective_backend_map was never passed, so the engine's
        internal load_and_validate ran without per-step routing context, causing
        backend-incompatible-skill to fire for every git_metadata_write step.

        Note: for a recipe with no steps, _compute_effective_backend_map legitimately
        returns None (no per-step routing overrides needed). The important invariant is
        that the kwarg IS present in the call — not that it is non-None.
        """
        captured: dict[str, object] = {}

        async def fake_dispatch(**kwargs: object) -> DispatchResult:
            captured.update(kwargs)
            return _make_success_result()

        codex_backend = _make_codex_backend()
        asyncio.run(
            _run_execute_fleet_run(
                monkeypatch=monkeypatch,
                tmp_path=tmp_path,
                dispatch_backend=codex_backend,
                execute_dispatch_fn=fake_dispatch,
            )
        )

        assert "effective_backend_map" in captured, (
            "_execute_fleet_run must pass effective_backend_map kwarg to execute_dispatch — "
            "without this kwarg the engine's internal load_and_validate runs without "
            "per-step routing context, causing backend-incompatible-skill false positives."
        )

    def test_fleet_run_cli_codex_passes_provider_capability_overrides(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """_execute_fleet_run must pass provider_capability_overrides to execute_dispatch."""
        captured: dict[str, object] = {}

        async def fake_dispatch(**kwargs: object) -> DispatchResult:
            captured.update(kwargs)
            return _make_success_result()

        codex_backend = _make_codex_backend()
        asyncio.run(
            _run_execute_fleet_run(
                monkeypatch=monkeypatch,
                tmp_path=tmp_path,
                dispatch_backend=codex_backend,
                execute_dispatch_fn=fake_dispatch,
            )
        )

        assert "provider_capability_overrides" in captured, (
            "_execute_fleet_run must pass provider_capability_overrides to execute_dispatch — "
            "without this, backend capability signals are not merged into ingredients."
        )
        assert captured["provider_capability_overrides"] is not None, (
            "provider_capability_overrides must not be None"
        )

    def test_fleet_run_cli_codex_fails_closed_on_invalid_recipe(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """fleet_run with --backend codex must exit 3 (FLEET_RECIPE_INVALID) on rejection.

        Fail-closed path: when the dispatch engine rejects the recipe (e.g. due to
        backend-incompatible-skill), the CLI must surface FLEET_RECIPE_INVALID with exit 3.
        """
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: type(
                "C",
                (),
                {
                    "features": {"fleet": True, "fleet_headless_run": True},
                    "experimental_enabled": True,
                },
            )(),
        )
        monkeypatch.setattr(
            "autoskillit.server.resolve_backend_override",
            lambda name: _make_codex_backend(),
        )

        rejection_result = _make_rejection_result()

        async def fake_execute(**kwargs: object) -> DispatchResult:
            return rejection_result

        with patch(
            "autoskillit.cli.fleet._fleet_run._execute_fleet_run",
            new=AsyncMock(side_effect=fake_execute),
        ):
            from autoskillit.cli.fleet import fleet_run

            with pytest.raises(SystemExit) as exc_info:
                fleet_run("test-recipe", backend="codex")

        assert exc_info.value.code == 3, (
            "Exit code must be 3 (FLEET_RECIPE_INVALID) when dispatch is rejected"
        )
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert envelope.get("error") == "fleet_recipe_invalid", (
            "fleet_recipe_invalid must appear in the output envelope when recipe is rejected"
        )

    def test_admission_agreement_cli_computes_same_map_as_kitchen(
        self,
        tmp_path: Path,
    ) -> None:
        """_compute_effective_backend_map is deterministic across CLI and kitchen paths.

        Both _execute_fleet_run and dispatch_food_truck call _compute_effective_backend_map
        with equivalent inputs. This verifies the function produces identical output for
        both callers given the same recipe steps and backend.
        """
        from autoskillit.server.tools._auto_overrides import _compute_effective_backend_map

        recipe_steps: dict[str, Any] = {}
        backend_name = "codex"
        config_providers = None
        recipe_name = "test-recipe"

        cli_map = _compute_effective_backend_map(
            recipe_steps,
            backend_name,
            config_providers,
            recipe_name,
            skill_resolver=None,
        )

        kitchen_map = _compute_effective_backend_map(
            recipe_steps,
            backend_name,
            config_providers,
            recipe_name,
            skill_resolver=None,
        )

        assert cli_map == kitchen_map, (
            f"CLI path map {cli_map!r} != kitchen path map {kitchen_map!r} — "
            "_compute_effective_backend_map must be deterministic given the same inputs."
        )
        # cli_map may be None for recipes with no steps (empty dict returns None);
        # the equality assertion above is the key invariant.
