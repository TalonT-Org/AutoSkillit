from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest
import structlog.testing

import autoskillit._probe_canary as _patch_autoskillit__probe_canary
from autoskillit._probe_canary import (
    ISSUE_BODY_MAX_CHARS,
    N_CONSECUTIVE_FLAKE_GUARD,
    CanaryState,
    ErrorKind,
    IssueUpdater,
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


class TestIssueUpdater:
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

        monkeypatch.setattr(_patch_autoskillit__probe_canary, "run_gh", mock_run_gh)
        updater = IssueUpdater(owner="test-org", repo="test-repo")
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

        monkeypatch.setattr(_patch_autoskillit__probe_canary, "run_gh", mock_run_gh)
        updater = IssueUpdater(owner="test-org", repo="test-repo")
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

        monkeypatch.setattr(_patch_autoskillit__probe_canary, "run_gh", mock_run_gh)
        updater = IssueUpdater(owner="test-org", repo="test-repo")
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

        monkeypatch.setattr(_patch_autoskillit__probe_canary, "run_gh", mock_run_gh)
        updater = IssueUpdater(owner="test-org", repo="test-repo")
        state = CanaryState()
        with structlog.testing.capture_logs() as cap_logs:
            num = updater.ensure_issue(state, "Probe failure", "Updated body")
        assert num == 42
        assert state.last_issue_number == 42
        assert any(e.get("event") == "canary_issue_edit_failed" for e in cap_logs)


_E2E_TITLE = "[E2E] canary failure"


class _FakeGh:
    """Answer gh issue commands for one optional open issue; record argv and body files."""

    def __init__(
        self,
        *,
        existing: int | None = None,
        body: str = "old body",
        failing: frozenset[str] = frozenset(),
    ) -> None:
        self.existing = existing
        self.body = body
        self.failing = failing
        self.calls: list[list[str]] = []
        self.bodies: dict[str, str] = {}

    def __call__(self, args, **kwargs):
        args = list(args)
        self.calls.append(args)
        command = args[1]
        if "--body-file" in args:
            self.bodies[command] = Path(args[args.index("--body-file") + 1]).read_text()
        if command in self.failing:
            return CompletedProcess(args=args, returncode=1, stdout="", stderr=f"{command} boom")
        if command == "list":
            issues = (
                [] if self.existing is None else [{"number": self.existing, "title": _E2E_TITLE}]
            )
            return CompletedProcess(args=args, returncode=0, stdout=json.dumps(issues), stderr="")
        if command == "view":
            stdout = json.dumps({"body": self.body})
            return CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
        if command == "create":
            stdout = json.dumps({"number": 99})
            return CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    def commands(self) -> list[str]:
        return [call[1] for call in self.calls]


def _append(monkeypatch: pytest.MonkeyPatch, gh: _FakeGh, section: str = "### Section") -> int:
    monkeypatch.setattr(_patch_autoskillit__probe_canary, "run_gh", gh)
    updater = IssueUpdater(owner="test-org", repo="test-repo")
    return updater.append_to_issue(_E2E_TITLE, "Header", section)


class TestAppendToIssue:
    def test_no_open_issue_creates_one_with_header_and_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gh = _FakeGh()
        assert _append(monkeypatch, gh) == 99
        assert gh.commands() == ["list", "create"]
        assert gh.bodies["create"] == "Header\n\n### Section"

    def test_open_issue_gets_the_section_appended(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gh = _FakeGh(existing=42)
        assert _append(monkeypatch, gh) == 42
        assert gh.calls[1] == ["issue", "view", "42", "--repo", "test-org/test-repo"] + [
            "--json",
            "body",
        ]
        assert gh.calls[2][:5] == ["issue", "edit", "42", "--repo", "test-org/test-repo"]
        assert gh.bodies["edit"] == "old body\n\n### Section"

    def test_view_failure_raises_without_editing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gh = _FakeGh(existing=42, failing=frozenset({"view"}))
        with pytest.raises(RuntimeError, match="gh issue view failed"):
            _append(monkeypatch, gh)
        assert "edit" not in gh.commands()

    def test_edit_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gh = _FakeGh(existing=42, failing=frozenset({"edit"}))
        with pytest.raises(RuntimeError, match="gh issue edit failed"):
            _append(monkeypatch, gh)

    def test_append_exactly_at_the_limit_edits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        section = "### Section"
        gh = _FakeGh(existing=42, body="x" * (ISSUE_BODY_MAX_CHARS - 2 - len(section)))
        assert _append(monkeypatch, gh, section) == 42
        assert gh.commands() == ["list", "view", "edit"]

    def test_append_past_the_limit_rotates_to_a_successor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        section = "### Section"
        gh = _FakeGh(existing=42, body="x" * (ISSUE_BODY_MAX_CHARS - 1 - len(section)))
        assert _append(monkeypatch, gh, section) == 99
        assert gh.commands() == ["list", "view", "close", "create"]
        assert gh.calls[2] == ["issue", "close", "42", "--repo", "test-org/test-repo"]
        assert "#42" in gh.bodies["create"]
        assert gh.bodies["create"].endswith(section)

    def test_close_failure_raises_without_creating(self, monkeypatch: pytest.MonkeyPatch) -> None:
        section = "### Section"
        gh = _FakeGh(
            existing=42,
            body="x" * ISSUE_BODY_MAX_CHARS,
            failing=frozenset({"close"}),
        )
        with pytest.raises(RuntimeError, match="gh issue close failed"):
            _append(monkeypatch, gh, section)
        assert "create" not in gh.commands()


def _post_e2e_failure(*extra: str) -> int:
    return _cli_main(
        [
            "post-e2e-failure",
            "--test",
            "canary",
            "--head-sha",
            "abcdef0123456789abcdef0123456789abcdef01",
            "--workflow-run-url",
            "https://example.com/run/9",
            *extra,
        ]
    )


class TestPostE2eFailure:
    def test_missing_github_repository_returns_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        assert _post_e2e_failure("--stage", "test", "--pull-request", "12") == 1

    @pytest.mark.parametrize("stage", ["test", "redaction"])
    def test_section_names_pull_request_commit_run_and_stage(
        self, monkeypatch: pytest.MonkeyPatch, stage: str
    ) -> None:
        monkeypatch.setenv("GITHUB_REPOSITORY", "test-org/test-repo")
        gh = _FakeGh()
        monkeypatch.setattr(_patch_autoskillit__probe_canary, "run_gh", gh)
        assert _post_e2e_failure("--stage", stage, "--pull-request", "12") == 0
        create = gh.calls[-1]
        assert create[create.index("--title") + 1] == _E2E_TITLE
        body = gh.bodies["create"]
        assert "#12" in body
        assert "abcdef0123456789abcdef0123456789abcdef01" in body
        assert "### Failure at abcdef012345" in body
        assert "https://example.com/run/9" in body
        assert f"**Stage:** {stage}" in body

    def test_empty_pull_request_reports_workflow_dispatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_REPOSITORY", "test-org/test-repo")
        gh = _FakeGh()
        monkeypatch.setattr(_patch_autoskillit__probe_canary, "run_gh", gh)
        assert _post_e2e_failure("--stage", "test", "--pull-request", "") == 0
        assert "workflow_dispatch" in gh.bodies["create"]

    def test_unknown_stage_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _post_e2e_failure("--stage", "other")

    def test_issue_failure_returns_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_REPOSITORY", "test-org/test-repo")
        monkeypatch.setattr(
            _patch_autoskillit__probe_canary, "run_gh", _FakeGh(failing=frozenset({"create"}))
        )
        with structlog.testing.capture_logs() as cap_logs:
            assert _post_e2e_failure("--stage", "test") == 1
        failure = next(e for e in cap_logs if e.get("event") == "e2e_failure_issue_failed")
        assert failure["exc_info"] is True


class TestCliMain:
    def test_post_failure_records_and_saves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """post-failure writes updated state to the state file."""
        monkeypatch.setenv("GITHUB_REPOSITORY", "test-org/test-repo")
        state_path = tmp_path / "state.json"

        gh_call_count = 0

        def mock_run_gh(args, **kwargs):
            nonlocal gh_call_count
            gh_call_count += 1
            return CompletedProcess(args=args, returncode=1, stdout="", stderr="")

        monkeypatch.setattr(_patch_autoskillit__probe_canary, "run_gh", mock_run_gh)

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
        assert gh_call_count == 0, "run_gh must not be called when streak is below threshold"

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

        monkeypatch.setattr(_patch_autoskillit__probe_canary, "run_gh", mock_run_gh)

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
        saved = json.loads(state_path.read_text())
        assert saved["network_streak"] == N_CONSECUTIVE_FLAKE_GUARD
        assert saved["last_issue_number"] == 7

    def test_post_failure_below_threshold_no_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Below threshold, no gh issue commands are issued."""
        monkeypatch.setenv("GITHUB_REPOSITORY", "test-org/test-repo")
        state_path = tmp_path / "state.json"

        def mock_run_gh(args, **kwargs):
            raise AssertionError(f"Unexpected gh call: {args}")

        monkeypatch.setattr(_patch_autoskillit__probe_canary, "run_gh", mock_run_gh)

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
