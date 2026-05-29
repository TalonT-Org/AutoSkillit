"""Tests for tools_kitchen.py: gate toggle, review gate cleanup, kitchen_id, misc."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.hooks.formatters._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# T2a
@pytest.mark.anyio
async def test_open_kitchen_enables_gate(tmp_path, monkeypatch):
    """After _open_kitchen_handler(), gate is enabled."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import _open_kitchen_handler

                    await _open_kitchen_handler()

    mock_ctx.gate.enable.assert_called_once()


# T2b
def test_close_kitchen_disables_gate(tmp_path, monkeypatch):
    """After _close_kitchen_handler(), gate is disabled."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    mock_ctx.gate.disable.assert_called_once()


# T2c
def test_close_kitchen_no_file_no_error(tmp_path, monkeypatch):
    """_close_kitchen_handler() doesn't raise when no gate file exists."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()  # Should not raise

    # Gate file was never created — confirm it still does not exist
    assert not tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS).exists()


# ---------------------------------------------------------------------------
# Group J — T7, T8, T5, alwaysLoad MCP meta
# ---------------------------------------------------------------------------


def test_kitchen_failure_envelope_hint_says_install_not_reinstall() -> None:
    from autoskillit.server.tools.tools_kitchen import _kitchen_failure_envelope

    result = json.loads(_kitchen_failure_envelope(exc=RuntimeError("x"), stage="test"))
    msg = result["user_visible_message"]
    assert "autoskillit install" in msg
    assert "reinstall" not in msg


def test_display_categories_omits_fleet_when_disabled() -> None:
    """Fleet category must not appear in iter_display_categories output when fleet is disabled."""
    from autoskillit.config import iter_display_categories

    cfg_features: dict[str, bool] = {"fleet": False}
    categories = [name for name, _ in iter_display_categories(cfg_features)]
    assert "Fleet" not in categories


def test_display_categories_includes_fleet_when_enabled() -> None:
    """Fleet category must appear in iter_display_categories output when fleet is enabled."""
    from autoskillit.config import iter_display_categories

    cfg_features: dict[str, bool] = {"fleet": True}
    categories = [name for name, _ in iter_display_categories(cfg_features)]
    assert "Fleet" in categories


# ---------------------------------------------------------------------------
# T5: close_kitchen cleans up review_gate_state.json
# ---------------------------------------------------------------------------

_REVIEW_GATE_STATE_RELPATH = (".autoskillit", "temp", "review_gate_state.json")


# T5-1
def test_close_kitchen_preserves_review_gate_when_loop_active(tmp_path, monkeypatch):
    """Preserve review_gate_state.json when an active review loop is in progress."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    state_path = tmp_path.joinpath(*_REVIEW_GATE_STATE_RELPATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "gate": "LOOP_REQUIRED",
                "review_verdict": "changes_requested",
                "check_review_loop_called": False,
                "pr_number": "1290",
                "set_at": "2026-04-26T04:30:00+00:00",
            }
        )
    )
    assert state_path.exists(), "State file must exist before close_kitchen"

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    assert state_path.exists(), "Active review loop state must survive close_kitchen"


# T5-2
def test_close_kitchen_no_review_gate_state_no_error(tmp_path, monkeypatch):
    """_close_kitchen_handler() must not raise when review_gate_state.json is absent."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()  # Must not raise

    assert not tmp_path.joinpath(*_REVIEW_GATE_STATE_RELPATH).exists()


