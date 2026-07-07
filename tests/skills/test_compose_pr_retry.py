"""Tests for compose-pr Step 5 bounded retry behavior.

Extracts the Step 5 bash block from compose-pr/SKILL.md and executes it
with a fake `gh` script on PATH to verify transient retry, terminal
fail-fast, and response-loss recovery.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from autoskillit.recipe._skill_placeholder_parser import (
    extract_bash_blocks,
    extract_step_sections,
)

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "compose-pr"
    / "SKILL.md"
)


def _step5_bash() -> str:
    """Return the literal bash block from compose-pr Step 5."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    sections = extract_step_sections(text)
    step5 = sections.get("Step 5")
    assert step5, "compose-pr/SKILL.md is missing a Step 5 section"
    blocks = extract_bash_blocks(step5)
    assert len(blocks) == 1, f"Expected exactly 1 bash block in Step 5, found {len(blocks)}"
    return blocks[0]


_FAKE_GH_TEMPLATE = """#!/bin/bash
set -u
if [ "$1" = "pr" ] && [ "$2" = "create" ]; then
  ATTEMPT_FILE="$FAKE_GH_ATTEMPT_FILE"
  ATTEMPT=$(cat "$ATTEMPT_FILE")
  ATTEMPT=$((ATTEMPT + 1))
  printf '%s' "$ATTEMPT" > "$ATTEMPT_FILE"
  STDOUT_VAR="FAKE_GH_CREATE_${ATTEMPT}_STDOUT"
  STDERR_VAR="FAKE_GH_CREATE_${ATTEMPT}_STDERR"
  EXIT_VAR="FAKE_GH_CREATE_${ATTEMPT}_EXIT"
  if [ -n "${!STDOUT_VAR+x}" ] && [ -n "${!STDOUT_VAR}" ]; then
    printf '%s\\n' "${!STDOUT_VAR}"
  fi
  if [ -n "${!STDERR_VAR+x}" ] && [ -n "${!STDERR_VAR}" ]; then
    printf '%s\\n' "${!STDERR_VAR}" >&2
  fi
  exit "${!EXIT_VAR:-0}"
fi
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  if [ -n "${FAKE_GH_VIEW_URL+x}" ] && [ -n "$FAKE_GH_VIEW_URL" ]; then
    printf '%s\\n' "$FAKE_GH_VIEW_URL"
  fi
  exit 0
fi
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  exit 0
fi
echo "fake gh: unsupported: $*" >&2
exit 99
"""


def _make_fake_gh(tmp_path: Path) -> Path:
    """Create a fake gh script on a fresh PATH bin dir."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(_FAKE_GH_TEMPLATE, encoding="utf-8")
    gh.chmod(0o755)
    state = tmp_path / "gh_create_attempts"
    state.write_text("0", encoding="utf-8")
    return bin_dir


def _run_step5(
    tmp_path: Path, bin_dir: Path, env_overrides: dict[str, str]
) -> subprocess.CompletedProcess:
    """Execute the extracted Step 5 bash block with fake prerequisites."""
    autotemp = tmp_path / "autotemp"
    autotemp.mkdir(parents=True, exist_ok=True)
    log_dir = autotemp / "compose-pr"
    log_dir.mkdir(parents=True, exist_ok=True)
    body_path = log_dir / "pr_body_testts.md"
    body_path.write_text("Summary\n\nCloses #123\n", encoding="utf-8")

    bash = _step5_bash().replace("{{AUTOSKILLIT_TEMP}}", str(autotemp))
    harness = (
        "set -u\n"
        "ts=testts\n"
        "WORK_DIR=/tmp/no-such-workdir\n"
        "BASE_BRANCH=main\n"
        "FEATURE_BRANCH=test-branch\n"
        "TASK_TITLE='Test PR'\n"
        "export BASE_BRANCH FEATURE_BRANCH TASK_TITLE ts WORK_DIR\n" + bash
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["AUTOSKILLIT_TEMP"] = str(autotemp)
    env["FAKE_GH_ATTEMPT_FILE"] = str(tmp_path / "gh_create_attempts")
    for k, v in env_overrides.items():
        env[k] = v

    return subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )


class TestStep5Retry:
    def test_transient_first_attempt_then_success(self, tmp_path):
        """First gh pr create fails transiently; second attempt succeeds."""
        bin_dir = _make_fake_gh(tmp_path)
        proc = _run_step5(
            tmp_path,
            bin_dir,
            env_overrides={
                "FAKE_GH_CREATE_1_EXIT": "1",
                "FAKE_GH_CREATE_1_STDERR": "HTTP 503 Service Unavailable",
                "FAKE_GH_CREATE_2_EXIT": "0",
                "FAKE_GH_CREATE_2_STDOUT": "https://github.com/owner/repo/pull/42",
            },
        )
        assert proc.returncode == 0
        assert "pr_url = https://github.com/owner/repo/pull/42" in proc.stdout
        assert "HTTP 503" not in proc.stdout
        attempts = int((tmp_path / "gh_create_attempts").read_text())
        assert attempts == 2

    def test_terminal_validation_error_does_not_retry(self, tmp_path):
        """Terminal validation error fails fast; no pr view recovery; stderr surfaces."""
        bin_dir = _make_fake_gh(tmp_path)
        proc = _run_step5(
            tmp_path,
            bin_dir,
            env_overrides={
                "FAKE_GH_CREATE_1_EXIT": "1",
                "FAKE_GH_CREATE_1_STDERR": "HTTP 422 validation failed: missing required field",
                "FAKE_GH_VIEW_URL": "https://github.com/owner/repo/pull/should-not-be-used",
            },
        )
        assert proc.returncode != 0
        # pr view recovery must NOT be triggered — url line is absent
        assert "pr_url = " not in proc.stdout
        assert "should-not-be-used" not in proc.stdout
        # terminal stderr surfaces on stderr
        assert "validation failed" in proc.stderr
        attempts = int((tmp_path / "gh_create_attempts").read_text())
        assert attempts == 1

    def test_response_loss_recovery_emits_view_url(self, tmp_path):
        """Transient failure with successful gh pr view recovery emits recovered URL."""
        bin_dir = _make_fake_gh(tmp_path)
        proc = _run_step5(
            tmp_path,
            bin_dir,
            env_overrides={
                "FAKE_GH_CREATE_1_EXIT": "1",
                "FAKE_GH_CREATE_1_STDERR": "HTTP 502 Bad Gateway",
                "FAKE_GH_VIEW_URL": "https://github.com/owner/repo/pull/77",
            },
        )
        assert proc.returncode == 0
        assert "pr_url = https://github.com/owner/repo/pull/77" in proc.stdout
        # No second create attempt after recovery
        attempts = int((tmp_path / "gh_create_attempts").read_text())
        assert attempts == 1
        # Recovery diagnostics off stdout
        assert "Bad Gateway" not in proc.stdout
