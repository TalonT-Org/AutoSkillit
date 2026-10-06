"""Structural tests for the .github/workflows/e2e.yml workflow."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = REPO_ROOT / ".github" / "workflows"
_WORKFLOW_PATH = _WORKFLOWS / "e2e.yml"
_DOCKERFILE = REPO_ROOT / "scripts" / "docker" / "Dockerfile"
_SHA_PIN = re.compile(r"@[0-9a-f]{40}\b")


def _on(workflow: dict) -> dict:
    """Return the triggers; PyYAML (YAML 1.1) parses a bare ``on:`` key as ``True``."""
    return workflow.get("on") or workflow.get(True) or {}


@pytest.fixture(scope="module")
def workflow() -> dict:
    return load_yaml(_WORKFLOW_PATH)


@pytest.fixture(scope="module")
def text() -> str:
    return _WORKFLOW_PATH.read_text(encoding="utf-8")


def _step(workflow: dict, job: str, *, step_id: str | None = None, name: str | None = None):
    return next(
        step
        for step in workflow["jobs"][job]["steps"]
        if (step_id is not None and step.get("id") == step_id)
        or (name is not None and step.get("name") == name)
    )


def _all_steps(workflow: dict):
    for job in workflow["jobs"].values():
        yield from job["steps"]


def _model_branch(run: str) -> tuple[str, str]:
    lines = run.splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.strip() == "if [[ '${{ matrix.kind }}' != 'clean-install' ]]; then"
    )
    end = next(index for index in range(start + 1, len(lines)) if lines[index].strip() == "fi")
    return "\n".join(lines[start + 1 : end]), "\n".join(lines[:start] + lines[end + 1 :])


def _inline_smoke_cleanup(run: str) -> str:
    helper = REPO_ROOT / "scripts" / "e2e" / "smoke-cleanup.sh"
    return run.replace(
        'source "$GITHUB_WORKSPACE/scripts/e2e/smoke-cleanup.sh"',
        helper.read_text(encoding="utf-8"),
    )


_DOCKER_STUB = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
log = Path(os.environ["DOCKER_LOG"])
state = Path(os.environ["DOCKER_STATE"])
container_id = os.environ["CONTAINER_ID"]

def record(operation):
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"operation": operation, "args": args}) + "\n")

if args[0] == "run":
    operation = "cleanup" if "cleanup" in args else "model"
    record(operation)
    if operation == "model":
        cidfile = Path(args[args.index("--cidfile") + 1])
        with cidfile.open("x", encoding="utf-8") as stream:
            stream.write(container_id + "\n")
        state.write_text(os.environ["MODEL_CONTAINER_STATE"], encoding="utf-8")
        raise SystemExit(int(os.environ["MODEL_EXIT"]))
    raise SystemExit(int(os.environ["CLEANUP_EXIT"]))

if args[0] == "rm":
    record("rm")
    status = int(os.environ["RM_EXIT"])
    if status == 0:
        state.write_text("absent", encoding="utf-8")
    raise SystemExit(status)

if args[:2] == ["container", "ls"]:
    record("list")
    status = int(os.environ["LIST_EXIT"])
    if status == 0 and state.read_text(encoding="utf-8") == "present":
        print(container_id)
    raise SystemExit(status)

record("unexpected")
raise SystemExit(99)
"""


def _run_smoke_workflow_shell(workflow: dict, tmp_path: Path, **scenario: str):
    run = _step(workflow, "run", step_id="e2e")["run"].replace("${{ matrix.kind }}", "recipe")
    runner_temp = tmp_path / "runner"
    (runner_temp / "e2e" / "out").mkdir(parents=True)
    (runner_temp / "e2e" / "data").mkdir()
    workspace = tmp_path / "workspace"
    (workspace / "scripts" / "e2e").mkdir(parents=True)
    helper = REPO_ROOT / "scripts" / "e2e" / "smoke-cleanup.sh"
    (workspace / "scripts" / "e2e" / helper.name).write_bytes(helper.read_bytes())
    docker_bin = tmp_path / "bin"
    docker_bin.mkdir()
    docker = docker_bin / "docker"
    docker.write_text(_DOCKER_STUB, encoding="utf-8")
    docker.chmod(0o755)

    log = tmp_path / "docker.jsonl"
    state = tmp_path / "container-state"
    state.write_text("absent", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{docker_bin}{os.pathsep}{env['PATH']}",
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_WORKSPACE": str(workspace),
            "GITHUB_RUN_ID": "1234",
            "GITHUB_RUN_ATTEMPT": "2",
            "TEST": "headless-smoke",
            "IMAGE": "autoskillit-e2e:test",
            "MINIMAX_API_KEY": "model-secret",
            "E2E_SANDBOX_TOKEN": "sandbox-secret",
            "DOCKER_LOG": str(log),
            "DOCKER_STATE": str(state),
            "CONTAINER_ID": "a" * 64,
            "MODEL_EXIT": "0",
            "MODEL_CONTAINER_STATE": "present",
            "RM_EXIT": "0",
            "LIST_EXIT": "0",
            "CLEANUP_EXIT": "0",
            **scenario,
        }
    )
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", run],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    cidfile = runner_temp / "e2e" / "out" / "headless-smoke-1234-2.cid"
    lifecycle = runner_temp / "e2e" / "out" / "headless-smoke-lifecycle.txt"
    statuses = dict(
        line.split("=", maxsplit=1) for line in lifecycle.read_text(encoding="utf-8").splitlines()
    )
    return result, events, cidfile, statuses