# T5-3
def test_close_kitchen_removes_review_gate_when_loop_complete(tmp_path, monkeypatch):
    """Remove review_gate_state.json when check_review_loop_called is True."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    state_path = tmp_path.joinpath(*_REVIEW_GATE_STATE_RELPATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "gate": "LOOP_REQUIRED",
                "review_verdict": "changes_requested",
                "check_review_loop_called": True,
                "pr_number": "1290",
                "set_at": "2026-04-26T04:30:00+00:00",
            }
        )
    )

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    assert not state_path.exists(), "Completed loop state must be cleaned up on close"


# T5-4
def test_close_kitchen_removes_review_gate_when_gate_not_loop_required(tmp_path, monkeypatch):
    """_close_kitchen_handler() must remove review_gate_state.json when gate != LOOP_REQUIRED."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    state_path = tmp_path.joinpath(*_REVIEW_GATE_STATE_RELPATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "gate": "CLEAR",
                "check_review_loop_called": False,
                "pr_number": "1290",
                "set_at": "2026-04-26T04:30:00+00:00",
            }
        )
    )

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    assert not state_path.exists(), "Non-LOOP_REQUIRED gate state must be cleaned up on close"


# T5-5
def test_close_kitchen_removes_review_gate_on_corrupt_json(tmp_path, monkeypatch):
    """Delete review_gate_state.json when JSON is malformed (fail-safe)."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    state_path = tmp_path.joinpath(*_REVIEW_GATE_STATE_RELPATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{not valid json")

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    assert not state_path.exists(), "Corrupt gate state must be deleted (fail-safe)"


@pytest.mark.anyio
async def test_open_kitchen_has_always_load_meta() -> None:
    """open_kitchen must carry anthropic/alwaysLoad: true in its MCP meta.

    alwaysLoad ensures open_kitchen is always in the initial tool context for
    direct 'claude' sessions (where ToolSearch is enabled). Defense-in-depth
    against the MCP first-call race for non-order/cook sessions.
    """
    from autoskillit.server import mcp

    tool = await mcp.get_tool("open_kitchen")
    assert tool is not None
    assert tool.meta is not None and tool.meta.get("anthropic/alwaysLoad") is True, (
        "open_kitchen missing anthropic/alwaysLoad:true — add to @mcp.tool(meta={...})"
    )


# ---------------------------------------------------------------------------
# Triple-ID unification: kitchen_id inherits AUTOSKILLIT_CAMPAIGN_ID
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_kitchen_inherits_campaign_id_from_env(tmp_path, monkeypatch):
    """When AUTOSKILLIT_CAMPAIGN_ID is in env, open_kitchen uses it as kitchen_id."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_CAMPAIGN_ID", "abc123def456")
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import _open_kitchen_handler

                    await _open_kitchen_handler()

    assert mock_ctx.kitchen_id == "abc123def456"


@pytest.mark.anyio
async def test_open_kitchen_generates_uuid_without_campaign_env(tmp_path, monkeypatch):
    """Without AUTOSKILLIT_CAMPAIGN_ID, open_kitchen generates a fresh UUID."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import _open_kitchen_handler

                    await _open_kitchen_handler()

    kitchen_id = mock_ctx.kitchen_id
    assert len(kitchen_id) == 36 and kitchen_id.count("-") == 4


# ---------------------------------------------------------------------------
# Kitchen-close orphan drain
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_kitchen_drains_orphaned_github_api_entries(tmp_path, monkeypatch):
    """Entries accumulated after last run_skill are flushed to disk at close."""
    import json

    from autoskillit.pipeline.github_api_log import DefaultGitHubApiLog

    monkeypatch.chdir(tmp_path)
    log = DefaultGitHubApiLog()
    await log.record_httpx(
        method="GET",
        path="/repos/o/r/issues/1",
        status_code=200,
        latency_ms=10.0,
        rate_limit_remaining=4999,
        rate_limit_used=1,
        rate_limit_reset=0,
        timestamp="2026-04-27T10:00:00Z",
    )

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.github_api_log = log
    mock_ctx.kitchen_id = "test-kitchen-123"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    mock_ctx.config.linux_tracing.log_dir = str(log_dir)

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen.resolve_log_dir", return_value=log_dir
            ):
                from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

                _close_kitchen_handler()

    orphan_path = log_dir / "github_api_usage_orchestrator.json"
    assert orphan_path.exists()
    data = json.loads(orphan_path.read_text())
    assert data["total_requests"] == 1
    assert not log._entries
