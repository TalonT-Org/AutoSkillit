"""Tests for scripts/e2e/e2e_harness.py with a recording fake in place of real processes."""

from __future__ import annotations

import json
import math
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.infra._complexity_helpers import load_check_script

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
_HARNESS_SCRIPT = REPO_ROOT / "scripts" / "e2e" / "e2e_harness.py"

harness = load_check_script("_autoskillit_e2e_harness", _HARNESS_SCRIPT)
e2e_catalog = harness.e2e_catalog

MINIMAX_KEY = "minimax-test-key"
SANDBOX_TOKEN = "sandbox-test-token"
SANDBOX = "owner/sandbox"
_MINIMAX_MODEL = "MiniMax-M3[1m]"
_EXPECTED_SETTINGS_ENV = {
    "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
    "ANTHROPIC_AUTH_TOKEN": MINIMAX_KEY,
    "ANTHROPIC_MODEL": _MINIMAX_MODEL,
    "ANTHROPIC_DEFAULT_SONNET_MODEL": _MINIMAX_MODEL,
    "ANTHROPIC_DEFAULT_OPUS_MODEL": _MINIMAX_MODEL,
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": _MINIMAX_MODEL,
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "1000000",
    "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "6",
}
_CANARY_REPLY = json.dumps(
    {"type": "result", "is_error": False, "result": "AUTOSKILLIT-E2E-CANARY-OK"}
)
_SKILL_NOTICE = "2 skills unavailable on this backend (codex): a, b"
_CLEAN_INSTALL_ISSUE = "https://github.com/TalonT-Org/AutoSkillit/issues/5231"


@dataclass
class RecordedCall:
    argv: list[str]
    cwd: Path | None
    env: dict[str, str] | None
    input: str | None
    timeout: float | None


Handler = Callable[[list[str]], "subprocess.CompletedProcess[str] | BaseException | None"]


