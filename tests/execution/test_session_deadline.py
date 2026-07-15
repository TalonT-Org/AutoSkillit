"""Tests for AUTOSKILLIT_SESSION_DEADLINE propagation from run_skill to L1 sessions.

L1 (non-fleet) sessions receive AUTOSKILLIT_SESSION_DEADLINE in their environment
when the orchestrator's order overlay configures a `timeout`. Fleet/food-truck
sessions inherit the deadline via env_extras from fleet/_api.py; interactive
order sessions must compute the deadline in run_skill before the executor runs
the subprocess.

The deadline flows into provider_extras (passed to executor.run()) and is also
cached in os.environ so downstream hooks (quota_guard, quota_post_hook) can
observe the wall-clock budget.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.fakes import InMemoryHeadlessExecutor

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _write_overlay(project_dir: Path, order_section: dict) -> Path:
    """Write a minimal overlay file with the given order section.

    Mirrors what `configure_order` produces via _write_session_config.
    """
    from autoskillit.server._misc import _hook_config_overlay_path, _hook_config_path

    hook_path = _hook_config_path(project_dir)
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(json.dumps({"features": {"fleet": True}}))

    overlay_path = _hook_config_overlay_path(project_dir)
    overlay_path.write_text(json.dumps({"order": order_section}))
    return overlay_path


@pytest.fixture
def clean_deadline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure AUTOSKILLIT_SESSION_DEADLINE is absent before each test."""
    monkeypatch.delenv("AUTOSKILLIT_SESSION_DEADLINE", raising=False)


