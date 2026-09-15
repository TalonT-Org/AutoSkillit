"""Tests for the bounded, credential-aware pre-spawn quota admission gate."""

from __future__ import annotations

import time

import pytest

from tests._helpers import make_quota_guard_config

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


@pytest.mark.anyio
async def test_admission_rejects_an_elapsed_deadline_without_acquiring_a_lease(
    monkeypatch, tmp_path
):
    """An expired logical deadline must not start a fresh quota wait or worker."""
    import autoskillit.execution.quota._admission as admission

    acquire_calls: list[object] = []

    def unexpected_acquire(*args, **kwargs):
        acquire_calls.append((args, kwargs))
        raise AssertionError("elapsed admission must not acquire the provider lease")

    monkeypatch.setattr(admission.ArtifactLease, "acquire_exclusive", unexpected_acquire)

    decision = await admission.admit_quota(
        config=make_quota_guard_config(credentials_path=str(tmp_path / "credentials.json")),
        credential_scope="anthropic-oauth:token-digest",
        diagnostic_log_root=tmp_path,
        deadline_monotonic=time.monotonic() - 0.01,
    )

    assert decision.admitted is False
    assert decision.reason == "deadline_exceeded"
    assert acquire_calls == []


@pytest.mark.anyio
async def test_oauth_fetch_failure_is_a_pre_spawn_rejection_not_a_sleep_or_admission(
    monkeypatch, tmp_path
):
    """Authoritative OAuth quota data that cannot be refreshed fails closed."""
    import autoskillit.execution.quota._admission as admission

    async def unavailable_quota(*args, **kwargs):
        raise OSError("usage endpoint unavailable")

    monkeypatch.setattr(admission, "_fetch_quota", unavailable_quota)
    monkeypatch.setattr(
        admission,
        "quota_scope",
        lambda *_args, **_kwargs: "anthropic-oauth:token-digest",
    )

    decision = await admission.admit_quota(
        config=make_quota_guard_config(credentials_path=str(tmp_path / "credentials.json")),
        credential_scope="anthropic-oauth:token-digest",
        diagnostic_log_root=tmp_path,
        deadline_monotonic=time.monotonic() + 5,
    )

    assert decision.admitted is False
    assert decision.reason == "quota_authority_unavailable"
    assert decision.lease is None


@pytest.mark.anyio
async def test_disabled_admission_bypasses_oauth_scope_and_lease(monkeypatch, tmp_path):
    import autoskillit.execution.quota._admission as admission

    def unexpected_acquire(*_args, **_kwargs):
        raise AssertionError("disabled quota guard must not acquire an OAuth lease")

    monkeypatch.setattr(admission.ArtifactLease, "acquire_exclusive", unexpected_acquire)

    decision = await admission.admit_quota(
        config=make_quota_guard_config(enabled=False),
        credential_scope="anthropic-oauth:token-digest",
        diagnostic_log_root=tmp_path,
        deadline_monotonic=time.monotonic() + 5,
    )

    assert decision.admitted is True
    assert decision.reason == "quota_guard_disabled"
    assert decision.lease is None


