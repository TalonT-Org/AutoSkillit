"""A production-shaped steady-state home starts the MCP server without a WARNING."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

import autoskillit.core.paths as _core_paths
import autoskillit.execution.quota._quota_gate as _quota_gate
from autoskillit.core import ArtifactLease, managed_home_for, read_retiring_cache
from autoskillit.hook_registry import render_hooks_json_text
from autoskillit.server.lifecycle import _lifespan
from tests._helpers import _flush_structlog_proxy_caches
from tests.fixtures.plugin_artifact_state import write_marketplace_surfaces, write_registry
from tests.fixtures.startup_steady_state import (
    DORMANT_QUOTA_WINDOW,
    enqueue_projection_retirement,
    fake_quota_http_client,
    plant_stale_projection,
    write_migrated_legacy_evidence,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_WARNING_LEVELS = frozenset({"warning", "error", "critical"})


@pytest.fixture
def steady_state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Seed a cook-only home with every routine state a real machine accumulates."""
    from autoskillit.cli.install._plugin_artifact import default_plugin_retirement_coordinator

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pkg_root = tmp_path / "pkg"
    (pkg_root / "hooks").mkdir(parents=True)
    (pkg_root / "hooks" / "hooks.json").write_text(render_hooks_json_text())
    monkeypatch.setattr(_core_paths, "pkg_root", lambda: pkg_root)
    monkeypatch.setattr(_lifespan, "iter_all_scope_paths", lambda project_root=None: iter(()))

    write_marketplace_surfaces(tmp_path, "0.0.1-stale")
    write_migrated_legacy_evidence(tmp_path, tmp_path / "cache" / "legacy")
    _, retiring, _, _, _ = plant_stale_projection(tmp_path, "retiring-key")
    enqueue_projection_retirement(
        tmp_path, retiring, not_before=datetime.now(UTC) - timedelta(hours=1)
    )
    _, _, _, _, live_lease_path = plant_stale_projection(tmp_path, "live-key")

    coordinator = default_plugin_retirement_coordinator(home=managed_home_for(tmp_path))
    monkeypatch.setattr(
        _lifespan,
        "_get_ctx_or_none",
        lambda: SimpleNamespace(plugin_retirement_coordinator=coordinator),
    )
    api_response = {
        "nimbus_quill": DORMANT_QUOTA_WINDOW,
        "five_hour": DORMANT_QUOTA_WINDOW,
        "seven_day": DORMANT_QUOTA_WINDOW,
    }
    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: fake_quota_http_client(api_response))
    monkeypatch.setattr(_quota_gate, "_read_credentials", lambda path: "fake-token")

    with ArtifactLease.acquire_shared(live_lease_path, timeout=2.0):
        yield tmp_path


async def _run_startup_checks() -> None:
    await asyncio.gather(
        _lifespan._run_drift_check_async(),
        _lifespan._run_retiring_sweep_async(),
        _lifespan._run_hook_health_check_async(),
        _lifespan._run_install_state_check_async(),
    )


async def _fetch_quota() -> _quota_gate.QuotaFetchResult:
    from autoskillit.config.settings import QuotaGuardConfig

    cfg = QuotaGuardConfig()
    return await _quota_gate._fetch_quota(
        cfg.credentials_path,
        short_threshold=cfg.short_window_threshold,
        long_threshold=cfg.long_window_threshold,
        long_patterns=cfg.long_window_patterns,
        short_enabled=cfg.short_window_enabled,
        long_enabled=cfg.long_window_enabled,
    )


def _events(logs: Sequence[Mapping[str, Any]], event: str) -> list[Mapping[str, Any]]:
    return [entry for entry in logs if entry.get("event") == event]


@pytest.mark.anyio
async def test_steady_state_startup_emits_no_warning(steady_state_home: Path) -> None:
    """A production-shaped steady-state home starts with no WARNING+ record.

    The capture is proven live by a lower-level witness from every module whose
    silence is asserted, and by the negative control below.
    """
    home = steady_state_home
    projections_root = home / ".autoskillit" / "plugin-projections"
    live_hooks = projections_root / "live-key" / "hooks" / "hooks.json"
    live_hooks_before = live_hooks.read_bytes()

    _flush_structlog_proxy_caches()
    try:
        with structlog.testing.capture_logs() as logs:
            await _run_startup_checks()
            result = await _fetch_quota()
    finally:
        _flush_structlog_proxy_caches()

    loud = [entry for entry in logs if entry.get("log_level") in _WARNING_LEVELS]
    assert loud == [], f"steady-state startup emitted WARNING+ records: {loud}"

    assert _events(logs, "startup_drift_check_ok")

    assert ("reclaim", "succeeded") in [
        (entry["action"], entry["outcome"])
        for entry in _events(logs, "plugin_artifact_lifecycle")
        if entry["semantic_key"] == "retiring-key"
    ]
    assert not (projections_root / "retiring-key").exists()
    assert not [
        record
        for record in read_retiring_cache(home=managed_home_for(home)).records
        if record.managed_path.name == "retiring-key"
    ]

    contended = _events(logs, "projection_hooks_repair_contended_at_startup")
    assert [(entry["incarnation"], entry["log_level"]) for entry in contended] == [
        (str(projections_root / "live-key"), "debug")
    ]
    assert live_hooks.read_bytes() == live_hooks_before
    assert not [
        entry
        for entry in logs
        if str(entry.get("event", "")).startswith("projection_hooks_")
        and "retiring-key" in str(entry.get("incarnation", ""))
    ]

    assert [
        (entry["log_level"], entry["check"]) for entry in _events(logs, "install_state_diagnostic")
    ] == [("info", "retiring_cache_legacy_evidence")]
    assert not [entry for entry in logs if str(entry.get("check", "")).startswith("marketplace_")]

    assert "nimbus_quill" not in result.windows
    assert result.binding.window_name in _quota_gate.KNOWN_QUOTA_WINDOW_NAMES
    assert [
        (entry["log_level"], entry["dormant_windows"])
        for entry in _events(logs, "quota_dormant_windows_ignored")
    ] == [("debug", ["nimbus_quill"])]


@pytest.mark.anyio
async def test_actionable_install_finding_still_warns(steady_state_home: Path) -> None:
    """Negative control: the same steady-state home plus one actionable finding warns once.

    The full steady-state fixture is deliberate — the only difference from the silent
    startup above is the dangling registry entry, so the exact WARNING list also proves
    the co-present routine state (migrated legacy evidence, stale marketplace surfaces)
    stays quiet alongside an actionable finding.
    """
    write_registry(steady_state_home, steady_state_home / "gone")

    _flush_structlog_proxy_caches()
    try:
        with structlog.testing.capture_logs() as logs:
            await _lifespan._run_install_state_check_async()
    finally:
        _flush_structlog_proxy_caches()

    loud = [entry for entry in logs if entry.get("log_level") in _WARNING_LEVELS]
    assert [(entry["event"], entry["check"]) for entry in loud] == [
        ("install_state_inconsistent", "generation_store_missing")
    ]
    assert "`autoskillit install`" in loud[0]["message"]
    assert "remediation" not in loud[0]
