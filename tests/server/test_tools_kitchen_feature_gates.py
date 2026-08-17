"""Tests for open_kitchen subset re-disable (T-VIS-003 / T-VIS-004 / T-VIS-005).

The companion `_redisable_subsets` unit tests live in the kept
`test_tools_kitchen_visibility.py` (Group I).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# T-VIS-003
@pytest.mark.anyio
async def test_open_kitchen_redisables_subsets(tmp_path, monkeypatch):
    """open_kitchen must call ctx.disable_components for each disabled subset."""
    from autoskillit.config.settings import AutomationConfig, SubsetsConfig

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.disable_components = AsyncMock()
    mock_ctx.config = AutomationConfig(subsets=SubsetsConfig(disabled=["github", "ci"]))

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    await open_kitchen(ctx=mock_ctx)

    disable_calls = mock_ctx.disable_components.call_args_list
    disabled_tags = [
        c.kwargs.get("tags") or (c.args[0] if c.args else None) for c in disable_calls
    ]
    assert {"github"} in disabled_tags
    assert {"ci"} in disabled_tags


# T-VIS-004
@pytest.mark.anyio
async def test_open_kitchen_redisable_order(tmp_path, monkeypatch):
    """ctx.disable_components must be called after ctx.enable_components (order matters)."""
    from autoskillit.config.settings import AutomationConfig, SubsetsConfig

    monkeypatch.chdir(tmp_path)
    call_order = []
    mock_ctx = _make_mock_ctx()

    async def record_enable(**kwargs):
        call_order.append(("enable", kwargs))

    async def record_disable(**kwargs):
        call_order.append(("disable", kwargs))

    mock_ctx.enable_components = record_enable
    mock_ctx.disable_components = record_disable
    mock_ctx.config = AutomationConfig(subsets=SubsetsConfig(disabled=["github"]))

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    await open_kitchen(ctx=mock_ctx)

    enable_idx = next(i for i, (op, _) in enumerate(call_order) if op == "enable")
    disable_idx = next(i for i, (op, _) in enumerate(call_order) if op == "disable")
    assert enable_idx < disable_idx, "disable_components must be called after enable_components"


# T-VIS-005
@pytest.mark.anyio
async def test_open_kitchen_keeps_disabled_feature_tags_hidden_when_subsets_empty(
    tmp_path, monkeypatch
):
    """An empty subset list still keeps disabled feature tags hidden."""
    from autoskillit.config.settings import AutomationConfig, SubsetsConfig

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.disable_components = AsyncMock()
    mock_ctx.config = AutomationConfig(
        subsets=SubsetsConfig(disabled=[]), features={"fleet": True}
    )

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    await open_kitchen(ctx=mock_ctx)

    mock_ctx.disable_components.assert_called_once_with(tags={"exploration"})
