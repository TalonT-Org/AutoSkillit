from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest
import structlog.testing

from autoskillit._probe_canary import (
    N_CONSECUTIVE_FLAKE_GUARD,
    CanaryIssueUpdater,
    CanaryState,
    ErrorKind,
    _cli_main,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestCanaryStateLoad:
    def test_load_absent_file_returns_zero_state(self, tmp_path: Path) -> None:
        state = CanaryState.load(tmp_path / "nonexistent.json")
        assert state.network_streak == 0
        assert state.schema_streak == 0
        assert state.last_issue_number is None

    def test_load_existing_file(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        p.write_text(
            json.dumps(
                {
                    "network_streak": 2,
                    "schema_streak": 1,
                    "last_issue_number": 42,
                }
            )
        )
        state = CanaryState.load(p)
        assert state.network_streak == 2
        assert state.schema_streak == 1
        assert state.last_issue_number == 42

    def test_load_corrupt_file_returns_zero_state(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        p.write_text("NOT JSON{{{")
        state = CanaryState.load(p)
        assert state.network_streak == 0
        assert state.schema_streak == 0
        assert state.last_issue_number is None


class TestCanaryStateSave:
    def test_save_creates_file(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        state = CanaryState(network_streak=3, schema_streak=1)
        state.save(p)
        assert p.exists()
        raw = json.loads(p.read_text())
        assert raw["network_streak"] == 3

    def test_save_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        original = CanaryState(network_streak=5, schema_streak=2, last_issue_number=99)
        original.save(p)
        loaded = CanaryState.load(p)
        assert loaded.network_streak == original.network_streak
        assert loaded.schema_streak == original.schema_streak
        assert loaded.last_issue_number == original.last_issue_number


class TestCanaryStateTransitions:
    def test_record_failure_network(self) -> None:
        state = CanaryState()
        state.record_failure(ErrorKind.NETWORK)
        assert state.network_streak == 1
        assert state.schema_streak == 0

    def test_record_failure_schema(self) -> None:
        state = CanaryState()
        state.record_failure(ErrorKind.SCHEMA)
        assert state.schema_streak == 1
        assert state.network_streak == 0

    def test_record_failure_accumulates(self) -> None:
        state = CanaryState()
        state.record_failure(ErrorKind.NETWORK)
        state.record_failure(ErrorKind.NETWORK)
        state.record_failure(ErrorKind.NETWORK)
        assert state.network_streak == 3

    def test_record_success_resets_all(self) -> None:
        state = CanaryState(network_streak=5, schema_streak=3)
        state.record_success()
        assert state.network_streak == 0
        assert state.schema_streak == 0


class TestShouldReport:
    def test_below_guard_returns_false(self) -> None:
        state = CanaryState(network_streak=N_CONSECUTIVE_FLAKE_GUARD - 1)
        assert state.should_report() is False

    def test_at_guard_returns_true(self) -> None:
        state = CanaryState(network_streak=N_CONSECUTIVE_FLAKE_GUARD)
        assert state.should_report() is True

    def test_above_guard_returns_true(self) -> None:
        state = CanaryState(schema_streak=N_CONSECUTIVE_FLAKE_GUARD + 1)
        assert state.should_report() is True

    def test_custom_guard(self) -> None:
        state = CanaryState(network_streak=5)
        assert state.should_report(flake_guard=5) is True
        assert state.should_report(flake_guard=6) is False

    def test_either_kind_triggers(self) -> None:
        state = CanaryState(network_streak=0, schema_streak=N_CONSECUTIVE_FLAKE_GUARD)
        assert state.should_report() is True


class TestErrorKind:
    def test_values(self) -> None:
        assert set(ErrorKind) == {ErrorKind.NETWORK, ErrorKind.SCHEMA}


class TestCanaryIssueUpdater:
    def test_ensure_issue_creates_new(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run_gh(args, **kwargs):
            if args[0:2] == ["issue", "list"]:
                return CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
            if args[0:2] == ["issue", "create"]:
                assert "--body-file" in args
                return CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=json.dumps({"number": 123}),
                    stderr="",
                )
            return CompletedProcess(args=args, returncode=1, stdout="", stderr="")

        monkeypatch.setattr("autoskillit._probe_canary.run_gh", mock_run_gh)
        updater = CanaryIssueUpdater(owner="test-org", repo="test-repo")
        state = CanaryState()
        num = updater.ensure_issue(state, "Probe failure", "Details here")
        assert num == 123
        assert state.last_issue_number == 123

    def test_ensure_issue_updates_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run_gh(args, **kwargs):
            if args[0:2] == ["issue", "list"]:
                return CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=json.dumps([{"number": 42, "title": "Probe failure"}]),
                    stderr="",
                )
            if args[0:2] == ["issue", "edit"]:
                assert "--body-file" in args
                return CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            return CompletedProcess(args=args, returncode=1, stdout="", stderr="")

        monkeypatch.setattr("autoskillit._probe_canary.run_gh", mock_run_gh)
        updater = CanaryIssueUpdater(owner="test-org", repo="test-repo")
        state = CanaryState()
        num = updater.ensure_issue(state, "Probe failure", "Updated body")
        assert num == 42
        assert state.last_issue_number == 42

    def test_ensure_issue_raises_on_create_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run_gh(args, **kwargs):
            if args[0:2] == ["issue", "list"]:
                return CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
            if args[0:2] == ["issue", "create"]:
                return CompletedProcess(args=args, returncode=1, stdout="", stderr="auth error")
            raise AssertionError(f"Unexpected gh call: {args}")

        monkeypatch.setattr("autoskillit._probe_canary.run_gh", mock_run_gh)
        updater = CanaryIssueUpdater(owner="test-org", repo="test-repo")
        state = CanaryState()
        with pytest.raises(RuntimeError, match="gh issue create failed"):
            updater.ensure_issue(state, "Probe failure", "Details")

    def test_ensure_issue_logs_on_edit_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_run_gh(args, **kwargs):
            if args[0:2] == ["issue", "list"]:
                return CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=json.dumps([{"number": 42, "title": "Probe failure"}]),
                    stderr="",
                )
            if args[0:2] == ["issue", "edit"]:
                assert "--body-file" in args
                return CompletedProcess(args=args, returncode=1, stdout="", stderr="locked")
            raise AssertionError(f"Unexpected gh call: {args}")

        monkeypatch.setattr("autoskillit._probe_canary.run_gh", mock_run_gh)
        updater = CanaryIssueUpdater(owner="test-org", repo="test-repo")
        state = CanaryState()
        with structlog.testing.capture_logs() as cap_logs:
            num = updater.ensure_issue(state, "Probe failure", "Updated body")
        assert num == 42
        assert state.last_issue_number == 42
        assert any(e.get("event") == "canary_issue_edit_failed" for e in cap_logs)


class TestCliMain:
    def test_post_failure_records_and_saves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """post-failure writes updated state to the state file."""
        monkeypatch.setenv("GITHUB_REPOSITORY", "test-org/test-repo")
        state_path = tmp_path / "state.json"

        def mock_run_gh(args, **kwargs):
            return CompletedProcess(args=args, returncode=1, stdout="", stderr="")

        monkeypatch.setattr("autoskillit._probe_canary.run_gh", mock_run_gh)

        rc = _cli_main(
            [
                "post-failure",
                "--state-file",
                str(state_path),
                "--backend",
                "claude-code",
                "--cli-version",
                "1.0.0",
                "--failure-type",
                "network",
                "--workflow-run-url",
                "https://example.com/run/1",
            ]
        )
        assert rc == 0
        assert state_path.exists()
        raw = json.loads(state_path.read_text())
        assert raw["network_streak"] == 1

    def test_post_failure_threshold_triggers_issue_creation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When streak reaches N_CONSECUTIVE_FLAKE_GUARD, ensure_issue is called."""
        monkeypatch.setenv("GITHUB_REPOSITORY", "test-org/test-repo")
        state_path = tmp_path / "state.json"
        state_path.write_text(
            json.dumps(
                {
                    "network_streak": N_CONSECUTIVE_FLAKE_GUARD - 1,
                    "schema_streak": 0,
                    "last_issue_number": None,
                }
            )
        )

        gh_calls: list[list[str]] = []

        def mock_run_gh(args, **kwargs):
            gh_calls.append(list(args))
            if args[0:2] == ["issue", "list"]:
                return CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
            if args[0:2] == ["issue", "create"]:
                return CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=json.dumps({"number": 7}),
                    stderr="",
                )
            return CompletedProcess(args=args, returncode=1, stdout="", stderr="")

        monkeypatch.setattr("autoskillit._probe_canary.run_gh", mock_run_gh)

        rc = _cli_main(
            [
                "post-failure",
                "--state-file",
                str(state_path),
                "--backend",
                "claude-code",
                "--cli-version",
                "1.0.0",
                "--failure-type",
                "network",
                "--workflow-run-url",
                "https://example.com/run/2",
            ]
        )
        assert rc == 0
        assert any(call[0:2] == ["issue", "list"] for call in gh_calls)
        assert any(call[0:2] == ["issue", "create"] for call in gh_calls)

    def test_post_failure_below_threshold_no_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Below threshold, no gh issue commands are issued."""
        monkeypatch.setenv("GITHUB_REPOSITORY", "test-org/test-repo")
        state_path = tmp_path / "state.json"

        def mock_run_gh(args, **kwargs):
            raise AssertionError(f"Unexpected gh call: {args}")

        monkeypatch.setattr("autoskillit._probe_canary.run_gh", mock_run_gh)

        rc = _cli_main(
            [
                "post-failure",
                "--state-file",
                str(state_path),
                "--backend",
                "claude-code",
                "--cli-version",
                "1.0.0",
                "--failure-type",
                "network",
                "--workflow-run-url",
                "https://example.com/run/3",
            ]
        )
        assert rc == 0
        assert state_path.exists()
        raw = json.loads(state_path.read_text())
        assert raw["network_streak"] == 1

    def test_post_failure_missing_github_repository(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing GITHUB_REPOSITORY env var returns 1."""
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        state_path = tmp_path / "state.json"

        rc = _cli_main(
            [
                "post-failure",
                "--state-file",
                str(state_path),
                "--backend",
                "claude-code",
                "--cli-version",
                "1.0.0",
                "--failure-type",
                "network",
                "--workflow-run-url",
                "https://example.com/run/4",
            ]
        )
        assert rc == 1

    def test_cli_no_command_returns_nonzero(self) -> None:
        """Calling _cli_main with no subcommand returns non-zero."""
        rc = _cli_main([])
        assert rc != 0
