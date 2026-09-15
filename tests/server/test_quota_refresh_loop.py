"""Tests for _quota_refresh_loop in server/_misc.py."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from autoskillit.config.settings import QuotaGuardConfig

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_quota_refresh_loop_calls_refresh_at_each_interval(monkeypatch, tmp_path):
    """Loop calls _refresh_quota_cache once per cache_refresh_interval sleep."""
    from autoskillit.server import _misc

    call_count = 0
    sleep_count = 0

    async def fake_sleep(n):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 3:
            raise asyncio.CancelledError

    async def fake_refresh(config):
        nonlocal call_count
        call_count += 1

    monkeypatch.setattr(_misc, "asyncio", SimpleNamespace(sleep=fake_sleep))
    monkeypatch.setattr(_misc, "_refresh_quota_cache", fake_refresh)

    config = QuotaGuardConfig(cache_refresh_interval=240)
    with pytest.raises(asyncio.CancelledError):
        await _misc._quota_refresh_loop(
            config,
            diagnostic_log_root=tmp_path,
            supports_quota_check=True,
        )

    assert call_count == 2  # one refresh per completed sleep


@pytest.mark.anyio
async def test_quota_refresh_loop_exits_cleanly_on_cancel(monkeypatch, tmp_path):
    """CancelledError from asyncio.sleep propagates; loop does not swallow it."""
    from autoskillit.server import _misc

    async def immediate_cancel(n):
        raise asyncio.CancelledError

    monkeypatch.setattr(_misc, "asyncio", SimpleNamespace(sleep=immediate_cancel))
    monkeypatch.setattr(_misc, "_refresh_quota_cache", AsyncMock())
    task = asyncio.create_task(
        _misc._quota_refresh_loop(
            QuotaGuardConfig(),
            diagnostic_log_root=tmp_path,
            supports_quota_check=True,
        )
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_quota_refresh_loop_continues_after_refresh_exception(monkeypatch, tmp_path):
    """A transient error in _refresh_quota_cache does not kill the loop."""
    from autoskillit.server import _misc

    call_count = 0
    sleep_count = 0

    async def fake_sleep(n):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 3:
            raise asyncio.CancelledError

    async def flaky_refresh(config):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("network blip")

    monkeypatch.setattr(_misc, "asyncio", SimpleNamespace(sleep=fake_sleep))
    monkeypatch.setattr(_misc, "_refresh_quota_cache", flaky_refresh)

    with pytest.raises(asyncio.CancelledError):
        await _misc._quota_refresh_loop(
            QuotaGuardConfig(),
            diagnostic_log_root=tmp_path,
            supports_quota_check=True,
        )

    assert call_count == 2  # loop continued after the first OSError


@pytest.mark.anyio
async def test_quota_refresh_loop_returns_immediately_when_unsupported(monkeypatch, tmp_path):
    """supports_quota_check=False exits immediately without entering the loop."""
    from autoskillit.server import _misc

    monkeypatch.setattr(
        _misc,
        "asyncio",
        SimpleNamespace(sleep=AsyncMock(side_effect=AssertionError("should not sleep"))),
    )
    monkeypatch.setattr(
        _misc,
        "_refresh_quota_cache",
        AsyncMock(side_effect=AssertionError("should not refresh")),
    )

    await _misc._quota_refresh_loop(
        QuotaGuardConfig(),
        diagnostic_log_root=tmp_path,
        supports_quota_check=False,
    )
    # No error = early return worked


@pytest.mark.anyio
async def test_prime_quota_cache_skips_when_unsupported(monkeypatch):
    """supports_quota_check=False skips the cache priming entirely."""
    from autoskillit.server import _misc

    monkeypatch.setattr(
        _misc,
        "check_and_sleep_if_needed",
        AsyncMock(side_effect=AssertionError("should not call")),
    )
    await _misc._prime_quota_cache(supports_quota_check=False)


@pytest.mark.parametrize(("existing_done", "start_count"), [(False, 0), (True, 1)])
def test_ensure_quota_refresh_started_reuses_live_task(
    monkeypatch,
    tmp_path,
    existing_done: bool,
    start_count: int,
):
    """A finalized Claude candidate starts one loop after a Codex boot."""
    from autoskillit.server import _misc

    loop = object()
    started_task = object()
    existing_task = SimpleNamespace(done=lambda: existing_done)
    create_task = MagicMock(return_value=started_task)
    monkeypatch.setattr(_misc, "_quota_refresh_loop", lambda *_, **__: loop)
    monkeypatch.setattr(_misc, "create_background_task", create_task)
    monkeypatch.setattr(_misc, "resolve_log_dir", lambda _: tmp_path / "diagnostics")
    ctx = SimpleNamespace(
        quota_refresh_task=existing_task,
        config=SimpleNamespace(
            quota_guard=QuotaGuardConfig(),
            linux_tracing=SimpleNamespace(log_dir=str(tmp_path)),
        ),
    )

    _misc._ensure_quota_refresh_started(ctx)

    assert create_task.call_count == start_count
    if start_count:
        assert ctx.quota_refresh_task is started_task
        assert create_task.call_args.args == (loop,)
        assert create_task.call_args.kwargs == {"label": "quota_refresh_loop"}


@pytest.mark.parametrize(
    ("candidate_backend", "candidate_profile", "default_provider", "expected"),
    [
        ("claude-code", "anthropic", "external", True),
        ("claude-code", None, None, True),
        ("claude-code", None, "external", False),
        ("claude-code", "external", None, False),
        ("codex", None, None, False),
    ],
)
def test_backend_supports_quota_only_for_first_party_candidate(
    candidate_backend: str,
    candidate_profile: str | None,
    default_provider: str | None,
    expected: bool,
):
    """A Codex parent does not poll Anthropic for external-only candidates."""
    from autoskillit.config import AgentBackendConfig
    from autoskillit.server.lifecycle._guards import _backend_supports_quota

    ctx = SimpleNamespace(
        backend=SimpleNamespace(capabilities=SimpleNamespace(anthropic_provider_capable=False)),
        config=SimpleNamespace(
            agent_backend=AgentBackendConfig(backend="codex"),
            features={"providers": True},
            providers=SimpleNamespace(
                default_provider=default_provider,
                step_overrides={},
                recipe_overrides={},
                execution_candidates=[
                    SimpleNamespace(backend=candidate_backend, profile=candidate_profile)
                ],
            ),
        ),
    )

    assert _backend_supports_quota(ctx) is expected


def test_backend_supports_quota_skips_external_primary() -> None:
    """An explicit external primary does not trigger an Anthropic refresh."""
    from autoskillit.server.lifecycle._guards import _backend_supports_quota

    ctx = SimpleNamespace(
        backend=SimpleNamespace(capabilities=SimpleNamespace(anthropic_provider_capable=True)),
        config=SimpleNamespace(
            agent_backend=SimpleNamespace(
                backend="claude-code",
                step_overrides={},
                recipe_overrides={},
            ),
            features={"providers": True},
            providers=SimpleNamespace(
                default_provider="external",
                step_overrides={},
                recipe_overrides={},
                execution_candidates=[],
            ),
        ),
    )

    assert _backend_supports_quota(ctx) is False
