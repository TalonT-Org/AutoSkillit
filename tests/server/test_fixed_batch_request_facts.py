"""Reason-bearing managed fixed-batch admission failures."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from autoskillit.core import (
    MANAGED_JOIN_PARENT_ID_ENV_VAR,
    ManagedJoinRefusalReason,
    ManagedJoinVerificationRefusal,
    SkillContractError,
)
from autoskillit.execution.backends import CodexBackend
from autoskillit.server._managed_join_attestation import DefaultManagedJoinAttestationAuthority
from autoskillit.server.tools.tools_execution._fixed_batch_handlers import _request_facts

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_request_facts_surfaces_home_drift_refusal(
    tool_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = DefaultManagedJoinAttestationAuthority()
    refusal = ManagedJoinVerificationRefusal(
        ManagedJoinRefusalReason.HOME_DRIFT,
        ("managed Codex config has the wrong resolved model (attested 'a', found 'b')",),
    )
    monkeypatch.setattr(authority, "find_verified_context", lambda **_kwargs: refusal)
    monkeypatch.setattr(tool_ctx, "backend", CodexBackend())
    monkeypatch.setattr(tool_ctx, "managed_join_attestation_authority", authority)
    monkeypatch.setattr(tool_ctx, "managed_fixed_batch_supervisor", object())
    monkeypatch.setenv(MANAGED_JOIN_PARENT_ID_ENV_VAR, "parent123")

    with pytest.raises(SkillContractError) as caught:
        _request_facts(
            skill_name="some-skill",
            request_context=SimpleNamespace(session_id="request123"),
            tool_ctx=tool_ctx,
        )

    message = str(caught.value)
    assert "requires a current server-issued attestation" in message
    assert "home_drift" in message
    assert "found 'b'" in message
    assert "restart the AutoSkillit session to re-attest" in message