class FakeRunner:
    """Record every launch; answer from *handler*, defaulting to a clean exit."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.calls: list[RecordedCall] = []

    def __call__(self, argv, *, cwd=None, env=None, input=None, timeout=None):
        self.calls.append(RecordedCall(list(argv), cwd, env, input, timeout))
        response = self.handler(list(argv))
        if isinstance(response, BaseException):
            raise response
        return response if response is not None else _completed(argv)

    def argv_prefixes(self, length: int = 3) -> list[tuple[str, ...]]:
        return [tuple(call.argv[:length]) for call in self.calls]


def _completed(argv, stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)


def _pr(number: int, state: str, additions: int = 3, deletions: int = 1) -> dict:
    return {"number": number, "state": state, "additions": additions, "deletions": deletions}


def _recipe_handler(
    *,
    new_prs: list[dict] | None = None,
    fleet: subprocess.CompletedProcess[str] | BaseException | None = None,
    extra: Handler | None = None,
) -> Handler:
    listed = [_pr(8, "OPEN"), _pr(7, "MERGED")] if new_prs is None else new_prs

    def handle(argv: list[str]):
        if extra is not None and (response := extra(argv)) is not None:
            return response
        if argv[:3] == ["gh", "pr", "list"]:
            baseline = argv[argv.index("--limit") + 1] == "1"
            return _completed(argv, json.dumps([{"number": 7}] if baseline else listed))
        if argv[:3] == ["autoskillit", "fleet", "run"]:
            return fleet or _completed(argv, f'{_SKILL_NOTICE}\n{{"success": true}}\n')
        return None

    return handle


def _recipe_catalog(state: str = "open"):
    return e2e_catalog.parse_catalog(
        {
            "sandbox_repository": SANDBOX,
            "tests": [
                {
                    "name": "impl",
                    "kind": "recipe",
                    "peak_sessions": 6,
                    "timeout_sec": 600,
                    "trigger_paths": ["src/*"],
                    "recipe": "implementation",
                    "ingredients": {"task": "Add a greeting"},
                    "expected_pull_request_state": state,
                }
            ],
        }
    )


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin",
        "HOME": "/home/autoskillit",
        "MINIMAX_API_KEY": MINIMAX_KEY,
        "E2E_SANDBOX_TOKEN": SANDBOX_TOKEN,
        "ANTHROPIC_API_KEY": "anthropic-test-key",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth-test-token",
    }
    env.update(overrides)
    return {name: value for name, value in env.items() if value}


def _run_recipe(tmp_path: Path, runner: FakeRunner, *, state: str = "open", env=None):
    catalog = _recipe_catalog(state)
    out = tmp_path / "out"
    failures = harness.run_test(
        catalog.get("impl"),
        catalog,
        out=out,
        home=tmp_path / "home",
        env=env if env is not None else _env(),
        runner=runner,
    )
    return failures, json.loads((out / "result.json").read_text(encoding="utf-8"))


def _run_canary(tmp_path: Path, runner: FakeRunner, monkeypatch, env=None):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    catalog = e2e_catalog.load_catalog()
    out = tmp_path / "out"
    failures = harness.run_test(
        catalog.get("canary"),
        catalog,
        out=out,
        home=tmp_path / "home",
        env=env if env is not None else _env(),
        runner=runner,
    )
    return failures, json.loads((out / "result.json").read_text(encoding="utf-8"))


def _clean_install_catalog(expected_failures: list[dict[str, str]] | None = None):
    test = {
        "name": "clean-install",
        "kind": "clean-install",
        "peak_sessions": 0,
        "timeout_sec": 90,
        "trigger_paths": ["src/*"],
    }
    if expected_failures is not None:
        test["expected_failures"] = expected_failures
    return e2e_catalog.parse_catalog({"sandbox_repository": SANDBOX, "tests": [test]})


def _clean_install_runner(
    *,
    doctor_stdout: str = (
        '{"results": [{"severity": "ok", "check": "install", "message": "ready"}, '
        '{"severity": "info", "check": "backend_onboarding", "message": "optional setup"}]}'
    ),
    responses: dict[str, subprocess.CompletedProcess[str] | BaseException] | None = None,
):
    responses = responses or {}
    observed: dict[str, object] = {}
    runner: FakeRunner

    def handle(argv: list[str]):
        if argv[:2] == ["autoskillit", "install"]:
            stage = "install"
        elif argv == ["git", "init"]:
            stage = "git-init"
        elif argv[:2] == ["autoskillit", "init"]:
            stage = "init"
            call = runner.calls[-1]
            observed["cwd"] = call.cwd
            if call.cwd is not None:
                observed["scanner_config"] = (call.cwd / ".pre-commit-config.yaml").read_text()
        elif argv == ["autoskillit", "doctor", "--output-json"]:
            stage = "doctor"
        else:
            return None
        if stage in responses:
            return responses[stage]
        return _completed(argv, doctor_stdout if stage == "doctor" else f"{stage} ok\n")

    runner = FakeRunner(handle)
    return runner, observed


def _run_clean_install(
    tmp_path: Path,
    runner: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
    *,
    expected_failures: list[dict[str, str]] | None = None,
    env: dict[str, str] | None = None,
):
    catalog = _clean_install_catalog(expected_failures)
    out = tmp_path / "out"
    failures = harness.run_test(
        catalog.get("clean-install"),
        catalog,
        out=out,
        home=tmp_path / "separate-home",
        env=env if env is not None else {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin"},
        runner=runner,
    )
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    return failures, result, out, catalog


def _diagnostic(severity: str, check: str, message: str) -> dict[str, str]:
    return {"severity": severity, "check": check, "message": message}


class TestProviderSetup:
    def test_settings_env_is_the_minimax_map_and_keeps_other_keys(self, tmp_path: Path) -> None:
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir()
        settings_path.write_text(json.dumps({"theme": "dark", "env": {"OLD": "1"}}))
        harness.write_claude_settings(tmp_path, MINIMAX_KEY)
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert settings["env"] == _EXPECTED_SETTINGS_ENV
        assert settings["theme"] == "dark"
        assert stat.S_IMODE(settings_path.stat().st_mode) == 0o600

    def test_new_settings_file_is_private(self, tmp_path: Path) -> None:
        path = harness.write_claude_settings(tmp_path, MINIMAX_KEY)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_child_env_drops_every_credential(self) -> None:
        env = _env(ANTHROPIC_BASE_URL="https://example.invalid")
        scrubbed = harness.child_env(env)
        assert scrubbed == {"PATH": "/usr/bin", "HOME": "/home/autoskillit"}


def test_shipped_config_routes_every_default_codex_step_to_claude_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.config import load_config
    from autoskillit.core.io import load_yaml
    from autoskillit.core.paths import pkg_root

    home = tmp_path / "home"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    harness.install_autoskillit_config(home)
    project = tmp_path / "project"
    project.mkdir()
    cfg = load_config(project)

    defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
    routes = cfg.agent_backend.recipe_overrides
    unrouted = sorted(
        (recipe, step)
        for recipe, steps in defaults["agent_backend"]["recipe_overrides"].items()
        for step, backend in steps.items()
        if backend == "codex" and routes.get(recipe, {}).get(step) != "claude-code"
    )
    assert unrouted == []
    assert "codex" not in {backend for steps in routes.values() for backend in steps.values()}
    assert cfg.agent_backend.backend == "claude-code"
    assert cfg.quota_guard.enabled is False
    assert cfg.features["fleet"] is True
    assert cfg.features["fleet_headless_run"] is True


class TestCanaryOutput:
    def test_successful_reply_passes(self) -> None:
        assert harness.check_canary_output(_CANARY_REPLY) == []

    @pytest.mark.parametrize(
        "stdout",
        [
            json.dumps({"is_error": True, "result": "AUTOSKILLIT-E2E-CANARY-OK"}),
            json.dumps({"is_error": False, "result": "something else"}),
            "not json",
            json.dumps(["is_error", False]),
        ],
        ids=["is-error", "missing-token", "not-json", "not-an-object"],
    )
    def test_bad_reply_fails_once(self, stdout: str) -> None:
        assert len(harness.check_canary_output(stdout)) == 1


class TestEnvelope:
    def test_success_envelope_passes(self) -> None:
        assert harness.check_envelope(0, '{"success": true}\n') == []

    def test_envelope_is_the_last_line_after_plain_text(self) -> None:
        stdout = f'{_SKILL_NOTICE}\n{{"success": true}}\n\n'
        assert harness.last_line(stdout) == '{"success": true}'
        assert harness.check_envelope(0, stdout) == []

    @pytest.mark.parametrize(
        ("returncode", "stdout"),
        [
            (1, '{"success": true}'),
            (0, '{"success": false}'),
            (0, "[true]"),
            (0, '{"success": true}\nnot json'),
            (0, ""),
        ],
        ids=["nonzero-exit", "unsuccessful", "list", "non-json-last-line", "empty"],
    )
    def test_bad_envelope_fails(self, returncode: int, stdout: str) -> None:
        assert harness.check_envelope(returncode, stdout)


class TestPullRequestAssertions:
    def test_one_merged_pull_request_matches_lower_case_expectation(self) -> None:
        assert harness.check_pull_requests([_pr(8, "MERGED")], "merged") == []

    @pytest.mark.parametrize(
        "prs",
        [[], [_pr(8, "MERGED"), _pr(9, "MERGED")], [_pr(8, "OPEN")], [_pr(8, "MERGED", 0, 0)]],
        ids=["none", "two", "state-mismatch", "empty-diff"],
    )
    def test_unexpected_pull_requests_fail(self, prs: list[dict]) -> None:
        assert harness.check_pull_requests(prs, "merged")


class TestRecipeFlow:
    def test_steps_run_in_order_with_the_token_only_on_stdin(self, tmp_path: Path) -> None:
        runner = FakeRunner(_recipe_handler())
        failures, result = _run_recipe(tmp_path, runner)

        assert failures == []
        assert result == {"test": "impl", "passed": True, "failures": []}
        assert [call.argv for call in runner.calls] == [
            ["git", "config", "--global", "user.name", "AutoSkillit E2E"],
            [
                "git",
                "config",
                "--global",
                "user.email",
                "autoskillit-e2e@users.noreply.github.com",
            ],
            ["gh", "auth", "login", "--hostname", "github.com", "--with-token"],
            ["gh", "auth", "setup-git"],
            ["gh", "repo", "clone", SANDBOX, "/workspace/sandbox"],
            ["gh", "pr", "list", "--repo", SANDBOX, "--state", "all"]
            + ["--limit", "1", "--json", "number"],
            ["autoskillit", "fleet", "run", "implementation", "-i", "task=Add a greeting"]
            + ["--disable-quota-guard", "--timeout-sec", "600"],
            ["gh", "pr", "list", "--repo", SANDBOX, "--state", "all"]
            + ["--limit", "20", "--json", "number,state,additions,deletions"],
            ["gh", "pr", "close", "8", "--repo", SANDBOX, "--delete-branch"],
        ]
        assert [call.input for call in runner.calls if call.input] == [SANDBOX_TOKEN]
        assert all(SANDBOX_TOKEN not in " ".join(call.argv) for call in runner.calls)
        assert all(call.env == harness.child_env(_env()) for call in runner.calls)
        fleet_call = runner.calls[6]
        assert fleet_call.cwd == harness.SANDBOX_CLONE
        assert fleet_call.timeout == 600 + e2e_catalog.HARNESS_GRACE_SEC

    def test_outputs_keep_full_stdout_and_only_the_envelope_line(self, tmp_path: Path) -> None:
        _run_recipe(tmp_path, FakeRunner(_recipe_handler()))
        out = tmp_path / "out"
        stdout = (out / "fleet-run.stdout.log").read_text(encoding="utf-8")
        assert stdout == f'{_SKILL_NOTICE}\n{{"success": true}}\n'
        assert (out / "envelope.json").read_text(encoding="utf-8") == '{"success": true}\n'
        assert (out / "fleet-run.stderr.log").exists()

    def test_harness_writes_provider_settings_and_user_config(self, tmp_path: Path) -> None:
        _run_recipe(tmp_path, FakeRunner(_recipe_handler()))
        home = tmp_path / "home"
        settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert settings["env"] == _EXPECTED_SETTINGS_ENV
        installed = (home / ".autoskillit" / "config.yaml").read_bytes()
        assert installed == harness.CONFIG_TEMPLATE.read_bytes()

    def test_merged_pull_request_is_not_closed(self, tmp_path: Path) -> None:
        runner = FakeRunner(_recipe_handler(new_prs=[_pr(8, "MERGED")]))
        failures, _ = _run_recipe(tmp_path, runner, state="merged")
        assert failures == []
        assert ("gh", "pr", "close") not in runner.argv_prefixes()

    def test_auth_failure_stops_before_the_baseline(self, tmp_path: Path) -> None:
        def reject_login(argv: list[str]):
            if argv[:3] == ["gh", "auth", "login"]:
                return _completed(argv, returncode=1, stderr="bad credentials")
            return None

        runner = FakeRunner(_recipe_handler(extra=reject_login))
        failures, result = _run_recipe(tmp_path, runner)
        prefixes = runner.argv_prefixes()
        assert result["passed"] is False
        assert "bad credentials" in failures[0]
        assert ("gh", "pr", "list") not in prefixes
        assert ("gh", "pr", "close") not in prefixes
        assert ("autoskillit", "fleet", "run") not in prefixes

    def test_fleet_timeout_is_a_failure_and_cleanup_still_runs(self, tmp_path: Path) -> None:
        timeout = subprocess.TimeoutExpired(["autoskillit"], 720)
        runner = FakeRunner(_recipe_handler(fleet=timeout))
        failures, result = _run_recipe(tmp_path, runner)
        assert result["passed"] is False
        assert any("fleet run: timed out" in failure for failure in failures)
        assert runner.calls[-1].argv[:4] == ["gh", "pr", "close", "8"]

    def test_unexpected_fleet_error_records_failure_and_still_cleans_up(
        self, tmp_path: Path
    ) -> None:
        runner = FakeRunner(_recipe_handler(fleet=RuntimeError("unexpected fleet error")))
        failures, result = _run_recipe(tmp_path, runner)
        assert failures == ["harness error: unexpected fleet error"]
        assert result == {"test": "impl", "passed": False, "failures": failures}
        assert runner.calls[-1].argv[:4] == ["gh", "pr", "close", "8"]

    def test_missing_gh_is_a_failure_string(self, tmp_path: Path) -> None:
        def no_gh(argv: list[str]):
            return (
                FileNotFoundError(2, "No such file or directory", "gh")
                if argv[0] == "gh"
                else None
            )

        runner = FakeRunner(_recipe_handler(extra=no_gh))
        failures, result = _run_recipe(tmp_path, runner)
        assert result["passed"] is False
        assert failures[0].startswith("gh auth login:")
        assert ("autoskillit", "fleet", "run") not in runner.argv_prefixes()

    def test_failed_listing_after_the_run_is_retried_for_cleanup(self, tmp_path: Path) -> None:
        listings = iter(["broken", json.dumps([_pr(8, "OPEN")])])

        def flaky_listing(argv: list[str]):
            if argv[:3] == ["gh", "pr", "list"] and "20" in argv:
                return _completed(argv, next(listings))
            return None

        runner = FakeRunner(_recipe_handler(extra=flaky_listing))
        failures, _ = _run_recipe(tmp_path, runner)
        assert failures == ["gh pr list: output is not JSON"]
        assert runner.calls[-1].argv[:4] == ["gh", "pr", "close", "8"]

    @pytest.mark.parametrize("limit", ["1", "20"])
    def test_missing_pull_request_number_records_failure(self, tmp_path: Path, limit: str) -> None:
        def malformed_listing(argv: list[str]):
            if argv[:3] == ["gh", "pr", "list"] and limit in argv:
                return _completed(argv, json.dumps([{"state": "OPEN"}]))
            return None

        runner = FakeRunner(_recipe_handler(extra=malformed_listing))
        failures, result = _run_recipe(tmp_path, runner)
        assert "gh pr list: pull request number is missing or invalid" in failures
        assert result == {"test": "impl", "passed": False, "failures": failures}
        assert ("gh", "pr", "close") not in runner.argv_prefixes()

    def test_missing_sandbox_token_fails_before_any_launch(self, tmp_path: Path) -> None:
        runner = FakeRunner(_recipe_handler())
        failures, result = _run_recipe(tmp_path, runner, env=_env(E2E_SANDBOX_TOKEN=""))
        assert failures == ["E2E_SANDBOX_TOKEN is not set"]
        assert result["passed"] is False
        assert runner.calls == []


class TestCanaryFlow:
    def test_canary_runs_one_claude_prompt(self, tmp_path: Path, monkeypatch) -> None:
        runner = FakeRunner(lambda argv: _completed(argv, _CANARY_REPLY))
        failures, result = _run_canary(tmp_path, runner, monkeypatch)
        assert failures == []
        assert result == {"test": "canary", "passed": True, "failures": []}
        assert [call.argv for call in runner.calls] == [
            ["claude", "-p", harness.CANARY_PROMPT, "--output-format", "json"]
        ]
        assert runner.calls[0].env == harness.child_env(_env())
        assert (tmp_path / "out" / "canary.json").read_text(encoding="utf-8") == _CANARY_REPLY

    def test_missing_minimax_key_fails_before_any_launch(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        runner = FakeRunner(lambda argv: _completed(argv, _CANARY_REPLY))
        failures, result = _run_canary(tmp_path, runner, monkeypatch, env=_env(MINIMAX_API_KEY=""))
        assert failures == ["MINIMAX_API_KEY is not set"]
        assert result["passed"] is False
        assert runner.calls == []


class TestCleanInstallFlow:
    @pytest.mark.parametrize("credentials", [False, True])
    def test_clean_flow_uses_one_scratch_directory_and_keeps_evidence(
        self, tmp_path, monkeypatch, credentials
    ):
        runner, observed = _clean_install_runner()
        env = {
            "HOME": str(tmp_path / "home"),
            "PATH": "/usr/bin",
            "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        }
        if credentials:
            env.update(
                _env(HOME=env["HOME"], OPENAI_API_KEY="openai-secret", OPENAI_ORG_ID="org-test")
            )
        failures, result, out, catalog = _run_clean_install(tmp_path, runner, monkeypatch, env=env)

        assert failures == []
        assert result["outcome"] == "passed"
        assert result["expected_findings"] == []
        assert [call.argv for call in runner.calls] == [
            ["autoskillit", "install"],
            ["git", "init"],
            ["autoskillit", "init", "--test-command", "git diff --check"],
            ["autoskillit", "doctor", "--output-json"],
        ]
        scratch = runner.calls[0].cwd
        assert scratch is not None
        assert all(call.cwd == scratch for call in runner.calls)
        assert observed["cwd"] == scratch
        assert observed["scanner_config"] == (
            "repos:\n  - repo: https://github.com/gitleaks/gitleaks\n"
            "    rev: v8.30.0\n    hooks:\n      - id: gitleaks\n"
        )
        assert all(call.env == runner.calls[0].env for call in runner.calls)
        assert runner.calls[0].env["HOME"] == env["HOME"]
        assert str(tmp_path / "separate-home") != runner.calls[0].env["HOME"]
        assert not any(name.startswith("OPENAI_") for name in runner.calls[0].env)
        assert not any(
            name in runner.calls[0].env
            for name in (
                "ANTHROPIC_API_KEY",
                "CLAUDE_CODE_OAUTH_TOKEN",
                "MINIMAX_API_KEY",
                "E2E_SANDBOX_TOKEN",
            )
        )
        timeouts = [call.timeout for call in runner.calls]
        assert all(
            value is not None and math.isfinite(value) and 0 < value <= 90 for value in timeouts
        )
        assert all(later <= earlier for earlier, later in zip(timeouts, timeouts[1:]))
        for stage in ("install", "git-init", "init", "doctor"):
            assert (out / f"{stage}.stdout.txt").is_file()
            assert (out / f"{stage}.stderr.txt").is_file()
            assert (out / f"{stage}.command.json").is_file()
        assert (out / "doctor.json").read_text() == (out / "doctor.stdout.txt").read_text()
        assert not (tmp_path / "separate-home" / ".claude" / "settings.json").exists()
        assert not (tmp_path / "separate-home" / ".autoskillit" / "config.yaml").exists()
        assert catalog.get("clean-install").peak_sessions == 0
        assert not scratch.exists()

    @pytest.mark.parametrize("additional_finding", [False, True])
    def test_expected_finding_admits_only_the_linked_diagnostic(
        self, tmp_path, monkeypatch, additional_finding
    ):
        diagnostic = _diagnostic("error", "runtime", "known clean-install failure")
        expected = {**diagnostic, "issue": _CLEAN_INSTALL_ISSUE}
        rows = [diagnostic]
        if additional_finding:
            rows.append(_diagnostic("error", "another-check", "new defect"))
        runner, _ = _clean_install_runner(doctor_stdout=json.dumps({"results": rows}))
        failures, result, _out, _catalog = _run_clean_install(
            tmp_path, runner, monkeypatch, expected_failures=[expected]
        )

        assert bool(failures) == additional_finding
        assert result["outcome"] == ("failed" if additional_finding else "expected_failure")
        assert result["passed"] is False
        assert result["expected_findings"] == [expected]

    @pytest.mark.parametrize(
        "doctor_stdout",
        [
            "not json",
            "[]",
            "{}",
            '{"results": {}}',
            '{"results": []}',
            '{"results": [null]}',
            '{"results": [{"severity": "fatal", "check": "x", "message": "y"}]}',
            '{"results": [{"severity": "warning", "check": "x", "message": 3}]}',
        ],
        ids=[
            "invalid-json",
            "not-object",
            "missing-results",
            "results-not-list",
            "empty-results",
            "row-not-object",
            "bad-severity",
            "bad-message",
        ],
    )
    def test_invalid_doctor_output_fails(self, tmp_path, monkeypatch, doctor_stdout):
        runner, _ = _clean_install_runner(doctor_stdout=doctor_stdout)
        failures, result, _out, _catalog = _run_clean_install(tmp_path, runner, monkeypatch)

        assert failures
        assert result["outcome"] == "failed"

    def test_unlisted_warning_and_zero_exit_error_fail(self, tmp_path, monkeypatch):
        for name, severity in (("warning", "warning"), ("error", "error")):
            case = tmp_path / name
            diagnostic = _diagnostic(severity, "new-check", "new diagnostic")
            runner, _ = _clean_install_runner(doctor_stdout=json.dumps({"results": [diagnostic]}))
            failures, result, _out, _catalog = _run_clean_install(case, runner, monkeypatch)
            assert failures
            assert result["outcome"] == "failed"

    def test_warning_allowlist_requires_an_exact_check_and_message(self, tmp_path, monkeypatch):
        assert all(reason.strip() for reason in harness.CLEAN_INSTALL_ALLOWED_WARNINGS.values())
        key = ("environment", "known warning")
        monkeypatch.setattr(harness, "CLEAN_INSTALL_ALLOWED_WARNINGS", {key: "tracked reason"})
        for message, outcome in ((key[1], "passed"), ("changed warning", "failed")):
            case = tmp_path / ("allowed" if outcome == "passed" else "changed")
            diagnostic = _diagnostic("warning", key[0], message)
            runner, _ = _clean_install_runner(doctor_stdout=json.dumps({"results": [diagnostic]}))
            failures, result, _out, _catalog = _run_clean_install(case, runner, monkeypatch)
            assert (not failures) == (outcome == "passed")
            assert result["outcome"] == outcome

    @pytest.mark.parametrize(
        "check",
        [
            "mcp_server_registered",
            "plugin_cache_exists",
            "hook_registration",
            "hook_registry_drift",
        ],
    )
    def test_installation_diagnostic_warning_fails_and_is_saved(
        self, tmp_path, monkeypatch, check
    ):
        stdout = json.dumps({"results": [_diagnostic("warning", check, "installation defect")]})
        runner, _ = _clean_install_runner(doctor_stdout=stdout)
        failures, result, out, _catalog = _run_clean_install(tmp_path, runner, monkeypatch)

        assert failures
        assert result["outcome"] == "failed"
        assert check in failures[0]
        assert (out / "doctor.json").read_text() == stdout

    @pytest.mark.parametrize("stage", ["install", "init", "doctor"])
    def test_nonzero_command_exit_is_recorded_and_stops_the_flow(
        self, tmp_path, monkeypatch, stage
    ):
        command = {
            "install": ["autoskillit", "install"],
            "init": ["autoskillit", "init", "--test-command", "git diff --check"],
            "doctor": ["autoskillit", "doctor", "--output-json"],
        }[stage]
        stdout = '{"results": []}' if stage == "doctor" else "partial output"
        runner, _ = _clean_install_runner(
            responses={stage: _completed(command, stdout, returncode=2, stderr="command failed")}
        )
        failures, result, out, _catalog = _run_clean_install(tmp_path, runner, monkeypatch)

        assert failures
        assert result["outcome"] == "failed"
        assert (out / f"{stage}.command.json").is_file()
        assert runner.calls[-1].argv == command
        if stage == "doctor":
            assert (out / "doctor.json").read_text(encoding="utf-8") == stdout

    @pytest.mark.parametrize("stage", ["install", "init", "doctor"])
    def test_timeout_saves_partial_byte_and_text_streams(self, tmp_path, monkeypatch, stage):
        command = {
            "install": ["autoskillit", "install"],
            "init": ["autoskillit", "init", "--test-command", "git diff --check"],
            "doctor": ["autoskillit", "doctor", "--output-json"],
        }[stage]
        timeout = subprocess.TimeoutExpired(
            command, 1.0, output=b"partial stdout", stderr="partial stderr"
        )
        runner, _ = _clean_install_runner(responses={stage: timeout})
        failures, result, out, _catalog = _run_clean_install(tmp_path, runner, monkeypatch)

        assert failures
        assert result["outcome"] == "failed"
        assert (out / f"{stage}.stdout.txt").read_text(encoding="utf-8") == "partial stdout"
        assert (out / f"{stage}.stderr.txt").read_text(encoding="utf-8") == "partial stderr"
        if stage == "doctor":
            assert (out / "doctor.json").read_bytes() == b"partial stdout"

    def test_launch_error_is_explicitly_recorded(self, tmp_path, monkeypatch):
        runner, _ = _clean_install_runner(
            responses={"install": FileNotFoundError(2, "No such file or directory", "autoskillit")}
        )
        failures, result, out, _catalog = _run_clean_install(tmp_path, runner, monkeypatch)

        assert failures
        assert result["outcome"] == "failed"
        evidence = (out / "install.command.json").read_text(encoding="utf-8")
        assert "No such file or directory" in evidence

    def test_exhausted_deadline_does_not_launch_another_command(self, tmp_path, monkeypatch):
        ticks = iter([0, 1, 90])
        monkeypatch.setattr(harness.time, "monotonic", lambda: next(ticks))
        runner, _ = _clean_install_runner()
        failures, result, _out, _catalog = _run_clean_install(tmp_path, runner, monkeypatch)

        assert "budget exhausted before git-init" in failures[0]
        assert result["outcome"] == "failed"
        assert len(runner.calls) == 1
        assert runner.calls[0].timeout == 89
        assert not runner.calls[0].cwd.exists()

    def test_stale_expectation_fails_and_unexpected_diagnostic_is_unlisted(
        self, tmp_path, monkeypatch
    ):
        old = {**_diagnostic("error", "runtime", "old message"), "issue": _CLEAN_INSTALL_ISSUE}
        current = _diagnostic("error", "runtime", "changed message")
        runner, _ = _clean_install_runner(doctor_stdout=json.dumps({"results": [current]}))
        failures, result, _out, _catalog = _run_clean_install(
            tmp_path, runner, monkeypatch, expected_failures=[old]
        )

        assert failures
        assert result["outcome"] == "failed"
        assert result["expected_findings"] == []

    def test_cli_names_expected_failure_and_run_test_handles_exceptions(
        self, tmp_path, monkeypatch, capsys
    ):
        diagnostic = _diagnostic("error", "runtime", "known clean-install failure")
        expected = {**diagnostic, "issue": _CLEAN_INSTALL_ISSUE}
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(
            json.dumps(
                {
                    "sandbox_repository": SANDBOX,
                    "tests": [
                        {
                            "name": "clean-install",
                            "kind": "clean-install",
                            "peak_sessions": 0,
                            "timeout_sec": 90,
                            "trigger_paths": ["src/*"],
                            "expected_failures": [expected],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        scratch_parent = tmp_path / "cli-scratch"
        scratch_parent.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(scratch_parent))
        runner, _ = _clean_install_runner(doctor_stdout=json.dumps({"results": [diagnostic]}))
        monkeypatch.setattr(harness, "run_command", runner)

        assert (
            harness.main(
                [
                    "run",
                    "--test",
                    "clean-install",
                    "--out",
                    str(tmp_path / "cli-out"),
                    "--catalog",
                    str(catalog_path),
                ]
            )
            == 0
        )
        assert "e2e_harness: clean-install expected_failure" in capsys.readouterr().out

        def crash(*_args, **_kwargs):
            raise RuntimeError("runner crashed")

        monkeypatch.setattr(harness, "run_command", crash)
        assert (
            harness.main(
                [
                    "run",
                    "--test",
                    "clean-install",
                    "--out",
                    str(tmp_path / "crash-out"),
                    "--catalog",
                    str(catalog_path),
                ]
            )
            == 1
        )
        assert "harness error: runner crashed" in capsys.readouterr().err


class TestRedaction:
    def test_secrets_are_replaced_in_nested_text_and_binary_files(self, tmp_path: Path) -> None:
        source = tmp_path / "out"
        (source / "nested").mkdir(parents=True)
        (source / "result.json").write_text(f"key={MINIMAX_KEY}\n", encoding="utf-8")
        (source / "nested" / "blob.bin").write_bytes(b"\x00" + SANDBOX_TOKEN.encode() + b"\xff")
        (source / "nested" / "blob.bin").chmod(0o600)
        dest = tmp_path / "upload"

        harness.redact_tree([source, tmp_path / "missing"], dest, [MINIMAX_KEY, SANDBOX_TOKEN, ""])

        assert (dest / "out" / "result.json").read_text(encoding="utf-8") == "key=[REDACTED]\n"
        assert (dest / "out" / "nested" / "blob.bin").read_bytes() == b"\x00[REDACTED]\xff"
        assert stat.S_IMODE((dest / "out" / "nested" / "blob.bin").stat().st_mode) == 0o644
        assert stat.S_IMODE((dest / "out" / "nested").stat().st_mode) == 0o755
        assert not (dest / "missing").exists()

    def test_symlinks_are_never_copied(self, tmp_path: Path) -> None:
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text(MINIMAX_KEY, encoding="utf-8")
        source = tmp_path / "out"
        source.mkdir()
        (source / "kept.txt").write_text("kept", encoding="utf-8")
        (source / "file-link").symlink_to(secret_file)
        (source / "dir-link").symlink_to(tmp_path)
        linked_source = tmp_path / "linked"
        linked_source.symlink_to(source)
        dest = tmp_path / "upload"

        harness.redact_tree([source, linked_source], dest, [MINIMAX_KEY])

        assert sorted(os.listdir(dest)) == ["out"]
        assert sorted(os.listdir(dest / "out")) == ["kept.txt"]

    def test_surviving_secret_fails_the_post_scan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "out"
        source.mkdir()
        (source / "log.txt").write_text(f"token {SANDBOX_TOKEN}", encoding="utf-8")
        monkeypatch.setattr(harness, "_redact", lambda data, needles: data)
        with pytest.raises(RuntimeError, match="survived redaction"):
            harness.redact_tree([source], tmp_path / "upload", [SANDBOX_TOKEN])

    def test_redact_command_reads_secrets_from_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "out"
        source.mkdir()
        (source / "log.txt").write_text(f"key {MINIMAX_KEY}", encoding="utf-8")
        monkeypatch.setenv("MINIMAX_API_KEY", MINIMAX_KEY)
        monkeypatch.delenv("E2E_SANDBOX_TOKEN", raising=False)
        argv = ["redact", "--dest", str(tmp_path / "upload")]
        argv += ["--secret-env", "MINIMAX_API_KEY", "--secret-env", "E2E_SANDBOX_TOKEN"]
        assert harness.main([*argv, str(source)]) == 0
        assert (tmp_path / "upload" / "out" / "log.txt").read_text(encoding="utf-8") == (
            "key [REDACTED]"
        )
