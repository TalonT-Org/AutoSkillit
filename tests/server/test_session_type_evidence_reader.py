"""Evidence-reader startup-identity visibility tests.

The original `TestEvidenceReaderBindingVisibility` did not define a
`_reset_mcp_visibility` class-level fixture; this file follows that pattern.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


class TestEvidenceReaderBindingVisibility:
    """Evidence-reader startup identity grants only its two broker tools."""

    @staticmethod
    def _set_complete_identity(monkeypatch: pytest.MonkeyPatch) -> None:
        from autoskillit.core import (
            EVIDENCE_READER_AUTHORITY_ENV_VAR,
            EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR,
            EVIDENCE_READER_CAPABILITY_ENV_VAR,
        )

        monkeypatch.setenv(EVIDENCE_READER_AUTHORITY_ENV_VAR, "sha256:" + ("a" * 64))
        monkeypatch.setenv(EVIDENCE_READER_CAPABILITY_ENV_VAR, "reader-capability")
        monkeypatch.setenv(EVIDENCE_READER_AUTHORITY_PATH_ENV_VAR, "/sealed/authority.json")

    @pytest.mark.anyio
    async def test_complete_startup_identity_reveals_exact_reader_surface(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock

        from autoskillit.core import EVIDENCE_READER_TOOLS, SessionType
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import _lifespan, mcp
        from autoskillit.server.tools import _evidence_reader

        self._set_complete_identity(monkeypatch)
        validate = Mock()
        monkeypatch.setattr(_evidence_reader, "validate_evidence_reader_startup", validate)
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        ordinary_boot = AsyncMock()
        monkeypatch.setitem(_lifespan._LIFESPAN_BOOT_REGISTRY, SessionType.SKILL, ordinary_boot)
        gate = DefaultGateState()

        await _lifespan._run_lifespan_session_boot(SimpleNamespace(gate=gate))

        visible = {tool.name for tool in await mcp.list_tools()}
        assert (
            visible
            == EVIDENCE_READER_TOOLS
            == {
                "read_authorized_artifact",
                "get_authorized_artifact_page",
            }
        )
        assert visible.isdisjoint(
            {
                "delegate_evidence_reader",
                "open_kitchen",
                "close_kitchen",
                "run_cmd",
                "run_python",
                "run_skill",
                "fetch_github_issue",
                "submit_exploration_query",
            }
        )
        assert list(await mcp.list_resources()) == []
        assert list(await mcp.list_resource_templates()) == []
        assert gate.enabled is True
        ordinary_boot.assert_not_awaited()
        validate.assert_called_once()

    @pytest.mark.anyio
    async def test_complete_but_unauthenticated_identity_aborts_startup(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import Mock

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import _lifespan
        from autoskillit.server.tools import _evidence_reader

        self._set_complete_identity(monkeypatch)
        monkeypatch.setattr(
            _evidence_reader,
            "validate_evidence_reader_startup",
            Mock(side_effect=_evidence_reader.EvidenceReaderError("authority_tampered")),
        )
        gate = DefaultGateState()

        with pytest.raises(_evidence_reader.EvidenceReaderError, match="authority_tampered"):
            await _lifespan._run_lifespan_session_boot(SimpleNamespace(gate=gate))

        assert gate.enabled is False

    @pytest.mark.anyio
    async def test_complete_identity_with_zero_matching_tools_aborts_startup(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import Mock

        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import _lifespan, mcp
        from autoskillit.server.tools import _evidence_reader

        self._set_complete_identity(monkeypatch)
        monkeypatch.setattr(
            _evidence_reader,
            "validate_evidence_reader_startup",
            Mock(),
        )

        async def no_visible_tools():
            return []

        monkeypatch.setattr(mcp, "list_tools", no_visible_tools)
        gate = DefaultGateState()

        with pytest.raises(RuntimeError, match="tool projection is incomplete"):
            await _lifespan._run_lifespan_session_boot(SimpleNamespace(gate=gate))

        assert gate.enabled is False

    @pytest.mark.anyio
    async def test_complete_ambient_identity_does_not_reveal_brokers_before_boot(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autoskillit.core import EVIDENCE_READER_TOOLS
        from autoskillit.server import _apply_session_type_visibility, mcp

        self._set_complete_identity(monkeypatch)
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

        _apply_session_type_visibility()

        visible = {tool.name for tool in await mcp.list_tools()}
        assert visible.isdisjoint(EVIDENCE_READER_TOOLS)

    @pytest.mark.parametrize("identity", ["partial", "empty"])
    @pytest.mark.anyio
    async def test_malformed_startup_identity_fails_closed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        identity: str,
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from autoskillit.core import (
            EVIDENCE_READER_AUTHORITY_ENV_VAR,
            EVIDENCE_READER_CAPABILITY_ENV_VAR,
            EVIDENCE_READER_ENV_FORWARD_VARS,
            EVIDENCE_READER_TOOLS,
            SessionType,
        )
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import _lifespan, mcp

        for name in EVIDENCE_READER_ENV_FORWARD_VARS:
            monkeypatch.delenv(name, raising=False)
        if identity == "partial":
            monkeypatch.setenv(EVIDENCE_READER_AUTHORITY_ENV_VAR, "sha256:" + ("a" * 64))
        else:
            self._set_complete_identity(monkeypatch)
            monkeypatch.setenv(EVIDENCE_READER_CAPABILITY_ENV_VAR, "")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        ordinary_boot = AsyncMock()
        monkeypatch.setitem(_lifespan._LIFESPAN_BOOT_REGISTRY, SessionType.SKILL, ordinary_boot)
        gate = DefaultGateState()

        with pytest.raises(RuntimeError, match="startup identity is malformed"):
            await _lifespan._run_lifespan_session_boot(SimpleNamespace(gate=gate))

        assert gate.enabled is False
        ordinary_boot.assert_not_awaited()
        assert {tool.name for tool in await mcp.list_tools()}.isdisjoint(EVIDENCE_READER_TOOLS)

    @pytest.mark.parametrize("gate_enabled", [False, True])
    @pytest.mark.anyio
    async def test_absent_reader_identity_preserves_ordinary_kitchen_boot(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gate_enabled: bool,
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from autoskillit.core import (
            EVIDENCE_READER_ENV_FORWARD_VARS,
            EVIDENCE_READER_TOOLS,
            SessionType,
        )
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import _lifespan, mcp

        for name in EVIDENCE_READER_ENV_FORWARD_VARS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        gate = DefaultGateState()
        if gate_enabled:
            gate.enable()
        ordinary_boot = AsyncMock()
        monkeypatch.setitem(_lifespan._LIFESPAN_BOOT_REGISTRY, SessionType.SKILL, ordinary_boot)

        await _lifespan._run_lifespan_session_boot(
            SimpleNamespace(gate=gate, exploration_context_store=None)
        )

        ordinary_boot.assert_awaited_once()
        assert gate.enabled is gate_enabled
        assert {tool.name for tool in await mcp.list_tools()}.isdisjoint(EVIDENCE_READER_TOOLS)
