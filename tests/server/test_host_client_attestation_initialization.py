"""Single-read contract for host client attestation initialization.

``initialize_host_client_attestation()`` is the sole authorized reader of
``os.environ`` for the launcher-injected attestation env vars — it is called
once, from ``make_context()``. ``get_context_host_client_attestation()`` must
only ever return the cached value (or ``None`` before initialization); it
must never fall back to reading ``os.environ`` itself, since that would let a
context-uninitialized caller observe a live env value the composition root
never sanctioned.
"""

from __future__ import annotations

import pytest

from autoskillit.core import (
    AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS,
    AUTOSKILLIT_ATTESTED_META_SUPPORT,
)
from autoskillit.server import _recipe_delivery
from autoskillit.server._recipe_delivery import (
    get_context_host_client_attestation,
    initialize_host_client_attestation,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _reset_context_attestation_state() -> None:
    """Restore module-global attestation cache after each test.

    ``initialize_host_client_attestation()`` mutates process-global state
    (``_CONTEXT_HOST_CLIENT_ATTESTATION`` / ``..._INITIALIZED``); leaving it
    set would leak into unrelated tests sharing this xdist worker.
    """
    yield
    _recipe_delivery._CONTEXT_HOST_CLIENT_ATTESTATION = None
    _recipe_delivery._CONTEXT_HOST_CLIENT_ATTESTATION_INITIALIZED = False


def test_absent_env_vars_initialize_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No launcher-injected env vars → initialization resolves to None."""
    monkeypatch.delenv(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS, raising=False)
    monkeypatch.delenv(AUTOSKILLIT_ATTESTED_META_SUPPORT, raising=False)

    result = initialize_host_client_attestation()

    assert result is None
    assert get_context_host_client_attestation() is None


def test_non_numeric_gate_tokens_initialize_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed gate-tokens value must not raise — it resolves to None."""
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS, "not_a_number")
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_META_SUPPORT, "1")

    result = initialize_host_client_attestation()

    assert result is None
    assert get_context_host_client_attestation() is None


def test_non_boolean_meta_support_initialize_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A meta-support value outside {'0', '1'} must resolve to None."""
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS, "50000")
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_META_SUPPORT, "invalid")

    result = initialize_host_client_attestation()

    assert result is None
    assert get_context_host_client_attestation() is None


def test_non_positive_gate_tokens_initialize_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero or negative gate-tokens value must resolve to None."""
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS, "0")
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_META_SUPPORT, "1")

    result = initialize_host_client_attestation()

    assert result is None
    assert get_context_host_client_attestation() is None


def test_get_context_before_initialization_returns_none_without_reading_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before ``initialize_host_client_attestation()`` runs, the getter must
    return None rather than falling back to a live ``os.environ`` read —
    even when the env vars are well-formed and present.
    """
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS, "50000")
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_META_SUPPORT, "1")

    assert _recipe_delivery._CONTEXT_HOST_CLIENT_ATTESTATION_INITIALIZED is False
    assert get_context_host_client_attestation() is None


def test_get_context_after_initialization_returns_cached_value_not_live_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once initialized, the getter must serve the cached snapshot even if
    the env vars subsequently change — proving it does not reread ``os.environ``.
    """
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS, "50000")
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_META_SUPPORT, "1")
    initialized = initialize_host_client_attestation()
    assert initialized is not None
    assert initialized.attested_client_gate_tokens == 50000
    assert initialized.annotation_support is True

    # Mutate the env after initialization — the cached value must not move.
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS, "1")
    monkeypatch.setenv(AUTOSKILLIT_ATTESTED_META_SUPPORT, "0")

    cached = get_context_host_client_attestation()
    assert cached is initialized
    assert cached.attested_client_gate_tokens == 50000
    assert cached.annotation_support is True
