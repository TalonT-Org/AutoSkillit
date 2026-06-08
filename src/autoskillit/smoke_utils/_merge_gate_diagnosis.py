"""Merge gate test failure diagnosis file writer for smoke_utils run_python steps."""

from __future__ import annotations

from pathlib import Path

import regex as re

_FAILED_TEST_RE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)
_TIMEOUT_RE = re.compile(r"timeout|timed out|TimeoutError", re.IGNORECASE)
_ENV_ERROR_RE = re.compile(
    r"ModuleNotFoundError|ImportError|FileNotFoundError|No such file", re.IGNORECASE
)

_PRE_TEST_STEPS = frozenset(
    {
        "dirty_tree",
        "dirty_main_repo",
        "rebase",
        "path_validation",
        "protected_branch",
        "branch_detection",
        "fetch",
        "pre_rebase_check",
        "merge_commits_detected",
        "generated_file_cleanup",
        "editable_install_guard",
        "embedded_worktree",
        "ref_coherence",
        "merge",
    }
)
_TEST_STEPS = frozenset({"test_gate", "post_rebase_test_gate"})


def _classify_test_subtype(test_stdout: str, test_stderr: str) -> str:
    combined = f"{test_stdout}\n{test_stderr}"
    if not combined.strip():
        return "no_test_output"
    if _TIMEOUT_RE.search(combined):
        return "timing_race"
    if _ENV_ERROR_RE.search(combined):
        return "env"
    if _FAILED_TEST_RE.search(combined):
        return "deterministic"
    return "unknown"


def _extract_failed_tests(test_stdout: str, test_stderr: str) -> list[str]:
    combined = f"{test_stdout}\n{test_stderr}"
    return _FAILED_TEST_RE.findall(combined)


def diagnose_merge_gate(
    test_stdout: str = "",
    test_stderr: str = "",
    output_dir: str = "",
    failed_step: str = "",
    **_kwargs: object,
) -> dict[str, str]:
    """Write a structured merge gate failure diagnosis file and return its path.

    Called by run_python from the diagnose_merge_gate step in remediation.yaml,
    implementation.yaml, and implementation-groups.yaml. Parses merge gate test
    output and writes a diagnosis file in the same format as diagnose-ci output.

    When `failed_step` is provided (e.g. "dirty_tree", "test_gate"), the
    classification is routed to the pre-test or test path explicitly. Without
    it, the legacy behavior of classifying from output content alone is used.
    """
    if failed_step in _PRE_TEST_STEPS:
        failure_type = "pre_test"
        subtype = failed_step
        failed_tests: list[str] = []
    elif failed_step in _TEST_STEPS:
        failure_type = "test"
        subtype = _classify_test_subtype(test_stdout, test_stderr)
        failed_tests = _extract_failed_tests(test_stdout, test_stderr)
    else:
        failure_type = "test"
        subtype = _classify_test_subtype(test_stdout, test_stderr)
        failed_tests = _extract_failed_tests(test_stdout, test_stderr)

    if not output_dir or not Path(output_dir).is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")
    out_path = Path(output_dir) / "diagnosis.md"

    failed_section = "\n".join(f"- {t}" for t in failed_tests) if failed_tests else "- none"
    log_excerpt = test_stdout.strip() or test_stderr.strip() or "(no output captured)"
    if len(log_excerpt) > 4000:
        log_excerpt = log_excerpt[-4000:]

    content = (
        "# Merge Gate Test Failure Diagnosis\n\n"
        "## Classification\n"
        f"failure_type = {failure_type}\n"
        f"failure_subtype = {subtype}\n\n"
        "## Failed Tests\n"
        f"{failed_section}\n\n"
        "## Log Excerpt\n"
        f"```\n{log_excerpt}\n```\n\n"
        "## Structured Output\n"
        f"failure_type = {failure_type}\n"
        f"failure_subtype = {subtype}\n"
    )

    from autoskillit.core import atomic_write  # noqa: PLC0415

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write(out_path, content)
    except OSError as exc:
        raise RuntimeError(f"Failed to write diagnosis to {out_path}") from exc
    return {"diagnosis_path": str(out_path), "ci_conclusion": "failure"}