class TestRunSkillSessionDeadlinePropagation:
    """run_skill must propagate AUTOSKILLIT_SESSION_DEADLINE to provider_extras."""

    @pytest.mark.anyio
    async def test_order_timeout_propagates_deadline_to_provider_extras(
        self, tool_ctx_kitchen_open, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When order.timeout is set, run_skill must inject AUTOSKILLIT_SESSION_DEADLINE
        into provider_extras with a value approximately time.time() + timeout."""
        # Configure an order overlay with a 1800s timeout.
        _write_overlay(tmp_path, {"timeout": 1800})
        monkeypatch.delenv("AUTOSKILLIT_SESSION_DEADLINE", raising=False)

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        tool_ctx_kitchen_open.project_dir = tmp_path
        # skill_resolver=None makes target_name=None, bypassing the resolver gates.
        tool_ctx_kitchen_open.skill_resolver = None

        from autoskillit.server.tools.tools_execution import run_skill

        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

        before = time.time()
        await run_skill("/test skill", str(tmp_path))
        after = time.time()

        assert executor.calls, "executor.run was never called"
        provider_extras = executor.calls[0].provider_extras
        assert provider_extras is not None, (
            "provider_extras must be a dict (not None) when order.timeout is configured"
        )
        assert "AUTOSKILLIT_SESSION_DEADLINE" in provider_extras, (
            f"Expected AUTOSKILLIT_SESSION_DEADLINE in provider_extras, got {provider_extras!r}"
        )

        deadline_value = float(provider_extras["AUTOSKILLIT_SESSION_DEADLINE"])
        expected_min = before + 1800 - 5  # 5s slack
        expected_max = after + 1800 + 5
        assert expected_min <= deadline_value <= expected_max, (
            f"Deadline {deadline_value} not within [time.time()+1800 ±5]. "
            f"Expected range [{expected_min}, {expected_max}], "
            f"before={before}, after={after}."
        )

    @pytest.mark.anyio
    async def test_no_timeout_leaves_deadline_absent(
        self, tool_ctx_kitchen_open, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no order.timeout is configured, AUTOSKILLIT_SESSION_DEADLINE must be absent
        from provider_extras and not injected into os.environ."""
        # Overlay exists but with no timeout key.
        _write_overlay(tmp_path, {"idle_output_timeout": 60})
        monkeypatch.delenv("AUTOSKILLIT_SESSION_DEADLINE", raising=False)

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.skill_resolver = None

        from autoskillit.server.tools.tools_execution import run_skill

        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

        await run_skill("/test skill", str(tmp_path))

        assert executor.calls, "executor.run was never called"
        provider_extras = executor.calls[0].provider_extras
        # provider_extras may be None or {} — either is acceptable, but no deadline key.
        if provider_extras is not None:
            assert "AUTOSKILLIT_SESSION_DEADLINE" not in provider_extras, (
                f"AUTOSKILLIT_SESSION_DEADLINE must be absent when no order.timeout "
                f"is configured. Got provider_extras={provider_extras!r}"
            )
        # And not injected into os.environ.
        import os

        assert "AUTOSKILLIT_SESSION_DEADLINE" not in os.environ, (
            "AUTOSKILLIT_SESSION_DEADLINE must not leak into os.environ when no timeout "
            "is configured"
        )

    @pytest.mark.anyio
    async def test_existing_deadline_preserved_for_fleet_session(
        self, tool_ctx_kitchen_open, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When AUTOSKILLIT_SESSION_DEADLINE is already set (e.g., fleet session),
        run_skill must NOT overwrite it — the inherited deadline wins."""
        existing_deadline = "1700000000"
        monkeypatch.setenv("AUTOSKILLIT_SESSION_DEADLINE", existing_deadline)

        _write_overlay(tmp_path, {"timeout": 1800})

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.skill_resolver = None

        from autoskillit.server.tools.tools_execution import run_skill

        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

        await run_skill("/test skill", str(tmp_path))

        assert executor.calls, "executor.run was never called"
        provider_extras = executor.calls[0].provider_extras
        assert provider_extras is not None
        assert provider_extras.get("AUTOSKILLIT_SESSION_DEADLINE") == existing_deadline, (
            "Fleet session's pre-existing AUTOSKILLIT_SESSION_DEADLINE must be "
            f"preserved unchanged. Got {provider_extras.get('AUTOSKILLIT_SESSION_DEADLINE')!r}, "
            f"expected {existing_deadline!r}"
        )

    @pytest.mark.anyio
    async def test_deadline_cached_in_environ_for_order_session(
        self, tool_ctx_kitchen_open, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When order.timeout is set, AUTOSKILLIT_SESSION_DEADLINE must also be
        cached in os.environ so downstream hooks (quota_guard, quota_post_hook)
        can read the deadline without needing provider_extras threaded through."""
        import os

        _write_overlay(tmp_path, {"timeout": 2400})
        monkeypatch.delenv("AUTOSKILLIT_SESSION_DEADLINE", raising=False)

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.skill_resolver = None

        from autoskillit.server.tools.tools_execution import run_skill

        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

        before = time.time()
        await run_skill("/test skill", str(tmp_path))
        after = time.time()

        assert "AUTOSKILLIT_SESSION_DEADLINE" in os.environ, (
            "run_skill must cache AUTOSKILLIT_SESSION_DEADLINE in os.environ when "
            "order.timeout is configured"
        )
        deadline_value = float(os.environ["AUTOSKILLIT_SESSION_DEADLINE"])
        # Same approx check as test 1.
        assert before + 2400 - 5 <= deadline_value <= after + 2400 + 5, (
            f"Cached env deadline {deadline_value} not within expected range"
        )

    @pytest.mark.anyio
    async def test_existing_deadline_does_not_get_re_cached(
        self, tool_ctx_kitchen_open, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When AUTOSKILLIT_SESSION_DEADLINE is already set (fleet session),
        os.environ value must NOT be re-cached with the order timeout-derived value.
        The inherited value wins and is preserved verbatim."""
        import os

        existing_deadline = "1700000000"
        monkeypatch.setenv("AUTOSKILLIT_SESSION_DEADLINE", existing_deadline)
        _write_overlay(tmp_path, {"timeout": 1800})

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.skill_resolver = None

        from autoskillit.server.tools.tools_execution import run_skill

        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

        await run_skill("/test skill", str(tmp_path))

        assert os.environ.get("AUTOSKILLIT_SESSION_DEADLINE") == existing_deadline, (
            "Pre-existing AUTOSKILLIT_SESSION_DEADLINE must be preserved in os.environ. "
            f"Got {os.environ.get('AUTOSKILLIT_SESSION_DEADLINE')!r}, "
            f"expected {existing_deadline!r}"
        )

    @pytest.mark.anyio
    async def test_no_overlay_file_skips_deadline_propagation(
        self, tool_ctx_kitchen_open, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no overlay file exists (kitchen never configured), run_skill must
        NOT raise and NOT inject a deadline."""
        import os

        # No overlay file written — kitchen was never opened/configured.
        monkeypatch.delenv("AUTOSKILLIT_SESSION_DEADLINE", raising=False)

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.skill_resolver = None

        from autoskillit.server.tools.tools_execution import run_skill

        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

        # Must not raise.
        await run_skill("/test skill", str(tmp_path))

        assert executor.calls, "executor.run was never called"
        provider_extras = executor.calls[0].provider_extras
        if provider_extras is not None:
            assert "AUTOSKILLIT_SESSION_DEADLINE" not in provider_extras
        assert "AUTOSKILLIT_SESSION_DEADLINE" not in os.environ

    @pytest.mark.anyio
    async def test_malformed_overlay_silently_skipped(
        self, tool_ctx_kitchen_open, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed overlay file must not crash run_skill — deadline propagation
        is silently skipped on JSONDecodeError."""
        import os

        from autoskillit.server._misc import _hook_config_overlay_path

        overlay_path = _hook_config_overlay_path(tmp_path)
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path.write_text("{ malformed json")
        monkeypatch.delenv("AUTOSKILLIT_SESSION_DEADLINE", raising=False)

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        tool_ctx_kitchen_open.project_dir = tmp_path
        tool_ctx_kitchen_open.skill_resolver = None

        from autoskillit.server.tools.tools_execution import run_skill

        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

        # Must not raise.
        await run_skill("/test skill", str(tmp_path))

        assert executor.calls, "executor.run was never called"
        provider_extras = executor.calls[0].provider_extras
        if provider_extras is not None:
            assert "AUTOSKILLIT_SESSION_DEADLINE" not in provider_extras
        assert "AUTOSKILLIT_SESSION_DEADLINE" not in os.environ
