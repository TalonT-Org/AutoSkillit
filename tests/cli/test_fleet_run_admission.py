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

    def test_fleet_run_cli_codex_fails_without_claude_binary(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, tmp_path: Path
    ) -> None:
        """_execute_fleet_run with codex backend fails closed when claude binary is absent.

        Exercises the real admission path: fleet_run CLI → _execute_fleet_run (real, not
        mocked) → execute_dispatch (real) → _run_dispatch → load_and_validate (real fake,
        not MagicMock). shutil.which=None in the _auto_overrides namespace triggers the
        no-claude-binary scenario; InMemoryRecipeRepository returns backend-incompatible-skill.
        """
        from tests.fakes import InMemoryRecipeRepository

        # Configure InMemoryRecipeRepository to return a backend-incompatible rejection.
        # load_and_validate is NOT a MagicMock — it runs the real InMemoryRecipeRepository
        # logic (records calls, checks _validated, raises RecipeNotFoundError if absent).
        repo = InMemoryRecipeRepository()
        repo.set_validated(
            "test-recipe",
            {
                "valid": False,
                "errors": ["backend-incompatible-skill: step-1 requires claude-code"],
            },
        )

        # Build ctx. Use MagicMock for find/load (pre-dispatch recipe lookups in
        # _execute_fleet_run); wire load_and_validate to the real InMemoryRecipeRepository
        # method so the dispatch path exercises real fake logic.
        codex_backend = _make_codex_backend()
        ctx = MagicMock()
        ctx.project_dir = tmp_path
        ctx.skill_resolver = None
        ctx.config.providers = None
        ctx.config.migration.suppressed = None
        ctx.temp_dir = tmp_path / ".autoskillit" / "temp"
        ctx.backend = codex_backend

        # fleet_lock: execute_dispatch awaits lock.acquire() then calls lock.release() sync.
        ctx.fleet_lock.at_capacity.return_value = False
        ctx.fleet_lock.acquire = AsyncMock(return_value=None)

        # Pre-dispatch recipe lookups in _execute_fleet_run (find/load for steps computation)
        recipe_info = MagicMock()
        recipe_info.path = tmp_path / "test-recipe.yaml"
        ctx.recipes.find.return_value = recipe_info
        ctx.recipes.load.return_value.steps = {}

        # Override load_and_validate on the MagicMock to use the real InMemoryRecipeRepository
        # method — this is a real fake, not a predetermined return-value mock.
        ctx.recipes.load_and_validate = repo.load_and_validate

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
            lambda name: codex_backend,
        )
        # shutil.which=None in the _auto_overrides namespace: no claude binary.
        # This exercises the real _provider_aware_capability_overrides and
        # _compute_effective_backend_map code paths inside _execute_fleet_run.
        monkeypatch.setattr(
            "autoskillit.server.tools._auto_overrides.shutil.which",
            lambda cmd: None,
        )
        monkeypatch.setattr(
            "autoskillit.server.make_context",
            lambda _cfg, project_dir=None: ctx,
        )

        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit) as exc_info:
            fleet_run("test-recipe", backend="codex")

        assert exc_info.value.code == 3, (
            "Exit code must be 3 (FLEET_RECIPE_INVALID) when admission fails"
        )
        captured_out = capsys.readouterr()
        envelope = json.loads(captured_out.out)
        assert envelope.get("error") == "fleet_recipe_invalid", (
            "fleet_recipe_invalid must appear in the output envelope"
        )
        rejection_message = envelope.get("user_visible_message", "")
        assert "backend-incompatible-skill" in rejection_message, (
            f"backend-incompatible-skill must be present in the rejection message; "
            f"got: {rejection_message!r}"
        )

        # Verify the real dispatch path was exercised: load_and_validate was called with
        # the effective_backend_map kwarg from the real _execute_fleet_run computation.
        lav_calls = [c for c in repo.calls if c["method"] == "load_and_validate"]
        assert lav_calls, (
            "load_and_validate must be called through the real dispatch path — "
            "if this assertion fails, execute_dispatch was not reached"
        )
        assert "effective_backend_map" in lav_calls[0], (
            "load_and_validate call must include effective_backend_map kwarg — "
            "REQ-004: effective_backend_map must be threaded to load_and_validate"
        )

    @pytest.mark.parametrize("backend_name", ["codex", "claude-code"])
    def test_admission_agreement_cli_matches_kitchen_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend_name: str
    ) -> None:
        """effective_backend_map at execute_dispatch is identical from CLI and kitchen paths.

        Runs _execute_fleet_run with a spy on execute_dispatch to capture the
        effective_backend_map kwarg (CLI path). Separately computes what dispatch_food_truck
        would pass to execute_dispatch by calling _compute_effective_backend_map directly
        with equivalent inputs (kitchen path). Asserts the two values are equal.

        Parametrized over backends to guard against per-backend routing divergence.
        """
        from autoskillit.server import _compute_effective_backend_map

        captured: dict[str, object] = {}

        async def spy_dispatch(**kwargs: object) -> DispatchResult:
            captured.update(kwargs)
            return _make_success_result()

        # Build backend mock for the requested backend_name
        if backend_name == "codex":
            backend = _make_codex_backend()
        else:
            backend = MagicMock()
            backend.name = "claude-code"
            backend.capabilities.git_metadata_writable = True
            backend.capabilities.has_unguarded_filesystem_access = False
            backend.capabilities.anthropic_provider_capable = True

        # Run CLI path via existing _run_execute_fleet_run helper.
        # _run_execute_fleet_run mocks execute_dispatch with spy_dispatch so the real
        # _execute_fleet_run computation (_compute_effective_backend_map, etc.) runs but
        # dispatch does not. spy_dispatch captures all kwargs passed to execute_dispatch.
        asyncio.run(
            _run_execute_fleet_run(
                monkeypatch=monkeypatch,
                tmp_path=tmp_path,
                dispatch_backend=backend,
                execute_dispatch_fn=spy_dispatch,
            )
        )

        cli_map = captured.get("effective_backend_map")

        # Kitchen path: compute what dispatch_food_truck passes to execute_dispatch.
        # dispatch_food_truck calls _compute_effective_backend_map(recipe_steps, backend_name,
        # config_providers, recipe_name, skill_resolver=skill_resolver) with the same recipe
        # steps that _make_mock_ctx returns (empty dict).
        recipe_steps: dict[str, Any] = {}
        kitchen_map, _ = _compute_effective_backend_map(
            recipe_steps,
            backend_name,
            None,  # config_providers
            "test-recipe",
            skill_resolver=None,
        )

        assert cli_map == kitchen_map, (
            f"CLI path effective_backend_map {cli_map!r} != "
            f"kitchen path {kitchen_map!r} for backend {backend_name!r}. "
            "Both _execute_fleet_run and dispatch_food_truck must pass identical "
            "effective_backend_map values to execute_dispatch."
        )
