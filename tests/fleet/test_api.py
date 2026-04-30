"""Tests for fleet._api module (Group J)."""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog.testing

from autoskillit.fleet import (
    DispatchRecord,
    _write_pid,
    write_initial_state,
)

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "campaign" / "state.json"


def _make_dispatches(*names: str) -> list[DispatchRecord]:
    return [DispatchRecord(name=n) for n in names]


class TestWritePidExceptionSwallow:
    def test_nonexistent_state_logs_warning(self, tmp_path: Path) -> None:
        bogus = tmp_path / "nope" / "state.json"
        with structlog.testing.capture_logs() as logs:
            _write_pid(bogus, "d1", "id1", 123, 0)
        assert any(
            "_write_pid" in entry.get("event", "")
            for entry in logs
            if entry.get("log_level") == "warning"
        )

    def test_runtime_error_logs_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sp = _state_path(tmp_path)
        write_initial_state(sp, "cid", "camp", "/m.yaml", _make_dispatches("d1"))
        monkeypatch.setattr(
            "autoskillit.fleet.mark_dispatch_running",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with structlog.testing.capture_logs() as logs:
            _write_pid(sp, "d1", "id1", 123, 0)
        assert any(
            "_write_pid" in entry.get("event", "")
            for entry in logs
            if entry.get("log_level") == "warning"
        )
