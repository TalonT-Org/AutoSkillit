"""Protocol satisfaction tests — Group Five (issue #1523).

Covers: OutputPatternResolver, WriteExpectedResolver, QuotaRefreshTask,
TokenFactory, CampaignProtector.

Tests 7–9 require WP2 (@runtime_checkable on CampaignProtector) to pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ── WP3-1: OutputPatternResolver ──────────────────────────────────────────────


def test_output_pattern_resolver_satisfied_by_minimal_callable():
    from autoskillit.core import OutputPatternResolver

    class _Resolver:
        def __call__(self, skill_command: str) -> list[str]:
            return []

    assert isinstance(_Resolver(), OutputPatternResolver)


# ── WP3-2: WriteExpectedResolver ──────────────────────────────────────────────


def test_write_expected_resolver_satisfied_by_minimal_callable():
    from autoskillit.core import WriteExpectedResolver

    class _Resolver:
        def __call__(self, skill_command: str) -> None:
            pass

    assert isinstance(_Resolver(), WriteExpectedResolver)


# ── WP3-3: QuotaRefreshTask — asyncio.Task ────────────────────────────────────


@pytest.mark.anyio
async def test_quota_refresh_task_satisfied_by_asyncio_task():
    """asyncio.Task has a cancel() method — satisfies QuotaRefreshTask."""
    import asyncio

    from autoskillit.core import QuotaRefreshTask

    async def _noop() -> None:
        pass

    task = asyncio.ensure_future(_noop())
    try:
        assert isinstance(task, QuotaRefreshTask)
    finally:
        task.cancel()


# ── WP3-4: QuotaRefreshTask — minimal cancel class ────────────────────────────


def test_quota_refresh_task_satisfied_by_minimal_cancel_class():
    from autoskillit.core import QuotaRefreshTask

    class _MinimalTask:
        def cancel(self, msg: object = None) -> bool:
            return True

    assert isinstance(_MinimalTask(), QuotaRefreshTask)


# ── WP3-5: TokenFactory — concrete server class ───────────────────────────────


def test_token_factory_satisfied_by_server_concrete_class():
    from autoskillit.core import TokenFactory
    from autoskillit.server._factory import _LazyTokenFactory as ConcreteTokenFactory

    instance = ConcreteTokenFactory(lambda: None)
    assert isinstance(instance, TokenFactory)


# ── WP3-6: TokenFactory — minimal callable ────────────────────────────────────


def test_token_factory_satisfied_by_minimal_callable():
    from autoskillit.core import TokenFactory

    class _MinimalFactory:
        def __call__(self) -> str | None:
            return None

    assert isinstance(_MinimalFactory(), TokenFactory)


# ── WP3-7: CampaignProtector — is runtime_checkable ──────────────────────────


def test_campaign_protector_is_runtime_checkable():
    """Requires WP2: @runtime_checkable added to CampaignProtector."""
    from autoskillit.core import CampaignProtector

    assert getattr(CampaignProtector, "_is_runtime_protocol", False), (
        "CampaignProtector must be decorated with @runtime_checkable"
    )


# ── WP3-8: CampaignProtector — build_protected_campaign_ids ──────────────────


def test_campaign_protector_satisfied_by_build_protected_campaign_ids():
    """Requires WP2: @runtime_checkable added to CampaignProtector."""
    from autoskillit.core import CampaignProtector
    from autoskillit.fleet import build_protected_campaign_ids

    assert isinstance(build_protected_campaign_ids, CampaignProtector)


# ── WP3-9: CampaignProtector — minimal callable ───────────────────────────────


def test_campaign_protector_satisfied_by_minimal_callable():
    """Requires WP2: @runtime_checkable added to CampaignProtector."""
    from autoskillit.core import CampaignProtector

    class _MinimalProtector:
        def __call__(self, project_dir: Path) -> frozenset[str]:
            return frozenset()

    assert isinstance(_MinimalProtector(), CampaignProtector)
