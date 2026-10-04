"""Publication contract for the Docker Hub user image."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _ROOT / ".github" / "workflows"
_DOCKER_IMAGE_WORKFLOW = _WORKFLOWS / "docker-image.yml"
_VERIFY_IMAGE = _ROOT / "scripts" / "docker" / "verify-image"
_IMAGE_NAME = "trecek/autoskillit"
_SUBPROCESS_TIMEOUT_SECONDS = 30
_GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        cwd=str(repo),
        env=_GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    return result.stdout.strip()


def _workflow() -> dict[Any, Any]:
    return load_yaml(_DOCKER_IMAGE_WORKFLOW)


def _step_using(steps: list[dict[str, Any]], action: str) -> dict[str, Any]:
    return next(step for step in steps if str(step.get("uses", "")).startswith(f"{action}@"))


@pytest.mark.parametrize(
    ("history", "ref_type", "channel_tags"),
    [
        pytest.param(("0.1.0", "0.1.1"), "branch", ("develop",), id="develop-version-bump"),
        pytest.param(("0.1.1", None), "branch", None, id="develop-without-bump"),
        pytest.param(("0.11.0",), "tag", ("stable", "latest"), id="release-tag"),
    ],
)
def test_plan_step_resolves_the_published_tags(
    tmp_path: Path,
    history: tuple[str | None, ...],
    ref_type: str,
    channel_tags: tuple[str, ...] | None,
) -> None:
    """``history`` lists one commit per entry: a version to write, or None for a README edit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    for index, version in enumerate(history):
        if version is None:
            (repo / "README.md").write_text(f"revision {index}\n", encoding="utf-8")
        else:
            (repo / "pyproject.toml").write_text(
                f'[project]\nname = "example"\nversion = "{version}"\n', encoding="utf-8"
            )
        _git(repo, "add", "--all")
        _git(repo, "commit", "--quiet", "--message", f"commit {index}")
    tip = _git(repo, "rev-parse", "HEAD")
    output = tmp_path / "github_output"
    output.touch()
    plan_step = next(
        step for step in _workflow()["jobs"]["plan"]["steps"] if step.get("id") == "plan"
    )

    subprocess.run(
        ["bash", "-c", plan_step["run"]],
        cwd=str(repo),
        env={
            **_GIT_ENV,
            # The step's python3 needs tomllib; resolve it to this interpreter.
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "IMAGE_NAME": _IMAGE_NAME,
            "GITHUB_SHA": tip,
            "GITHUB_REF_TYPE": ref_type,
            "GITHUB_OUTPUT": str(output),
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )

    if channel_tags is None:
        assert output.read_text(encoding="utf-8") == "publish=false\n"
        return
    version = history[-1]
    tags = [f"{_IMAGE_NAME}:{tag}" for tag in (version, f"sha-{tip[:7]}", *channel_tags)]
    assert output.read_text(encoding="utf-8").splitlines() == [
        "publish=true",
        f"version={version}",
        "tags<<EOF",
        *tags,
        "EOF",
    ]


class TestDockerImageWorkflow:
    def test_publishes_only_from_develop_and_release_tags(self) -> None:
        workflow = _workflow()

        assert workflow.get("on", workflow.get(True)) == {
            "push": {"branches": ["develop"], "tags": ["v*"]}
        }
        assert workflow["permissions"] == {"contents": "read"}
        assert workflow["concurrency"]["cancel-in-progress"] is False
        assert workflow["env"]["IMAGE_NAME"] == _IMAGE_NAME

    def test_every_action_is_sha_pinned(self) -> None:
        for job in _workflow()["jobs"].values():
            for step in job["steps"]:
                if "uses" in step:
                    assert re.search(r"@[0-9a-f]{40}\b", step["uses"]), step["uses"]

    def test_plan_checkout_reads_the_parent_commit(self) -> None:
        steps = _workflow()["jobs"]["plan"]["steps"]

        assert _step_using(steps, "actions/checkout")["with"]["fetch-depth"] == 2

    def test_publish_builds_the_archived_user_target(self) -> None:
        publish = _workflow()["jobs"]["publish"]
        steps = publish["steps"]
        build = _step_using(steps, "docker/build-push-action")["with"]

        assert publish["needs"] == "plan"
        assert "needs.plan.outputs.publish == 'true'" in publish["if"]
        assert any(
            'git archive "$GITHUB_SHA"' in step.get("run", "") and "tar -x" in step["run"]
            for step in steps
        )
        assert build["target"] == "user"
        assert build["push"] is True
        assert build["context"] == "${{ runner.temp }}/build-context"
        assert build["file"] == "${{ runner.temp }}/build-context/scripts/docker/Dockerfile"
        assert build["tags"] == "${{ needs.plan.outputs.tags }}"
        assert build["cache-from"].startswith("type=gha,scope=docker-image-user")
        assert build["cache-to"].startswith("type=gha,scope=docker-image-user")

    def test_credentials_reach_only_the_login_step(self) -> None:
        steps = _workflow()["jobs"]["publish"]["steps"]
        build = _step_using(steps, "docker/build-push-action")["with"]
        login = _step_using(steps, "docker/login-action")["with"]

        assert not {"build-args", "secrets", "secret-files"} & set(build)
        assert login["username"] == "${{ secrets.DOCKERHUB_USERNAME }}"
        assert login["password"] == "${{ secrets.DOCKERHUB_TOKEN }}"

    def test_pushed_image_is_verified_after_logging_out(self) -> None:
        verify = _workflow()["jobs"]["publish"]["steps"][-1]["run"]

        assert "docker logout" in verify
        assert verify.index("docker logout") < verify.index("scripts/docker/verify-image")


class TestPublicationTriggersFire:
    @pytest.mark.parametrize(
        ("workflow_name", "job_name"),
        [
            ("patch-bump-develop.yml", "bump-patch"),
            ("version-bump.yml", "minor-bump-on-promote"),
            ("release.yml", "release"),
        ],
    )
    def test_version_pushes_authenticate_with_the_pat(
        self, workflow_name: str, job_name: str
    ) -> None:
        """Pushes made with GITHUB_TOKEN start no workflow, so docker-image.yml needs the PAT."""
        steps = load_yaml(_WORKFLOWS / workflow_name)["jobs"][job_name]["steps"]

        assert _step_using(steps, "actions/checkout")["with"]["token"] == "${{ secrets.GH_PAT }}"


class TestVerifyImageScript:
    def test_syntax_is_valid(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(_VERIFY_IMAGE)],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        assert result.returncode == 0, result.stderr

    def test_is_executable(self) -> None:
        assert os.access(_VERIFY_IMAGE, os.X_OK)

    def test_requires_an_image_argument(self) -> None:
        result = subprocess.run(
            ["bash", str(_VERIFY_IMAGE)],
            capture_output=True,
            text=True,
            env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")},
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_history_failure_cannot_read_as_no_credential(self) -> None:
        script = _VERIFY_IMAGE.read_text(encoding="utf-8")
        capture = script.index("image_history=$(docker history")

        assert script.index("|| die", capture) < script.index("grep -Eq", capture)
        assert '<<<"$image_history"' in script
