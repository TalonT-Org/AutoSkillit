"""Context ownership contract for host client attestation initialization.

``initialize_host_client_attestation()`` is the sole authorized reader of
``os.environ`` for the launcher-injected attestation env vars — it is called
once per ``make_context()`` call and its result is owned by that ToolContext.
"""

from __future__ import annotations

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import (
    AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS,
    AUTOSKILLIT_ATTESTED_META_SUPPORT,
)
from autoskillit.server._factory import make_context
from autoskillit.server._recipe_delivery import initialize_host_client_attestation

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_absent_env_vars_initialize_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No launcher-injected env vars → initialization resolves to None."""
    monkeypatch.delenv(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS, raising=False)
    monkeypatch.delenv(AUTOSKILLIT_ATTESTED_META_SUPPORT, raising=False)

    result = initialize_host_client_attestation()

    assert result is None


def test_non_numeric_gate_tokens_initialize_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed gate-tokens value must not raise — it resolves to None."""
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS, "not_a_number")
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_META_SUPPORT, "1")

    result = initialize_host_client_attestation()

    assert result is None


def test_non_boolean_meta_support_initialize_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A meta-support value outside {'0', '1'} must resolve to None."""
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS, "50000")
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_META_SUPPORT, "invalid")

    result = initialize_host_client_attestation()

    assert result is None


def test_non_positive_gate_tokens_initialize_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero or negative gate-tokens value must resolve to None."""
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS, "0")
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_META_SUPPORT, "1")

    result = initialize_host_client_attestation()

    assert result is None


def test_contexts_keep_their_own_attestation_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS, "50000")
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_META_SUPPORT, "1")
    first = make_context(AutomationConfig(), runner=None, project_dir=tmp_path)

    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS, "1")
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_META_SUPPORT, "0")
    second = make_context(AutomationConfig(), runner=None, project_dir=tmp_path)

    assert first.host_client_attestation is not None
    assert first.host_client_attestation.attested_client_gate_tokens == 50000
    assert first.host_client_attestation.annotation_support is True
    assert second.host_client_attestation is not None
    assert second.host_client_attestation.attested_client_gate_tokens == 1
    assert second.host_client_attestation.annotation_support is False
