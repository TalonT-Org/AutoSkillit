"""Server-side invariant tests for run_python: recipe-read prohibition."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from autoskillit.server._guards import RECIPE_READ_DENY_TRIGGER
from autoskillit.server.tools.tools_execution import run_python

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestRecipeReadProhibitionCallable:
    """run_python denies autoskillit.recipe.* callables in headless sessions."""

    @pytest.fixture(autouse=True)
    def _headless(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")

    @pytest.mark.anyio
    async def test_denies_recipe_callable(self, tool_ctx_kitchen_open):
        result = json.loads(await run_python(callable="autoskillit.recipe.load_recipe"))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"
        assert RECIPE_READ_DENY_TRIGGER in result["result"]

    @pytest.mark.anyio
    async def test_denies_recipe_submodule_callable(self, tool_ctx_kitchen_open):
        result = json.loads(await run_python(callable="autoskillit.recipe.schema.validate"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_allows_cmd_rpc_callable(self, tool_ctx_kitchen_open, monkeypatch):
        mock = AsyncMock(return_value={"success": True, "result": "ok"})
        monkeypatch.setattr("autoskillit.server.tools.tools_execution._import_and_call", mock)
        result = json.loads(await run_python(callable="autoskillit.recipe._cmd_rpc"))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_allows_cmd_rpc_batch_callable(self, tool_ctx_kitchen_open, monkeypatch):
        mock = AsyncMock(return_value={"success": True, "result": "ok"})
        monkeypatch.setattr("autoskillit.server.tools.tools_execution._import_and_call", mock)
        result = json.loads(await run_python(callable="autoskillit.recipe._cmd_rpc_batch"))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_allows_non_recipe_callable(self, tool_ctx_kitchen_open, monkeypatch):
        mock = AsyncMock(return_value={"success": True, "result": "ok"})
        monkeypatch.setattr("autoskillit.server.tools.tools_execution._import_and_call", mock)
        result = json.loads(await run_python(callable="autoskillit.core.paths.pkg_root"))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_allows_in_non_headless(self, tool_ctx_kitchen_open, monkeypatch):
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        mock = AsyncMock(return_value={"success": True, "result": "ok"})
        monkeypatch.setattr("autoskillit.server.tools.tools_execution._import_and_call", mock)
        result = json.loads(await run_python(callable="autoskillit.recipe.load_recipe"))
        assert result["success"] is True