@pytest.mark.anyio
async def test_malformed_oauth_authority_fails_closed(tmp_path):
    import autoskillit.execution.quota._admission as admission

    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{")

    decision = await admission.admit_quota(
        config=make_quota_guard_config(
            credentials_path=str(credentials_path),
            cache_path=str(tmp_path / "quota-cache.json"),
        ),
        credential_scope="anthropic-oauth:token-digest",
        diagnostic_log_root=tmp_path,
        deadline_monotonic=time.monotonic() + 5,
    )

    assert decision.admitted is False
    assert decision.reason == "quota_authority_unknown"


@pytest.mark.anyio
async def test_near_threshold_outer_admission_retains_the_shared_lease(monkeypatch, tmp_path):
    import autoskillit.execution.quota._admission as admission
    from autoskillit.core import ARTIFACT_LEASE_TIMEOUT_SECONDS
    from autoskillit.execution.quota import QuotaFetchResult, QuotaStatus, QuotaWindowEntry

    class Lease:
        closed = False

        def close_preserving(self):
            self.closed = True

    lease = Lease()
    fetches = 0
    acquisition_timeouts: list[float] = []

    def acquire(*_args, **kwargs):
        acquisition_timeouts.append(kwargs["timeout"])
        return lease

    async def near_threshold(*_args, **_kwargs):
        nonlocal fetches
        fetches += 1
        return QuotaFetchResult(
            windows={"five_hour": QuotaWindowEntry(utilization=82.0, resets_at=None)},
            binding=QuotaStatus(
                utilization=82.0,
                resets_at=None,
                window_name="five_hour",
                effective_threshold=85.0,
            ),
        )

    monkeypatch.setattr(admission.ArtifactLease, "acquire_exclusive", acquire)
    monkeypatch.setattr(admission, "_fetch_quota", near_threshold)
    monkeypatch.setattr(
        admission,
        "quota_scope",
        lambda *_args, **_kwargs: "anthropic-oauth:token-digest",
    )

    decision = await admission.admit_quota(
        config=make_quota_guard_config(credentials_path=str(tmp_path / "credentials.json")),
        credential_scope="anthropic-oauth:token-digest",
        diagnostic_log_root=tmp_path,
        deadline_monotonic=time.monotonic() + 5,
    )

    assert decision.admitted is True
    assert decision.lease is lease
    assert lease.closed is False
    assert fetches == 2
    assert 0 < acquisition_timeouts[0] <= ARTIFACT_LEASE_TIMEOUT_SECONDS


@pytest.mark.anyio
async def test_near_threshold_window_revalidates_even_when_binding_has_more_headroom(
    monkeypatch, tmp_path
):
    """Admission protects the closest enabled window, not just the highest utilization."""
    import autoskillit.execution.quota._admission as admission
    from autoskillit.execution.quota import QuotaFetchResult, QuotaStatus, QuotaWindowEntry

    class Lease:
        closed = False

        def close_preserving(self):
            self.closed = True

    lease = Lease()
    fetches = 0

    async def windows_with_different_headroom(*_args, **_kwargs):
        nonlocal fetches
        fetches += 1
        return QuotaFetchResult(
            windows={
                "seven_day": QuotaWindowEntry(utilization=89.0, resets_at=None),
                "five_hour": QuotaWindowEntry(utilization=77.0, resets_at=None),
            },
            binding=QuotaStatus(
                utilization=89.0,
                resets_at=None,
                window_name="seven_day",
                effective_threshold=95.0,
            ),
        )

    monkeypatch.setattr(
        admission.ArtifactLease, "acquire_exclusive", lambda *_args, **_kwargs: lease
    )
    monkeypatch.setattr(admission, "_fetch_quota", windows_with_different_headroom)
    monkeypatch.setattr(
        admission,
        "quota_scope",
        lambda *_args, **_kwargs: "anthropic-oauth:token-digest",
    )

    decision = await admission.admit_quota(
        config=make_quota_guard_config(
            credentials_path=str(tmp_path / "credentials.json"),
            short_window_threshold=80.0,
            long_window_threshold=95.0,
        ),
        credential_scope="anthropic-oauth:token-digest",
        diagnostic_log_root=tmp_path,
        deadline_monotonic=time.monotonic() + 5,
    )

    assert decision.admitted is True
    assert decision.lease is lease
    assert lease.closed is False
    assert fetches == 2


@pytest.mark.anyio
async def test_external_binding_observed_constraint_rejects_without_oauth_poll(
    monkeypatch, tmp_path
):
    import autoskillit.execution.quota._admission as admission
    from autoskillit.execution.quota._quota_observed import record_observed_rate_limit

    scope = "external:credential-digest"
    now_epoch = int(time.time())
    config = make_quota_guard_config(
        credentials_path=str(tmp_path / "credentials.json"),
        cache_path=str(tmp_path / "quota-cache.json"),
    )
    record_observed_rate_limit(
        config,
        scope=scope,
        resets_at_epoch=now_epoch + 3600,
        limit_type="seven_day",
        now_epoch=now_epoch,
    )

    async def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("external bindings must not poll Anthropic OAuth usage")

    monkeypatch.setattr(admission, "_fetch_quota", unexpected_fetch)

    decision = await admission.admit_quota(
        config=config,
        credential_scope=None,
        binding_scope=scope,
        provider="external-provider",
        diagnostic_log_root=tmp_path,
        deadline_monotonic=time.monotonic() + 5,
    )

    assert decision.admitted is False
    assert decision.reason == "observed_quota_blocked"
    assert decision.rate_limit.resets_at_epoch == now_epoch + 3600
