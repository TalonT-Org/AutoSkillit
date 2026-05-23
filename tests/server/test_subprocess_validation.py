"""Tests for _run_subprocess cwd validation."""

from __future__ import annotations

import pytest

from autoskillit.server._subprocess import _run_subprocess
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestRunSubprocessCwdValidation:
    @pytest.mark.anyio
    async def test_rejects_empty_cwd(self, tool_ctx_kitchen_open):
        with pytest.raises(ValueError, match="cwd must not be empty"):
            await _run_subprocess(["echo", "hi"], cwd="", timeout=10)

    @pytest.mark.anyio
    async def test_accepts_relative_cwd(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(0, "ok\n", ""))
        rc, stdout, _ = await _run_subprocess(["echo", "hi"], cwd=".", timeout=10)
        assert rc == 0

    @pytest.mark.anyio
    async def test_accepts_valid_absolute_cwd(self, tool_ctx_kitchen_open, tmp_path):
        tool_ctx_kitchen_open.runner.push(_make_result(0, "ok\n", ""))
        rc, stdout, _ = await _run_subprocess(["echo", "hi"], cwd=str(tmp_path), timeout=10)
        assert rc == 0
        assert stdout == "ok\n"

    @pytest.mark.anyio
    async def test_rejects_nonexistent_cwd(self, tool_ctx_kitchen_open):
        with pytest.raises(ValueError, match="does not exist"):
            await _run_subprocess(["echo", "hi"], cwd="/nonexistent", timeout=10)
