"""Merge gate test failure diagnosis file writer for smoke_utils run_python steps."""

from __future__ import annotations

from pathlib import Path

import regex as re

_FAILED_TEST_RE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)
_TIMEOUT_RE = re.compile(r"timeout|timed out|TimeoutError", re.IGNORECASE)
_ENV_ERROR_RE = re.compile(
    r"ModuleNotFoundError|ImportError|FileNotFoundError|No such file", re.IGNORECASE
)


def _classify_subtype(test_stdout: str, test_stderr: str) -> str:
    combined = f"{test_stdout}\n{test_stderr}"
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
    **_kwargs: object,
) -> dict[str, str]:
    """Write a structured merge gate failure diagnosis file and return its path.

    Called by run_python from the diagnose_merge_gate step in remediation.yaml,
    implementation.yaml, and implementation-groups.yaml. Parses merge gate test
    output and writes a diagnosis file in the same format as diagnose-ci output.
    """
    subtype = _classify_subtype(test_stdout, test_stderr)
    failed_tests = _extract_failed_tests(test_stdout, test_stderr)

    if output_dir:
        out_path = Path(output_dir) / "diagnosis.md"
    else:
        out_path = Path(".autoskillit") / "temp" / "diagnose-merge-gate" / "diagnosis.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    failed_section = "\n".join(f"- {t}" for t in failed_tests) if failed_tests else "- none"
    log_excerpt = (test_stdout or test_stderr or "(no output captured)").strip()
    if len(log_excerpt) > 4000:
        log_excerpt = log_excerpt[-4000:]

    content = (
        "# Merge Gate Test Failure Diagnosis\n\n"
        "## Classification\n"
        "failure_type = test\n"
        f"failure_subtype = {subtype}\n\n"
        "## Failed Tests\n"
        f"{failed_section}\n\n"
        "## Log Excerpt\n"
        f"```\n{log_excerpt}\n```\n\n"
        "## Structured Output\n"
        f"failure_subtype = {subtype}\n"
    )

    from autoskillit.core import atomic_write  # noqa: PLC0415

    atomic_write(out_path, content)
    return {"diagnosis_path": str(out_path), "ci_conclusion": "failure"}
