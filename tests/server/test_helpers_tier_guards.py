"""Tests for tier-aware guard helpers in server.helpers."""

from __future__ import annotations

import json
import warnings

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# _require_orchestrator_or_higher
# ---------------------------------------------------------------------------


def test_A1_require_orchestrator_or_higher_permits_interactive(monkeypatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    from autoskillit.server.helpers import _require_orchestrator_or_higher

    assert _require_orchestrator_or_higher("run_cmd") is None


def test_A2_require_orchestrator_or_higher_permits_headless_orchestrator(monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    from autoskillit.server.helpers import _require_orchestrator_or_higher

    assert _require_orchestrator_or_higher("run_cmd") is None


def test_A3_require_orchestrator_or_higher_permits_headless_franchise(monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
    from autoskillit.server.helpers import _require_orchestrator_or_higher

    assert _require_orchestrator_or_higher("run_cmd") is None


def test_A4_require_orchestrator_or_higher_denies_headless_leaf(monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
    from autoskillit.server.helpers import _require_orchestrator_or_higher

    result = _require_orchestrator_or_higher("run_cmd")
    assert result is not None
    data = json.loads(result)
    assert data["subtype"] == "headless_error"


def test_A5_require_orchestrator_or_higher_denies_headless_unset_session_type(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    from autoskillit.server.helpers import _require_orchestrator_or_higher

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = _require_orchestrator_or_higher("run_cmd")
    assert result is not None
    data = json.loads(result)
    assert data["subtype"] == "headless_error"


def test_A6_require_orchestrator_or_higher_denies_headless_invalid_session_type(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "bogus")
    from autoskillit.server.helpers import _require_orchestrator_or_higher

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        result = _require_orchestrator_or_higher("run_cmd")
    assert result is not None
    data = json.loads(result)
    assert data["subtype"] == "headless_error"


# ---------------------------------------------------------------------------
# _require_orchestrator_exact
# ---------------------------------------------------------------------------


def test_A7_require_orchestrator_exact_permits_interactive(monkeypatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    from autoskillit.server.helpers import _require_orchestrator_exact

    assert _require_orchestrator_exact("open_kitchen") is None


def test_A8_require_orchestrator_exact_permits_headless_orchestrator(monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    from autoskillit.server.helpers import _require_orchestrator_exact

    assert _require_orchestrator_exact("open_kitchen") is None


def test_A9_require_orchestrator_exact_denies_headless_franchise(monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
    from autoskillit.server.helpers import _require_orchestrator_exact

    result = _require_orchestrator_exact("open_kitchen")
    assert result is not None
    data = json.loads(result)
    assert data["subtype"] == "headless_error"
    msg = data.get("result", "").lower()
    assert "fleet" in msg or "franchise" in msg


def test_A10_require_orchestrator_exact_denies_headless_leaf(monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
    from autoskillit.server.helpers import _require_orchestrator_exact

    result = _require_orchestrator_exact("open_kitchen")
    assert result is not None
    data = json.loads(result)
    assert data["subtype"] == "headless_error"


# ---------------------------------------------------------------------------
# _require_franchise
# ---------------------------------------------------------------------------


def test_A11_require_franchise_permits_franchise_session(monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "franchise")
    from autoskillit.server.helpers import _require_franchise

    assert _require_franchise() is None


def test_A12_require_franchise_denies_orchestrator(monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")
    from autoskillit.server.helpers import _require_franchise

    result = _require_franchise()
    assert result is not None
    data = json.loads(result)
    assert data["subtype"] == "headless_error"


def test_A13_require_franchise_denies_interactive_no_session_type(monkeypatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    from autoskillit.server.helpers import _require_franchise

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = _require_franchise()
    assert result is not None
    data = json.loads(result)
    assert data["subtype"] == "headless_error"
