"""L1 unit tests for execution/testing.py — pytest output parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.types import (
    AUTOSKILLIT_PRIVATE_ENV_VARS,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.testing import (
    DefaultTestRunner,
    _read_sidecar_base_branch,
    _resolve_base_ref,
    build_sanitized_env,
)
from autoskillit.execution.testing import (
    parse_pytest_summary as _parse_pytest_summary,
)
from tests._helpers import make_test_check_config, make_test_config


def test_build_sanitized_env_strips_private_env_vars(monkeypatch):
    """build_sanitized_env() must strip every var in AUTOSKILLIT_PRIVATE_ENV_VARS."""
    for var in AUTOSKILLIT_PRIVATE_ENV_VARS:
        monkeypatch.setenv(var, "1")
    monkeypatch.setenv("UNRELATED_VAR", "keep-me")

    result = build_sanitized_env()

    for var in AUTOSKILLIT_PRIVATE_ENV_VARS:
        assert var not in result, f"{var} must not appear in sanitized env"
    assert result.get("UNRELATED_VAR") == "keep-me"


def test_build_sanitized_env_returns_full_copy_when_no_private_vars(monkeypatch):
    """When no private vars are present, build_sanitized_env returns the full env."""
    for var in AUTOSKILLIT_PRIVATE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SENTINEL_VAR", "present")

    result = build_sanitized_env()
    assert "SENTINEL_VAR" in result


@pytest.mark.anyio
async def test_default_test_runner_strips_private_env_vars_from_subprocess(monkeypatch, tmp_path):
    """DefaultTestRunner.run() must pass an env dict to the runner that excludes
    every var in AUTOSKILLIT_PRIVATE_ENV_VARS, even when the var is set in the
    calling process."""
    for var in AUTOSKILLIT_PRIVATE_ENV_VARS:
        monkeypatch.setenv(var, "1")

    captured_kwargs: dict = {}

    # env= is always passed as a keyword argument by DefaultTestRunner.run(),
    # so it lands in **kwargs and is captured correctly here.
    async def capturing_runner(cmd, *, cwd, timeout, **kwargs):
        captured_kwargs.update(kwargs)
        return SubprocessResult(
            returncode=0,
            stdout="1 passed",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
        )

    runner = DefaultTestRunner(config=make_test_config(), runner=capturing_runner)
    await runner.run(cwd=tmp_path)

    assert "env" in captured_kwargs, "DefaultTestRunner must pass env= to its runner"
    passed_env = captured_kwargs["env"]
    for var in AUTOSKILLIT_PRIVATE_ENV_VARS:
        assert var not in passed_env, (
            f"{var} must not appear in the env passed to the subprocess runner"
        )


class TestParsePytestSummary:
    """_parse_pytest_summary extracts structured counts from pytest output."""

    def test_simple_pass(self):
        assert _parse_pytest_summary("= 100 passed =\n") == {"passed": 100}

    def test_failed_and_passed(self):
        assert _parse_pytest_summary("= 3 failed, 97 passed =\n") == {
            "failed": 3,
            "passed": 97,
        }

    def test_xfailed_parsed_separately(self):
        counts = _parse_pytest_summary("= 8552 passed, 3 xfailed =\n")
        assert counts == {"passed": 8552, "xfailed": 3}
        assert "failed" not in counts

    def test_mixed_all_outcomes(self):
        counts = _parse_pytest_summary(
            "= 1 failed, 2 xfailed, 1 xpassed, 3 skipped, 93 passed =\n"
        )
        assert counts["failed"] == 1
        assert counts["xfailed"] == 2
        assert counts["xpassed"] == 1
        assert counts["skipped"] == 3
        assert counts["passed"] == 93

    def test_error_outcome(self):
        assert _parse_pytest_summary("= 1 error, 99 passed =\n") == {
            "error": 1,
            "passed": 99,
        }

    def test_multiline_finds_summary(self):
        output = "some log output\nERROR in setup\n=== 100 passed in 2.5s ===\n"
        counts = _parse_pytest_summary(output)
        assert counts == {"passed": 100}

    def test_empty_output(self):
        assert _parse_pytest_summary("") == {}

    def test_no_summary_line(self):
        assert _parse_pytest_summary("no test results here\n") == {}

    def test_bare_q_format_failed_and_passed(self):
        """Bare -q format parses correctly — no = delimiters needed."""
        counts = _parse_pytest_summary("3 failed, 97 passed in 2.31s")
        assert counts["failed"] == 3
        assert counts["passed"] == 97

    def test_bare_q_format_passed_only(self):
        """Bare -q single-outcome line."""
        counts = _parse_pytest_summary("100 passed in 1.50s")
        assert counts == {"passed": 100}


class TestParsePytestSummaryAnchored:
    """_parse_pytest_summary only matches lines in the === delimited section."""

    def test_pytest_summary_ignores_non_summary_lines(self):
        """Log output with 'N failed' must not be confused with the summary.

        Test output can contain lines like '3 failed connections reestablished'
        which match the outcome pattern. Only the === delimited summary line
        should be matched.
        """
        stdout = (
            "test_network.py::test_reconnect PASSED\n"
            "3 failed connections reestablished\n"
            "1 error in config reloaded successfully\n"
            "=== 5 passed in 2.1s ===\n"
        )
        counts = _parse_pytest_summary(stdout)
        assert counts == {"passed": 5}
        assert "failed" not in counts
        assert "error" not in counts


_REALISTIC_VERBOSE = (
    "============================= test session starts ==============================\n"
    "platform linux -- Python 3.12\n"
    "collected 100 items\n"
    "\n"
    "tests/test_foo.py::test_a PASSED                                         [  1%]\n"
    "tests/test_foo.py::test_b FAILED                                         [  2%]\n"
    "...\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_foo.py::test_b - AssertionError\n"
    "========================== 2 failed, 98 passed in 3.45s ==========================\n"
)

PYTEST_SUMMARY_FORMAT_VARIANTS = [
    pytest.param("verbose_single_outcome", "= 5 passed in 1.23s =", {"passed": 5}),
    pytest.param(
        "verbose_multiple_outcomes", "= 3 failed, 97 passed in 2.0s =", {"failed": 3, "passed": 97}
    ),
    pytest.param(
        "verbose_with_warnings", "= 100 passed, 5 warnings in 3s =", {"passed": 100, "warning": 5}
    ),
    pytest.param(
        "verbose_with_xfailed", "= 97 passed, 3 xfailed in 1.2s =", {"passed": 97, "xfailed": 3}
    ),
    pytest.param(
        "verbose_with_xpassed", "= 95 passed, 5 xpassed in 2.0s =", {"passed": 95, "xpassed": 5}
    ),
    pytest.param("verbose_with_errors", "= 1 error in 0.48s =", {"error": 1}),
    pytest.param(
        "verbose_with_deselected",
        "= 50 passed, 50 deselected in 1.0s =",
        {"passed": 50, "deselected": 50},
    ),
    pytest.param("bare_q_passed_only", "100 passed in 1.50s", {"passed": 100}),
    pytest.param(
        "bare_q_failed_and_passed", "3 failed, 97 passed in 2.31s", {"failed": 3, "passed": 97}
    ),
    pytest.param(
        "bare_q_with_warnings", "97 passed, 5 warnings in 3.1s", {"passed": 97, "warning": 5}
    ),
    pytest.param(
        "bare_q_multiline",
        "FAILED tests/test_foo.py::test_a - AssertionError\n2 failed, 98 passed in 4.12s\n",
        {"failed": 2, "passed": 98},
    ),
    pytest.param("empty_string", "", {}),
    pytest.param("no_summary_log_only", "collected 100 items\nsome log output\n", {}),
    pytest.param("realistic_verbose_full_output", _REALISTIC_VERBOSE, {"failed": 2, "passed": 98}),
]


@pytest.mark.parametrize("_id,stdout,expected", PYTEST_SUMMARY_FORMAT_VARIANTS)
def test_parse_pytest_summary_format_variants(_id, stdout, expected):
    assert _parse_pytest_summary(stdout) == expected


def test_parse_pytest_summary_returns_empty_dict_for_no_match():
    assert _parse_pytest_summary("no summary here") == {}


def test_parse_pytest_summary_only_matches_equals_delimited_lines():
    assert _parse_pytest_summary("3 failed connections\n= 5 passed =") == {"passed": 5}


def test_check_test_passed_true_on_zero_rc_clean_output():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "= 50 passed in 1s =") is True


def test_check_test_passed_false_on_nonzero_rc():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(1, "= 50 passed in 1s =") is False


def test_check_test_passed_false_on_zero_rc_with_failed_in_output():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "= 2 failed, 48 passed =") is False


def test_check_test_passed_false_on_zero_rc_with_error_in_output():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "= 1 error =") is False


def test_check_test_passed_true_for_xfailed_skipped():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "= 97 passed, 3 xfailed, 1 skipped =") is True


def test_check_test_passed_true_when_no_summary_empty_output():
    from autoskillit.execution.testing import check_test_passed

    # Non-pytest runner: rc=0, no output — trust exit code
    assert check_test_passed(0, "") is True


def test_check_test_passed_true_when_no_summary_log_only():
    from autoskillit.execution.testing import check_test_passed

    # Non-pytest runner: rc=0, non-pytest stdout — trust exit code
    assert check_test_passed(0, "collected 100 items\nsome log output\n") is True


def test_check_test_passed_false_bare_q_failures():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "3 failed, 97 passed in 2.31s") is False


def test_check_test_passed_true_bare_q_clean():
    from autoskillit.execution.testing import check_test_passed

    assert check_test_passed(0, "100 passed in 1.50s") is True


def test_check_test_passed_true_when_no_summary_stderr_only() -> None:
    from autoskillit.execution.testing import check_test_passed

    # Non-pytest runner: rc=0, empty stdout, stderr pass signal → PASS
    assert check_test_passed(0, "", "test result: ok. 42 passed; 0 failed\n") is True


def test_check_test_passed_true_when_non_pytest_stdout() -> None:
    from autoskillit.execution.testing import check_test_passed

    # Non-pytest runner: rc=0, stdout-only non-pytest output → PASS
    assert check_test_passed(0, "All tests passed.\n", "") is True


def test_check_test_passed_false_when_nonzero_rc_no_output() -> None:
    from autoskillit.execution.testing import check_test_passed

    # Non-zero rc still fails regardless of output
    assert check_test_passed(1, "", "") is False


def test_check_test_passed_parses_pytest_summary_in_stderr() -> None:
    from autoskillit.execution.testing import check_test_passed

    # Pytest summary found in stderr — parse it
    assert check_test_passed(0, "", "= 5 passed in 1.2s =\n") is True


@pytest.mark.anyio
async def test_default_test_runner_returns_test_result_with_stderr(tmp_path: Path) -> None:
    from autoskillit.core import TestResult
    from autoskillit.execution.testing import DefaultTestRunner
    from tests.conftest import MockSubprocessRunner, _make_result

    runner = MockSubprocessRunner()
    runner.push(_make_result(0, "", stderr="PASSED [0.5s] all tests"))
    config = make_test_config()
    tester = DefaultTestRunner(config=config, runner=runner)
    result = await tester.run(tmp_path)
    assert isinstance(result, TestResult)
    assert result.passed is True
    assert result.stderr == "PASSED [0.5s] all tests"


def test_test_result_dataclass_fields() -> None:
    from autoskillit.core import TestResult

    r = TestResult(passed=True, stdout="out", stderr="err")
    assert r.passed is True
    assert r.stdout == "out"
    assert r.stderr == "err"


# ---------- TestCheckConfig filter_mode / base_ref ----------


def test_test_check_config_has_filter_mode_and_base_ref_fields():
    cfg = make_test_check_config()
    assert cfg.filter_mode is None
    assert cfg.base_ref is None


def test_from_dynaconf_reads_filter_mode_and_base_ref(monkeypatch):
    from tests._helpers import make_dynaconf_and_automation_config

    _make_dynaconf, AutomationConfig = make_dynaconf_and_automation_config()
    monkeypatch.setenv("AUTOSKILLIT_TEST_CHECK__FILTER_MODE", "conservative")
    monkeypatch.setenv("AUTOSKILLIT_TEST_CHECK__BASE_REF", "origin/main")
    d = _make_dynaconf()
    cfg = AutomationConfig.from_dynaconf(d)
    assert cfg.test_check.filter_mode == "conservative"
    assert cfg.test_check.base_ref == "origin/main"


@pytest.mark.anyio
async def test_default_test_runner_injects_filter_mode_env_var(monkeypatch, tmp_path):
    captured_kwargs: dict = {}

    async def capturing_runner(cmd, *, cwd, timeout, **kwargs):
        captured_kwargs.update(kwargs)
        return SubprocessResult(
            returncode=0,
            stdout="1 passed",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
        )

    config = make_test_config(test_check=make_test_check_config(filter_mode="conservative"))
    runner = DefaultTestRunner(config=config, runner=capturing_runner)
    await runner.run(cwd=tmp_path)
    assert captured_kwargs["env"]["AUTOSKILLIT_TEST_FILTER"] == "conservative"


@pytest.mark.anyio
async def test_default_test_runner_omits_filter_env_when_none(monkeypatch, tmp_path):
    captured_kwargs: dict = {}

    async def capturing_runner(cmd, *, cwd, timeout, **kwargs):
        captured_kwargs.update(kwargs)
        return SubprocessResult(
            returncode=0,
            stdout="1 passed",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
        )

    config = make_test_config(test_check=make_test_check_config())
    runner = DefaultTestRunner(config=config, runner=capturing_runner)
    await runner.run(cwd=tmp_path)
    assert "AUTOSKILLIT_TEST_FILTER" not in captured_kwargs["env"]


@pytest.mark.anyio
async def test_default_test_runner_injects_base_ref_from_config(monkeypatch, tmp_path):
    captured_kwargs: dict = {}

    async def capturing_runner(cmd, *, cwd, timeout, **kwargs):
        captured_kwargs.update(kwargs)
        return SubprocessResult(
            returncode=0,
            stdout="1 passed",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
        )

    config = make_test_config(test_check=make_test_check_config(base_ref="origin/main"))
    runner = DefaultTestRunner(config=config, runner=capturing_runner)
    await runner.run(cwd=tmp_path)
    assert captured_kwargs["env"]["AUTOSKILLIT_TEST_BASE_REF"] == "origin/main"


# ---------- _resolve_base_ref ----------


@pytest.mark.anyio
async def test_resolve_base_ref_config_override_wins(tmp_path):
    result = await _resolve_base_ref("origin/main", tmp_path)
    assert result == "origin/main"


@pytest.mark.anyio
async def test_resolve_base_ref_returns_none_when_no_source(tmp_path):
    result = await _resolve_base_ref(None, tmp_path)
    assert result is None


@pytest.mark.anyio
async def test_resolve_base_ref_git_upstream_fallback(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "branch", "upstream-branch"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "branch", "--set-upstream-to=upstream-branch"],
        check=True,
        capture_output=True,
    )
    result = await _resolve_base_ref(None, repo)
    assert result == "upstream-branch"


# ---------- _read_sidecar_base_branch ----------


def test_read_sidecar_base_branch_returns_branch(tmp_path):
    wt_dir = tmp_path / "my-worktree"
    wt_dir.mkdir()
    main_git = tmp_path / "main-repo" / ".git"
    main_git.mkdir(parents=True)
    worktrees_gitdir = main_git / "worktrees" / "my-worktree"
    worktrees_gitdir.mkdir(parents=True)
    (wt_dir / ".git").write_text(f"gitdir: {worktrees_gitdir}\n")
    sidecar = tmp_path / "main-repo" / ".autoskillit" / "temp" / "worktrees" / "my-worktree"
    sidecar.mkdir(parents=True)
    (sidecar / "base-branch").write_text("impl-934\n")
    assert _read_sidecar_base_branch(wt_dir) == "impl-934"


def test_read_sidecar_base_branch_returns_none_for_regular_dir(tmp_path):
    assert _read_sidecar_base_branch(tmp_path) is None


def test_read_sidecar_base_branch_returns_none_for_main_checkout(tmp_path):
    (tmp_path / ".git").mkdir()
    assert _read_sidecar_base_branch(tmp_path) is None


# ---------- env var passthrough ----------


def test_filter_env_vars_not_in_private_set():
    assert "AUTOSKILLIT_TEST_FILTER" not in AUTOSKILLIT_PRIVATE_ENV_VARS
    assert "AUTOSKILLIT_TEST_BASE_REF" not in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_defaults_yaml_has_filter_mode_and_base_ref():
    from autoskillit.core import load_yaml, pkg_root

    defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
    tc = defaults["test_check"]
    assert "filter_mode" in tc
    assert "base_ref" in tc
    assert tc["filter_mode"] is None
    assert tc["base_ref"] is None