@pytest.mark.parametrize(
    (
        "scenario",
        "expected_status",
        "expected_events",
        "expected_cidfile",
        "expected_lifecycle",
    ),
    [
        pytest.param(
            {"MODEL_EXIT": "19", "MODEL_CONTAINER_STATE": "absent", "RM_EXIT": "1"},
            1,
            ["model", "rm", "list", "cleanup"],
            False,
            {
                "model_exit": "19",
                "docker_rm": "1",
                "container_verify": "0",
                "container_removal": "0",
                "remote_cleanup": "0",
            },
            id="model-failure-still-cleans",
        ),
        pytest.param(
            {"RM_EXIT": "23", "MODEL_CONTAINER_STATE": "present"},
            1,
            ["model", "rm", "list", "cleanup"],
            True,
            {
                "model_exit": "0",
                "docker_rm": "23",
                "container_verify": "0",
                "container_removal": "1",
                "remote_cleanup": "0",
            },
            id="removal-failure-still-cleans",
        ),
        pytest.param(
            {"CLEANUP_EXIT": "17"},
            1,
            ["model", "rm", "list", "cleanup"],
            False,
            {
                "model_exit": "0",
                "docker_rm": "0",
                "container_verify": "0",
                "container_removal": "0",
                "remote_cleanup": "17",
            },
            id="cleanup-failure",
        ),
        pytest.param(
            {"MODEL_CONTAINER_STATE": "absent", "RM_EXIT": "1"},
            0,
            ["model", "rm", "list", "cleanup"],
            False,
            {
                "model_exit": "0",
                "docker_rm": "1",
                "container_verify": "0",
                "container_removal": "0",
                "remote_cleanup": "0",
            },
            id="already-absent-container",
        ),
    ],
)
def test_headless_smoke_exit_trap_removes_exact_container_then_runs_cleanup(
    workflow: dict,
    tmp_path: Path,
    scenario: dict[str, str],
    expected_status: int,
    expected_events: list[str],
    expected_cidfile: bool,
    expected_lifecycle: dict[str, str],
) -> None:
    result, events, cidfile, statuses = _run_smoke_workflow_shell(workflow, tmp_path, **scenario)

    assert result.returncode == expected_status, result.stderr
    assert [event["operation"] for event in events] == expected_events
    container_id = "a" * 64
    model = events[0]["args"]
    model_cidfile = Path(model[model.index("--cidfile") + 1])
    assert model_cidfile == cidfile
    model_mounts = [model[index + 1] for index, value in enumerate(model) if value == "--volume"]
    assert any(mount.endswith("/scripts/e2e:/opt/e2e:ro") for mount in model_mounts)
    assert any(mount.endswith(":/artifacts") for mount in model_mounts)
    assert events[1]["args"] == ["rm", "-f", container_id]
    assert events[2]["args"][2:5] == ["--all", "--quiet", "--no-trunc"]
    assert events[2]["args"][-2:] == ["--filter", f"id={container_id}"]
    cleanup = events[-1]["args"]
    assert "--env" in cleanup
    assert cleanup[cleanup.index("--env") + 1] == "E2E_SANDBOX_TOKEN"
    assert "MINIMAX_API_KEY" not in cleanup
    cleanup_mounts = [
        cleanup[index + 1] for index, value in enumerate(cleanup) if value == "--volume"
    ]
    assert any(mount.endswith("/scripts/e2e:/opt/e2e:ro") for mount in cleanup_mounts)
    assert any(mount.endswith(":/artifacts") for mount in cleanup_mounts)
    assert cleanup[-7:] == [
        "cleanup",
        "--test",
        "headless-smoke",
        "--catalog",
        "/opt/e2e/catalog.json",
        "--out",
        "/artifacts",
    ]
    assert cidfile.exists() is expected_cidfile
    if expected_cidfile:
        assert cidfile.read_text(encoding="utf-8") == container_id + "\n"
    for key, value in expected_lifecycle.items():
        assert statuses[key] == value


