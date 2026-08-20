"""End-to-end tests for the pre-launch model<->backend compatibility gate (#4238).

A model native to one backend (e.g. a Codex-only model id) must never silently
redirect the backend selected by ``authority_candidates`` — and the model's own
config provenance (its key path, not the backend authority's) must be what
reaches ``LaunchPreparation``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.core import LaunchPreparation, LaunchValueSourceKind
from autoskillit.core.types import LaunchContractError, RetryReason, SkillResult

from .conftest import _backend_authority, _mock_backend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_CODEX_NATIVE_MODEL = "gpt-5.6-sol"
_RECIPE_OVERRIDES = {"implementation": {"review_pr": _CODEX_NATIVE_MODEL}}
_MODEL_KEY_PATH = "model.recipe_overrides.implementation.review_pr"
_MODEL_KEY_PATH_RE = _MODEL_KEY_PATH.replace(".", r"\.")


def _skill_result_success() -> SkillResult:
    return SkillResult(
        success=True,
        result="",
        session_id="test-session",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )


@pytest.mark.anyio
async def test_codex_model_pinned_without_backend_pin_fails_before_launch(minimal_ctx):
    """A Codex-native model pinned via recipe_overrides, with no backend pin,
    must fail closed before any subprocess is spawned — the global Claude Code
    authority wins and a foreign-backend model may never redirect it."""
    minimal_ctx.config.model.recipe_overrides = _RECIPE_OVERRIDES
    minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)
    minimal_ctx.runner = AsyncMock()

    from autoskillit.execution.headless import run_headless_core

    with pytest.raises(
        LaunchContractError,
        match=rf"{_MODEL_KEY_PATH_RE}.*agent_backend\.backend",
    ):
        await run_headless_core(
            "/autoskillit:test",
            "/tmp/cwd",
            minimal_ctx,
            completion_marker="%%DONE%%",
            step_name="review_pr",
            recipe_name="implementation",
        )

    minimal_ctx.runner.assert_not_awaited()


@pytest.mark.anyio
async def test_codex_model_with_matching_backend_pin_launches(minimal_ctx):
    """The same Codex-native model, paired with an explicit codex
    backend_authority pin, proceeds past resolution with no model/backend
    drift error."""
    minimal_ctx.config.model.recipe_overrides = _RECIPE_OVERRIDES
    minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)
    minimal_ctx.runner = AsyncMock()

    codex_backend = _mock_backend(
        pty_required=False, channel_b_capable=False, process_name="codex"
    )
    codex_backend.name = "codex"
    authority = _backend_authority("codex")

    with (
        patch.object(minimal_ctx.launch_resolver, "backend_for", return_value=codex_backend),
        patch("autoskillit.execution.headless._execute_claude_headless") as mock_exec,
    ):
        mock_exec.return_value = _skill_result_success()
        from autoskillit.execution.headless import run_headless_core

        result = await run_headless_core(
            "/autoskillit:test",
            "/tmp/cwd",
            minimal_ctx,
            completion_marker="%%DONE%%",
            step_name="review_pr",
            recipe_name="implementation",
            backend_authority=authority,
        )

    assert result.success is True
    mock_exec.assert_called_once()


@pytest.mark.anyio
async def test_model_key_path_reaches_launch_preparation(minimal_ctx):
    """Provenance of the resolved model must be the model's own config key path
    (model.recipe_overrides.<recipe>.<step>), not the backend authority's key
    path (agent_backend.backend) — the real-provenance fix behind #4238."""
    minimal_ctx.config.model.recipe_overrides = _RECIPE_OVERRIDES
    minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)
    minimal_ctx.runner = AsyncMock()

    codex_backend = _mock_backend(
        pty_required=False, channel_b_capable=False, process_name="codex"
    )
    codex_backend.name = "codex"
    authority = _backend_authority("codex")

    original_prepare = minimal_ctx.launch_resolver.prepare
    captured: list[LaunchPreparation] = []

    def _spy_prepare(request):
        preparation = original_prepare(request)
        captured.append(preparation)
        return preparation

    with (
        patch.object(minimal_ctx.launch_resolver, "prepare", side_effect=_spy_prepare),
        patch.object(minimal_ctx.launch_resolver, "backend_for", return_value=codex_backend),
        patch("autoskillit.execution.headless._execute_claude_headless") as mock_exec,
    ):
        mock_exec.return_value = _skill_result_success()
        from autoskillit.execution.headless import run_headless_core

        await run_headless_core(
            "/autoskillit:test",
            "/tmp/cwd",
            minimal_ctx,
            completion_marker="%%DONE%%",
            step_name="review_pr",
            recipe_name="implementation",
            backend_authority=authority,
        )

    assert len(captured) == 1
    preparation = captured[0]
    assert preparation.configured_model_source.key_path == _MODEL_KEY_PATH
    assert preparation.configured_model_source.kind is LaunchValueSourceKind.RECIPE
