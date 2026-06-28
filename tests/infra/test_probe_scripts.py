"""Tests for CI-facing probe canary shell scripts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


class TestProbeScriptStructure:
    def test_post_probe_failure_syntax(self) -> None:
        """post-probe-failure.sh passes bash -n syntax check."""
        result = subprocess.run(
            ["bash", "-n", str(SCRIPTS_DIR / "post-probe-failure.sh")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_create_probe_canary_issue_syntax(self) -> None:
        """create-probe-canary-issue.sh passes bash -n syntax check."""
        result = subprocess.run(
            ["bash", "-n", str(SCRIPTS_DIR / "create-probe-canary-issue.sh")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_post_probe_failure_executable(self) -> None:
        assert os.access(SCRIPTS_DIR / "post-probe-failure.sh", os.X_OK)

    def test_create_probe_canary_issue_executable(self) -> None:
        assert os.access(SCRIPTS_DIR / "create-probe-canary-issue.sh", os.X_OK)


class TestPostProbeFailureValidation:
    def test_rejects_missing_args(self) -> None:
        """post-probe-failure.sh exits non-zero when called without arguments."""
        result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "post-probe-failure.sh")],
            capture_output=True,
            text=True,
            env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")},
        )
        assert result.returncode != 0
        assert "Usage" in result.stderr


class TestCreateProbeCanaryIssueValidation:
    def test_rejects_missing_github_repository(self) -> None:
        """Exits non-zero when GITHUB_REPOSITORY is not set."""
        result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "create-probe-canary-issue.sh"), "title", "body"],
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", "/tmp"),
                "GITHUB_TOKEN": "fake-token",
            },
        )
        assert result.returncode != 0
        assert "GITHUB_REPOSITORY" in result.stderr

    def test_rejects_missing_github_token(self) -> None:
        """Exits non-zero when GITHUB_TOKEN is not set."""
        result = subprocess.run(
            ["bash", str(SCRIPTS_DIR / "create-probe-canary-issue.sh"), "title", "body"],
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", "/tmp"),
                "GITHUB_REPOSITORY": "test-org/test-repo",
            },
        )
        assert result.returncode != 0
        assert "GITHUB_TOKEN" in result.stderr