class TestTriggers:
    def test_pull_request_types_include_labeled(self, workflow: dict) -> None:
        types = _on(workflow)["pull_request"]["types"]
        assert types == ["opened", "synchronize", "reopened", "labeled"]

    def test_merge_group_is_a_trigger(self, workflow: dict) -> None:
        assert "merge_group" in _on(workflow)

    def test_dispatch_takes_a_string_tests_input(self, workflow: dict) -> None:
        assert _on(workflow)["workflow_dispatch"]["inputs"]["tests"]["type"] == "string"


class TestSecrets:
    def test_only_the_minimax_key_and_sandbox_token_are_used(self, text: str) -> None:
        assert set(re.findall(r"secrets\.(\w+)", text)) == {"MINIMAX_API_KEY", "E2E_SANDBOX_TOKEN"}

    @pytest.mark.parametrize("name", ["ANTHROPIC", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY"])
    def test_no_other_provider_credential_is_referenced(self, text: str, name: str) -> None:
        assert name not in text

    def test_secrets_reach_only_the_test_and_redaction_steps(self, workflow: dict) -> None:
        holders = {
            step.get("id")
            for step in _all_steps(workflow)
            if "secrets." in str(step.get("env", {}))
        }
        assert holders == {"e2e", "redact"}
        assert all("secrets." not in str(job.get("env", {})) for job in workflow["jobs"].values())

    @pytest.mark.parametrize("step_id", ["e2e", "redact"])
    def test_model_credentials_are_empty_for_clean_install(
        self, workflow: dict, step_id: str
    ) -> None:
        env = _step(workflow, "run", step_id=step_id)["env"]
        assert env["MINIMAX_API_KEY"] == (
            "${{ matrix.kind != 'clean-install' && secrets.MINIMAX_API_KEY || '' }}"
        )
        assert env["E2E_SANDBOX_TOKEN"] == (
            "${{ matrix.kind != 'clean-install' && secrets.E2E_SANDBOX_TOKEN || '' }}"
        )

    def test_report_step_uses_only_the_workflow_token(self, workflow: dict) -> None:
        env = _step(workflow, "run", name="Report failure")["env"]
        assert env["GH_TOKEN"] == "${{ github.token }}"
        assert "secrets." not in str(env)

    @pytest.mark.parametrize("step_id", ["e2e", "redact"])
    def test_docker_run_forwards_secrets_by_name_only(self, workflow: dict, step_id: str) -> None:
        run = _inline_smoke_cleanup(_step(workflow, "run", step_id=step_id)["run"])
        forwarded = re.findall(r"--env ([A-Z0-9_]+)", run)
        expected = ["MINIMAX_API_KEY", "E2E_SANDBOX_TOKEN"]
        if step_id == "e2e":
            expected.append("E2E_SANDBOX_TOKEN")
        assert forwarded == expected

    def test_redaction_keeps_required_secret_names(self, workflow: dict) -> None:
        run = _step(workflow, "run", step_id="redact")["run"]
        branch, outside = _model_branch(run)
        assert re.findall(r"--secret-env (\S+)", outside) == [
            "MINIMAX_API_KEY",
            "E2E_SANDBOX_TOKEN",
        ]
        assert "--secret-env" not in branch


@pytest.mark.parametrize(
    ("step_id", "common_mounts"),
    [
        (
            "e2e",
            (
                '--volume "$GITHUB_WORKSPACE/scripts/e2e:/opt/e2e:ro"',
                '--volume "$RUNNER_TEMP/e2e/out:/artifacts"',
            ),
        ),
        (
            "redact",
            (
                '--volume "$GITHUB_WORKSPACE/scripts/e2e:/opt/e2e:ro"',
                '--volume "$RUNNER_TEMP/e2e:/e2e"',
            ),
        ),
    ],
)
def test_clean_install_uses_the_common_user_image_and_artifacts(
    workflow: dict, step_id: str, common_mounts: tuple[str, ...]
) -> None:
    run = _inline_smoke_cleanup(_step(workflow, "run", step_id=step_id)["run"])
    branch, outside = _model_branch(run)

    assert run.count("docker_args=()") == 1
    assert '"${docker_args[@]}"' in outside
    assert "docker run --rm" in outside
    assert '"$IMAGE"' in outside
    for mount in common_mounts:
        assert mount in outside
    assert not any(mount in branch for mount in common_mounts)

    credential_lines = [
        line.strip() for line in branch.splitlines() if "--env MINIMAX_API_KEY" in line
    ]
    assert len(credential_lines) == 1
    assert credential_lines[0].startswith("docker_args+=(")
    assert re.findall(r"--env ([A-Z0-9_]+)", credential_lines[0]) == [
        "MINIMAX_API_KEY",
        "E2E_SANDBOX_TOKEN",
    ]
    if step_id == "e2e":
        assert not re.search(r"--env MINIMAX_API_KEY\b", outside)
        assert re.findall(r"--env ([A-Z0-9_]+)", outside) == ["E2E_SANDBOX_TOKEN"]
    else:
        assert not re.search(r"--env (?:MINIMAX_API_KEY|E2E_SANDBOX_TOKEN)\b", outside)

    data_mounts = re.findall(r'--volume "\$RUNNER_TEMP/e2e/data:[^"]+"', run)
    if step_id == "e2e":
        assert len(data_mounts) == 1
        assert data_mounts[0] in branch
        assert data_mounts[0] not in outside
        assert any(
            line.strip().startswith("docker_args+=(") and data_mounts[0] in line
            for line in branch.splitlines()
        )
    else:
        assert not data_mounts


def test_user_image_is_built_for_the_matrix_run_and_workflow_does_not_install_or_init(
    workflow: dict,
) -> None:
    build = _step(workflow, "run", name="Build the user image")
    assert build["with"]["target"] == "user"
    for step in _all_steps(workflow):
        run = step.get("run", "")
        assert not re.search(r"\bautoskillit\s+(?:install|init)\b", run)


class TestRunJob:
    def test_one_repository_wide_queue(self, workflow: dict) -> None:
        assert workflow["jobs"]["run"]["concurrency"] == {
            "group": "e2e",
            "queue": "max",
            "cancel-in-progress": False,
        }

    def test_matrix_runs_one_test_at_a_time(self, workflow: dict) -> None:
        strategy = workflow["jobs"]["run"]["strategy"]
        assert strategy["max-parallel"] == 1
        assert strategy["fail-fast"] is False

    def test_runs_only_when_selected_and_times_out_per_test(self, workflow: dict) -> None:
        run = workflow["jobs"]["run"]
        assert "needs.select.outputs.selected" in run["if"]
        assert "matrix.timeout_minutes" in str(run["timeout-minutes"])

    def test_permissions(self, workflow: dict) -> None:
        assert workflow["jobs"]["run"]["permissions"] == {"contents": "read", "issues": "write"}

    def test_data_mount_targets_the_user_image_autoskillit_data_dir(self, workflow: dict) -> None:
        stage = _DOCKERFILE.read_text(encoding="utf-8").split("FROM toolchain AS user", 1)[1]
        home = re.search(r"ENV HOME=(\S+)", stage)
        assert home is not None
        run = _step(workflow, "run", step_id="e2e")["run"]
        mounts = re.findall(r'--volume "\$RUNNER_TEMP/e2e/data:([^"]+)"', run)
        assert mounts == [f"{home.group(1)}/.local/share/autoskillit"]

    def test_upload_sends_only_a_successfully_redacted_copy(self, workflow: dict) -> None:
        upload = next(
            step for step in _all_steps(workflow) if "upload-artifact" in step.get("uses", "")
        )
        assert "steps.redact.outcome == 'success'" in upload["if"]
        assert upload["with"]["path"].endswith("e2e/upload")
        assert upload["with"]["include-hidden-files"] is True

    def test_report_runs_for_a_failed_test_or_redaction(self, workflow: dict) -> None:
        report = _step(workflow, "run", name="Report failure")
        assert report["if"] == (
            "failure() && (steps.e2e.outcome == 'failure' || steps.redact.outcome == 'failure')"
        )
        assert "STAGE" in report["env"]
        assert '"$STAGE"' in report["run"]

    def test_build_context_export_matches_the_docker_image_workflow(self, workflow: dict) -> None:
        name = "Export the archived build context"
        docker_image = load_yaml(_WORKFLOWS / "docker-image.yml")
        ours = _step(workflow, "run", name=name)["run"]
        assert ours == _step(docker_image, "publish", name=name)["run"]


class TestGate:
    def test_gate_always_runs_after_select_and_run(self, workflow: dict) -> None:
        gate = workflow["jobs"]["e2e-gate"]
        assert gate["if"] == "always()"
        assert gate["needs"] == ["select", "run"]
        assert "name" not in gate

    def test_gate_judges_with_the_selector(self, workflow: dict) -> None:
        runs = [step.get("run", "") for step in workflow["jobs"]["e2e-gate"]["steps"]]
        assert any("e2e_select.py gate" in run for run in runs)


def test_every_action_is_sha_pinned(workflow: dict) -> None:
    for step in _all_steps(workflow):
        if "uses" in step:
            assert _SHA_PIN.search(step["uses"]), step["uses"]
