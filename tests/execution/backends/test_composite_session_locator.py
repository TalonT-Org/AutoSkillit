from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.core import SessionLocator, SessionSummary
from autoskillit.execution.backends import CompositeSessionLocator

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _fake_backend(locator: SessionLocator) -> type:
    backend = Mock()
    backend.session_locator.return_value = locator
    cls = Mock(return_value=backend)
    return cls


class _StubLocator:
    def __init__(
        self,
        locate_result: Path | None = None,
        log_dir: Path | None = None,
        summaries: tuple[SessionSummary, ...] = (),
    ):
        self._locate = locate_result
        self._log_dir = log_dir or Path("/stub")
        self._summaries = summaries

    def locate_session(self, session_id: str) -> Path | None:
        return self._locate

    def project_log_dir(self, cwd: str) -> Path:
        return self._log_dir

    def session_log_path(self, cwd: str, session_id: str) -> Path | None:
        return self._locate

    def list_sessions(self, cwd: str) -> tuple[SessionSummary, ...]:
        return self._summaries


def _summary(backend_name: str, session_id: str, cwd: str) -> SessionSummary:
    return SessionSummary(
        backend_name=backend_name,
        session_id=session_id,
        launch_id=None,
        cwd=cwd,
        first_prompt="prompt",
        summary="summary",
        git_branch=None,
        modified=None,
        is_sidechain=False,
        session_type_hint="cook",
    )


class TestLocateSession:
    def test_returns_first_non_none(self, monkeypatch):
        hit = Path("/found/session.jsonl")
        registry = {
            "miss": _fake_backend(_StubLocator(None)),
            "hit": _fake_backend(_StubLocator(hit)),
        }
        import autoskillit.execution.backends as backends_mod

        monkeypatch.setattr(backends_mod, "BACKEND_REGISTRY", registry)

        assert CompositeSessionLocator().locate_session("sid-1") == hit

    def test_returns_none_when_all_miss(self, monkeypatch):
        registry = {"a": _fake_backend(_StubLocator(None))}
        import autoskillit.execution.backends as backends_mod

        monkeypatch.setattr(backends_mod, "BACKEND_REGISTRY", registry)

        assert CompositeSessionLocator().locate_session("sid-1") is None

    @pytest.mark.parametrize("sid", ["", "no_session_123", "crashed_456"])
    def test_guards_invalid_ids(self, sid):
        assert CompositeSessionLocator().locate_session(sid) is None

    def test_skips_failing_backend(self, monkeypatch):
        class _FailLocator:
            def locate_session(self, session_id):
                raise RuntimeError("backend unavailable")

            def project_log_dir(self, cwd):
                return Path("/stub")

            def session_log_path(self, cwd, session_id):
                return None

        hit = Path("/found/session.jsonl")
        registry = {
            "broken": _fake_backend(_FailLocator()),
            "good": _fake_backend(_StubLocator(hit)),
        }
        import autoskillit.execution.backends as backends_mod

        monkeypatch.setattr(backends_mod, "BACKEND_REGISTRY", registry)

        assert CompositeSessionLocator().locate_session("sid-1") == hit


class TestProjectLogDirFor:
    def test_dispatches_by_name(self, monkeypatch):
        expected = Path("/logs/claude")
        registry = {"claude-code": _fake_backend(_StubLocator(log_dir=expected))}
        import autoskillit.execution.backends as backends_mod

        monkeypatch.setattr(backends_mod, "BACKEND_REGISTRY", registry)

        assert CompositeSessionLocator().project_log_dir_for("/cwd", "claude-code") == expected

    def test_raises_for_unknown(self, monkeypatch):
        import autoskillit.execution.backends as backends_mod

        monkeypatch.setattr(backends_mod, "BACKEND_REGISTRY", {})

        with pytest.raises(ValueError, match="Unknown backend"):
            CompositeSessionLocator().project_log_dir_for("/cwd", "unknown")


class TestSessionLogPath:
    def test_delegates_to_locate_session(self, monkeypatch):
        hit = Path("/found/session.jsonl")
        registry = {"a": _fake_backend(_StubLocator(hit))}
        import autoskillit.execution.backends as backends_mod

        monkeypatch.setattr(backends_mod, "BACKEND_REGISTRY", registry)

        assert CompositeSessionLocator().session_log_path("/cwd", "sid") == hit


class TestListSessions:
    def test_preserves_backend_registry_and_source_order(self, monkeypatch, tmp_path):
        cwd = str(tmp_path.resolve())
        registry = {
            "claude-code": _fake_backend(
                _StubLocator(summaries=(_summary("claude-code", "claude-new", cwd),))
            ),
            "codex": _fake_backend(
                _StubLocator(
                    summaries=(
                        _summary("codex", "codex-new", cwd),
                        _summary("codex", "codex-old", cwd),
                    )
                )
            ),
        }
        import autoskillit.execution.backends as backends_mod

        monkeypatch.setattr(backends_mod, "BACKEND_REGISTRY", registry)

        result = CompositeSessionLocator().list_sessions(cwd)
        assert [(item.backend_name, item.session_id) for item in result] == [
            ("claude-code", "claude-new"),
            ("codex", "codex-new"),
            ("codex", "codex-old"),
        ]

    def test_skips_failing_locator_without_reordering_other_sources(self, monkeypatch, tmp_path):
        class _FailLocator(_StubLocator):
            def list_sessions(self, cwd):
                raise ValueError("corrupt backend index")

        cwd = str(tmp_path.resolve())
        registry = {
            "broken": _fake_backend(_FailLocator()),
            "codex": _fake_backend(_StubLocator(summaries=(_summary("codex", "survives", cwd),))),
        }
        import autoskillit.execution.backends as backends_mod

        monkeypatch.setattr(backends_mod, "BACKEND_REGISTRY", registry)

        assert [item.session_id for item in CompositeSessionLocator().list_sessions(cwd)] == [
            "survives"
        ]


class TestLocatorFor:
    def test_returns_backend_locator(self, monkeypatch):
        stub = _StubLocator(Path("/x"))
        registry = {"claude-code": _fake_backend(stub)}
        import autoskillit.execution.backends as backends_mod

        monkeypatch.setattr(backends_mod, "BACKEND_REGISTRY", registry)

        result = CompositeSessionLocator().locator_for("claude-code")
        assert result is stub

    def test_raises_for_unknown(self, monkeypatch):
        import autoskillit.execution.backends as backends_mod

        monkeypatch.setattr(backends_mod, "BACKEND_REGISTRY", {})

        with pytest.raises(ValueError, match="Unknown backend"):
            CompositeSessionLocator().locator_for("unknown")


class TestInvariants:
    def test_frozen(self):
        loc = CompositeSessionLocator()
        with pytest.raises((FrozenInstanceError, TypeError)):
            loc.x = 1  # type: ignore[attr-defined]

    def test_protocol_conformance(self):
        assert isinstance(CompositeSessionLocator(), SessionLocator)
