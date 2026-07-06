"""Tests: fleet run command gates — session guard, feature gates, CLAUDECODE relaxation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import FleetErrorCode
from autoskillit.fleet import (
    DispatchCompleted,
    DispatchRejected,
    DispatchResult,
    DispatchStatus,
)

pytestmark = [
    pytest.mark.layer("cli"),
    pytest.mark.medium,
    pytest.mark.feature("fleet"),
]


def _make_test_config(
    *, fleet: bool = False, fleet_headless_run: bool = False, experimental_enabled: bool = False
) -> object:
    """Build a lightweight config mock for `load_config` substitute."""
    return type(
        "C",
        (),
        {
            "features": {"fleet": fleet, "fleet_headless_run": fleet_headless_run},
            "experimental_enabled": experimental_enabled,
        },
    )()


def _mock_success_result(dispatch_id="test-dispatch-001", session_id="test-session-001"):
    return DispatchResult(
        outcome=DispatchCompleted(
            success=True,
            dispatch_status=DispatchStatus.SUCCESS,
            dispatch_id=dispatch_id,
            dispatched_session_id=session_id,
            reason="completed",
        ),
    )


def _mock_failure_result(status=DispatchStatus.FAILURE):
    return DispatchResult(
        outcome=DispatchCompleted(
            success=False,
            dispatch_status=status,
            dispatch_id="test-dispatch-002",
            dispatched_session_id="test-session-002",
            reason="failed",
        ),
    )


def _mock_rejected_result():
    return DispatchResult(
        outcome=DispatchRejected(
            error_code=FleetErrorCode.FLEET_RECIPE_NOT_FOUND,
            message="recipe not found",
        ),
    )


class TestFleetRunGates:
    def test_fleet_run_blocks_in_leaf_session(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """fleet_run exits with FLEET_SESSION_TYPE_BLOCKED when SESSION_TYPE=leaf."""
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit) as exc_info:
            fleet_run("test-recipe")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert envelope["success"] is False
        assert envelope["error"] == "FLEET_SESSION_TYPE_BLOCKED"

    def test_fleet_run_blocks_in_skill_session(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """fleet_run exits with FLEET_SESSION_TYPE_BLOCKED when SESSION_TYPE=skill."""
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit) as exc_info:
            fleet_run("test-recipe")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert envelope["success"] is False
        assert envelope["error"] == "FLEET_SESSION_TYPE_BLOCKED"

    def test_fleet_run_allows_claudecode_env(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """CLAUDECODE env var is NOT a blocker — fleet_run proceeds past it to the feature gate."""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(
                fleet=True, fleet_headless_run=False, experimental_enabled=True
            ),
        )
        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit) as exc_info:
            fleet_run("test-recipe")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        # Must NOT be a CLAUDECODE error — must be a feature gate error
        assert "CLAUDECODE" not in captured.out
        assert envelope["error"] == "FLEET_FEATURE_DISABLED"

    def test_fleet_run_exits_when_feature_disabled(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """fleet_run exits with JSON FLEET_FEATURE_DISABLED when fleet_headless_run is disabled."""
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        # Config: fleet=True, fleet_headless_run=False. experimental_enabled must be False too
        # so the feature is genuinely rejected (otherwise the blanket would promote it).
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(
                fleet=True, fleet_headless_run=False, experimental_enabled=False
            ),
        )
        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit) as exc_info:
            fleet_run("test-recipe")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert envelope["success"] is False
        assert envelope["error"] == "FLEET_FEATURE_DISABLED"
        assert "fleet_headless_run" in envelope["user_visible_message"]

    def test_fleet_run_exits_when_fleet_disabled(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """fleet_run exits with JSON FLEET_FEATURE_DISABLED when base fleet feature is disabled."""
        monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        # Config: fleet=False, fleet_headless_run=False. experimental_enabled False.
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(
                fleet=False, fleet_headless_run=False, experimental_enabled=False
            ),
        )
        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit) as exc_info:
            fleet_run("test-recipe")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert envelope["success"] is False
        assert envelope["error"] == "FLEET_FEATURE_DISABLED"
        assert "fleet" in envelope["user_visible_message"].lower()


class TestFleetRunDispatch:
    """Tests for the Part B dispatch body."""

    def test_fleet_run_parses_ingredients(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--ingredient k1=v1 --ingredient k2=v2 produces {"k1": "v1", "k2": "v2"}."""
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        captured_args: dict[str, object] = {}

        async def fake_execute(**kwargs: object) -> DispatchResult:
            captured_args.update(kwargs)
            return _mock_success_result()

        with patch(
            "autoskillit.cli.fleet._fleet_run._execute_fleet_run",
            new=AsyncMock(side_effect=fake_execute),
        ):
            from autoskillit.cli.fleet import fleet_run

            with pytest.raises(SystemExit):
                fleet_run(
                    "test-recipe",
                    ingredient=("key1=val1", "key2=val2"),
                    task="test task",
                )
        assert captured_args["ingredients"] == {"key1": "val1", "key2": "val2"}

    def test_fleet_run_rejects_invalid_ingredient(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """--ingredient without = sign produces JSON error."""
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit):
            fleet_run("test-recipe", ingredient=("noequals",), task="test")
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["success"] is False
        assert envelope["error"] == "FLEET_INVALID_ARGUMENT"

    def test_fleet_run_resolves_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--backend claude-code resolves to a backend with name == claude-code."""
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        fake_backend = MagicMock()
        fake_backend.name = "claude-code"
        monkeypatch.setattr(
            "autoskillit.server.resolve_backend_override",
            lambda name: fake_backend,
        )
        captured_args: dict[str, object] = {}

        async def fake_execute(**kwargs: object) -> DispatchResult:
            captured_args.update(kwargs)
            return _mock_success_result()

        with patch(
            "autoskillit.cli.fleet._fleet_run._execute_fleet_run",
            new=AsyncMock(side_effect=fake_execute),
        ):
            from autoskillit.cli.fleet import fleet_run

            with pytest.raises(SystemExit):
                fleet_run("test-recipe", backend="claude-code", task="test")
        assert captured_args["dispatch_backend"] is fake_backend

    def test_fleet_run_rejects_invalid_backend(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """--backend unknown-backend produces JSON error."""
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )

        def fake_resolve(name: str) -> object:
            raise ValueError(f"Unknown backend {name!r}. Valid names: claude-code, codex")

        monkeypatch.setattr(
            "autoskillit.server.resolve_backend_override",
            fake_resolve,
        )
        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit):
            fleet_run("test-recipe", backend="invalid", task="test")
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["success"] is False
        assert envelope["error"] == "FLEET_INVALID_BACKEND"

    def test_fleet_run_exit_code_success(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Exit code 0 when dispatch succeeds."""
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        with patch(
            "autoskillit.cli.fleet._fleet_run._execute_fleet_run",
            new=AsyncMock(return_value=_mock_success_result()),
        ):
            from autoskillit.cli.fleet import fleet_run

            with pytest.raises(SystemExit) as exc:
                fleet_run("test-recipe", task="test")
        assert exc.value.code == 0
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["success"] is True

    def test_fleet_run_exit_code_failure(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Exit code 1 when dispatch fails."""
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        with patch(
            "autoskillit.cli.fleet._fleet_run._execute_fleet_run",
            new=AsyncMock(return_value=_mock_failure_result(status=DispatchStatus.FAILURE)),
        ):
            from autoskillit.cli.fleet import fleet_run

            with pytest.raises(SystemExit) as exc:
                fleet_run("test-recipe", task="test")
        assert exc.value.code == 1
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["success"] is False

    def test_fleet_run_exit_code_resumable(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Exit code 2 when dispatch is resumable — distinct from general failure."""
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        with patch(
            "autoskillit.cli.fleet._fleet_run._execute_fleet_run",
            new=AsyncMock(return_value=_mock_failure_result(status=DispatchStatus.RESUMABLE)),
        ):
            from autoskillit.cli.fleet import fleet_run

            with pytest.raises(SystemExit) as exc:
                fleet_run("test-recipe", task="test")
        assert exc.value.code == 2
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["dispatch_status"] == "resumable"

    def test_fleet_run_exit_code_rejected(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Exit code 3 when dispatch is rejected pre-launch."""
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        with patch(
            "autoskillit.cli.fleet._fleet_run._execute_fleet_run",
            new=AsyncMock(return_value=_mock_rejected_result()),
        ):
            from autoskillit.cli.fleet import fleet_run

            with pytest.raises(SystemExit) as exc:
                fleet_run("test-recipe", task="test")
        assert exc.value.code == 3
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["kind"] == "rejected"

    def test_fleet_run_prints_json_envelope(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Stdout contains valid JSON with dispatch_id, dispatched_session_id, dispatch_status."""
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        with patch(
            "autoskillit.cli.fleet._fleet_run._execute_fleet_run",
            new=AsyncMock(return_value=_mock_success_result()),
        ):
            from autoskillit.cli.fleet import fleet_run

            with pytest.raises(SystemExit):
                fleet_run("test-recipe", task="test")
        envelope = json.loads(capsys.readouterr().out)
        assert "dispatch_id" in envelope
        assert "dispatched_session_id" in envelope
        assert "dispatch_status" in envelope

    def test_fleet_run_disable_quota_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--disable-quota-guard passes a no-op quota checker."""
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )

        from autoskillit.execution import check_and_sleep_if_needed

        fake_ctx = MagicMock()
        fake_ctx.backend = None
        fake_ctx.config = MagicMock()
        monkeypatch.setattr(
            "autoskillit.server.make_context",
            lambda cfg, **kwargs: fake_ctx,
        )

        captured: dict[str, object] = {}

        async def fake_execute_dispatch(**kwargs: object) -> DispatchResult:
            captured["quota_checker"] = kwargs["quota_checker"]
            captured["quota_refresher"] = kwargs["quota_refresher"]
            return _mock_success_result()

        monkeypatch.setattr(
            "autoskillit.fleet.execute_dispatch",
            fake_execute_dispatch,
        )

        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit):
            fleet_run("test-recipe", task="test", disable_quota_guard=True)

        quota_checker = captured["quota_checker"]
        assert quota_checker is not check_and_sleep_if_needed

    def test_fleet_run_passes_resume_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--resume-session-id and --prior-dispatch-id are forwarded to execute_dispatch."""
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        captured_args: dict[str, object] = {}

        async def fake_execute(**kwargs: object) -> DispatchResult:
            captured_args.update(kwargs)
            return _mock_success_result()

        with patch(
            "autoskillit.cli.fleet._fleet_run._execute_fleet_run",
            new=AsyncMock(side_effect=fake_execute),
        ):
            from autoskillit.cli.fleet import fleet_run

            with pytest.raises(SystemExit):
                fleet_run(
                    "test-recipe",
                    task="test",
                    resume_session_id="sess-123",
                    prior_dispatch_id="disp-456",
                )
        assert captured_args["resume_session_id"] == "sess-123"
        assert captured_args["prior_dispatch_id"] == "disp-456"

    def test_fleet_run_uses_fleet_semaphore(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ToolContext is constructed via make_context — FleetSemaphore auto-wired."""
        monkeypatch.setattr(
            "autoskillit.config.load_config",
            lambda path=None: _make_test_config(fleet=True, fleet_headless_run=True),
        )
        make_context_calls: list[object] = []
        fake_ctx = MagicMock()
        fake_ctx.backend = None
        fake_ctx.config = MagicMock()
        fake_ctx.fleet_lock = MagicMock()

        def fake_make_context(cfg: object, **kwargs: object) -> MagicMock:
            make_context_calls.append(cfg)
            return fake_ctx

        monkeypatch.setattr(
            "autoskillit.server.make_context",
            fake_make_context,
        )

        async def fake_execute_dispatch(**kwargs: object) -> DispatchResult:
            return _mock_success_result()

        monkeypatch.setattr(
            "autoskillit.fleet.execute_dispatch",
            fake_execute_dispatch,
        )

        from autoskillit.cli.fleet import fleet_run

        with pytest.raises(SystemExit):
            fleet_run("test-recipe", task="test")

        assert len(make_context_calls) == 1
        assert fake_ctx.fleet_lock is not None
