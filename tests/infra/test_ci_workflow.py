"""Behavioral and structural tests for branch-targeted CI policy."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import subprocess
import sys
import tomllib
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType

import pytest

from autoskillit.core.io import load_yaml
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

_BASE_SHA = "0123456789abcdef0123456789abcdef01234567"
_POLICY_MODULE_NAME = "_autoskillit_ci_target_policy"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_policy_module() -> ModuleType:
    script = _repo_root() / "scripts" / "ci_target_policy.py"
    spec = importlib.util.spec_from_file_location(_POLICY_MODULE_NAME, script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_POLICY_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


ci_target_policy = _load_policy_module()


def _event_payload(event_name: str, target: str) -> dict[str, object]:
    if event_name == "pull_request":
        return {"pull_request": {"base": {"ref": target, "sha": _BASE_SHA}}}
    if event_name == "merge_group":
        return {
            "merge_group": {
                "base_ref": f"refs/heads/{target}",
                "base_sha": _BASE_SHA,
            }
        }
    if event_name == "push":
        return {"ref": f"refs/heads/{target}"}
    raise AssertionError(f"unsupported test event: {event_name}")


def _run_cli(
    tmp_path: Path,
    *,
    event_name: str | None,
    payload: object | None = None,
    raw_payload: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = production_interpreter_env()
    env.pop("GITHUB_EVENT_NAME", None)
    env.pop("GITHUB_EVENT_PATH", None)
    if event_name is not None:
        env["GITHUB_EVENT_NAME"] = event_name
    if payload is not None or raw_payload is not None:
        event_path = tmp_path / "event.json"
        event_path.write_text(
            raw_payload if raw_payload is not None else json.dumps(payload),
            encoding="utf-8",
        )
        env["GITHUB_EVENT_PATH"] = str(event_path)
    return subprocess.run(
        [sys.executable, str(_repo_root() / "scripts" / "ci_target_policy.py")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _assert_cli_rejection(
    completed: subprocess.CompletedProcess[str],
    expected_error: str,
) -> None:
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.startswith("ci_target_policy: ")
    assert expected_error in completed.stderr
    assert completed.stderr.count("\n") == 1
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize(
    ("event_name", "target", "expected_runners", "expected_filter", "expected_base"),
    [
        ("pull_request", "develop", ("ubuntu-latest",), "conservative", _BASE_SHA),
        ("merge_group", "develop", ("ubuntu-latest",), "conservative", _BASE_SHA),
        ("pull_request", "main", ("ubuntu-latest",), "none", ""),
        ("merge_group", "main", ("ubuntu-latest",), "none", ""),
        (
            "pull_request",
            "stable",
            ("ubuntu-latest", "macos-15"),
            "none",
            "",
        ),
        (
            "merge_group",
            "stable",
            ("ubuntu-latest", "macos-15"),
            "none",
            "",
        ),
        ("push", "main", ("ubuntu-latest",), "none", ""),
        ("push", "stable", ("ubuntu-latest", "macos-15"), "none", ""),
    ],
)
def test_resolver_applies_exact_event_target_policy(
    event_name: str,
    target: str,
    expected_runners: tuple[str, ...],
    expected_filter: str,
    expected_base: str,
) -> None:
    policy, base_revision = ci_target_policy.resolve_ci_profile(
        event_name,
        _event_payload(event_name, target),
    )

    assert policy.os_runners == expected_runners
    assert policy.filter_mode == expected_filter
    assert base_revision == expected_base


@pytest.mark.parametrize(
    ("event_name", "target", "expected_lines"),
    [
        (
            "pull_request",
            "develop",
            (
                'os-matrix=["ubuntu-latest"]',
                "test-filter-mode=conservative",
                f"test-base-revision={_BASE_SHA}",
            ),
        ),
        (
            "merge_group",
            "develop",
            (
                'os-matrix=["ubuntu-latest"]',
                "test-filter-mode=conservative",
                f"test-base-revision={_BASE_SHA}",
            ),
        ),
        (
            "pull_request",
            "main",
            (
                'os-matrix=["ubuntu-latest"]',
                "test-filter-mode=none",
                "test-base-revision=",
            ),
        ),
        (
            "merge_group",
            "main",
            (
                'os-matrix=["ubuntu-latest"]',
                "test-filter-mode=none",
                "test-base-revision=",
            ),
        ),
        (
            "pull_request",
            "stable",
            (
                'os-matrix=["ubuntu-latest","macos-15"]',
                "test-filter-mode=none",
                "test-base-revision=",
            ),
        ),
        (
            "merge_group",
            "stable",
            (
                'os-matrix=["ubuntu-latest","macos-15"]',
                "test-filter-mode=none",
                "test-base-revision=",
            ),
        ),
        (
            "push",
            "main",
            (
                'os-matrix=["ubuntu-latest"]',
                "test-filter-mode=none",
                "test-base-revision=",
            ),
        ),
        (
            "push",
            "stable",
            (
                'os-matrix=["ubuntu-latest","macos-15"]',
                "test-filter-mode=none",
                "test-base-revision=",
            ),
        ),
    ],
)
def test_cli_emits_exact_profile_outputs(
    tmp_path: Path,
    event_name: str,
    target: str,
    expected_lines: tuple[str, ...],
) -> None:
    completed = _run_cli(
        tmp_path,
        event_name=event_name,
        payload=_event_payload(event_name, target),
    )

    assert completed.returncode == 0
    assert completed.stdout.splitlines() == list(expected_lines)
    assert completed.stderr == ""


def test_policy_registry_is_exact_and_deeply_immutable() -> None:
    policy_type = ci_target_policy.CiTargetPolicyDef
    assert dataclasses.is_dataclass(policy_type)
    assert policy_type.__dataclass_params__.frozen
    assert policy_type.__slots__ == ("os_runners", "filter_mode")
    assert dict(ci_target_policy.ALLOWED_TARGETS_BY_EVENT) == {
        "pull_request": frozenset({"develop", "main", "stable"}),
        "merge_group": frozenset({"develop", "main", "stable"}),
        "push": frozenset({"main", "stable"}),
    }
    assert set(ci_target_policy.CI_TARGET_POLICIES) == {"develop", "main", "stable"}
    assert all(
        isinstance(policy.os_runners, tuple)
        for policy in ci_target_policy.CI_TARGET_POLICIES.values()
    )

    with pytest.raises(TypeError):
        ci_target_policy.CI_TARGET_POLICIES["develop"] = policy_type((), "none")
    with pytest.raises(TypeError):
        ci_target_policy.ALLOWED_TARGETS_BY_EVENT["push"] = frozenset()
    with pytest.raises(AttributeError):
        ci_target_policy.ALLOWED_TARGETS_BY_EVENT["push"].add("develop")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ci_target_policy.CI_TARGET_POLICIES["main"].filter_mode = "conservative"


class _AccessRaisingPayload(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        raise AssertionError(f"payload was read for unsupported event via {key!r}")

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0


def test_resolver_rejects_unsupported_event_before_payload_access() -> None:
    with pytest.raises(ValueError, match="unsupported event"):
        ci_target_policy.resolve_ci_profile("workflow_dispatch", _AccessRaisingPayload())


@pytest.mark.parametrize(
    ("event_name", "payload"),
    [
        ("pull_request", {}),
        ("pull_request", {"pull_request": []}),
        ("pull_request", {"pull_request": {"base": {"ref": "develop"}}}),
        (
            "pull_request",
            {"pull_request": {"base": {"ref": "develop", "sha": "not-a-sha"}}},
        ),
        (
            "pull_request",
            {"pull_request": {"base": {"ref": "refs/heads/develop", "sha": _BASE_SHA}}},
        ),
        (
            "merge_group",
            {"merge_group": {"base_ref": "develop", "base_sha": _BASE_SHA}},
        ),
        (
            "merge_group",
            {"merge_group": {"base_ref": "refs/tags/develop", "base_sha": _BASE_SHA}},
        ),
        (
            "merge_group",
            {"merge_group": {"base_ref": "refs/heads/develop"}},
        ),
        ("push", {"ref": "main"}),
        ("push", {"ref": "refs/tags/main"}),
        ("push", {"ref": "refs/heads/develop"}),
        ("push", {"ref": "refs/heads/unknown"}),
    ],
)
def test_resolver_fails_closed_for_malformed_or_unregistered_context(
    event_name: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ci_target_policy.resolve_ci_profile(event_name, payload)


def test_resolver_rejects_allowed_target_without_registered_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed_targets = {
        **ci_target_policy.ALLOWED_TARGETS_BY_EVENT,
        "push": ci_target_policy.ALLOWED_TARGETS_BY_EVENT["push"] | frozenset({"unregistered"}),
    }
    monkeypatch.setattr(
        ci_target_policy,
        "ALLOWED_TARGETS_BY_EVENT",
        allowed_targets,
    )

    with pytest.raises(ValueError, match="has no registered policy"):
        ci_target_policy.resolve_ci_profile(
            "push",
            _event_payload("push", "unregistered"),
        )


@pytest.mark.parametrize(
    ("event_name", "payload", "raw_payload", "expected_error"),
    [
        (
            "pull_request",
            ["not", "an", "object"],
            None,
            "GitHub event payload must be an object",
        ),
        ("pull_request", None, "{invalid-json", "Expecting property name"),
        (
            "pull_request",
            {"merge_group": {}},
            None,
            "pull_request must be an object",
        ),
        (
            "workflow_dispatch",
            _event_payload("pull_request", "develop"),
            None,
            "unsupported event: 'workflow_dispatch'",
        ),
        (
            "pull_request",
            {"pull_request": {"base": {"ref": "develop", "sha": "bad"}}},
            None,
            "base SHA must be a 40-character lowercase commit SHA",
        ),
        (
            "push",
            {"ref": "refs/heads/develop"},
            None,
            "target 'develop' is not allowed for event 'push'",
        ),
    ],
)
def test_cli_failures_use_handled_rejection_contract(
    tmp_path: Path,
    event_name: str,
    payload: object | None,
    raw_payload: str | None,
    expected_error: str,
) -> None:
    completed = _run_cli(
        tmp_path,
        event_name=event_name,
        payload=payload,
        raw_payload=raw_payload,
    )

    _assert_cli_rejection(completed, expected_error)


@pytest.mark.parametrize(
    ("event_name", "payload", "expected_error"),
    [
        (
            None,
            _event_payload("pull_request", "develop"),
            "GITHUB_EVENT_NAME is required",
        ),
        ("pull_request", None, "GITHUB_EVENT_PATH is required"),
    ],
)
def test_cli_requires_inherited_event_environment(
    tmp_path: Path,
    event_name: str | None,
    payload: object | None,
    expected_error: str,
) -> None:
    completed = _run_cli(tmp_path, event_name=event_name, payload=payload)

    _assert_cli_rejection(completed, expected_error)


def test_cli_delegates_to_resolver_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    event_path = tmp_path / "event.json"
    payload = _event_payload("push", "main")
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    calls: list[tuple[str, object]] = []

    def resolve_once(event_name: str, received_payload: object):
        calls.append((event_name, received_payload))
        return ci_target_policy.CI_TARGET_POLICIES["main"], ""

    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setattr(ci_target_policy, "resolve_ci_profile", resolve_once)

    assert ci_target_policy.main() == 0
    assert calls == [("push", payload)]
    assert capsys.readouterr().out.splitlines() == [
        'os-matrix=["ubuntu-latest"]',
        "test-filter-mode=none",
        "test-base-revision=",
    ]


def test_workflow_consumes_one_target_policy_authority() -> None:
    workflow_path = _repo_root() / ".github" / "workflows" / "tests.yml"
    workflow = load_yaml(workflow_path)
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["pull_request"]["branches"] == ["main", "develop", "stable"]
    assert triggers["push"]["branches"] == ["main", "stable"]

    preflight = workflow["jobs"]["preflight"]
    assert preflight["name"] == "Preflight checks"
    assert preflight["outputs"] == {
        "os-matrix": "${{ steps.ci-policy.outputs.os-matrix }}",
        "test-filter-mode": "${{ steps.ci-policy.outputs.test-filter-mode }}",
        "test-base-revision": "${{ steps.ci-policy.outputs.test-base-revision }}",
    }
    steps = preflight["steps"]
    checkout_steps = [
        (index, step)
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    policy_steps = [
        (index, step) for index, step in enumerate(steps) if step.get("id") == "ci-policy"
    ]
    assert len(checkout_steps) == 1
    assert len(policy_steps) == 1
    assert checkout_steps[0][0] == 0
    assert checkout_steps[0][0] < policy_steps[0][0]
    assert policy_steps[0][1]["run"] == ('python3 scripts/ci_target_policy.py >> "$GITHUB_OUTPUT"')

    test_job = workflow["jobs"]["test"]
    assert test_job["name"] == "Test (${{ matrix.shard }}) on ${{ matrix.os }}"
    assert test_job["strategy"]["matrix"] == {
        "os": "${{ fromJSON(needs.preflight.outputs.os-matrix) }}",
        "shard": [
            "execution-channel-b",
            "execution-top-level",
            "execution",
            "recipe",
            "general",
        ],
    }
    test_checkout = next(
        step
        for step in test_job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert test_checkout["with"]["fetch-depth"] == 0
    install_rg = next(step for step in test_job["steps"] if step.get("name") == "Install ripgrep")
    run_tests = next(step for step in test_job["steps"] if step.get("name") == "Run tests")
    lint_imports = [step for step in test_job["steps"] if step.get("name") == "Lint imports"]
    assert test_job["steps"].index(install_rg) < test_job["steps"].index(run_tests)
    assert len(lint_imports) == 1
    assert lint_imports[0]["if"] == "matrix.shard == 'execution'"
    assert lint_imports[0]["run"] == "uv run lint-imports"
    assert install_rg["shell"] == "bash"
    assert "command -v rg" in install_rg["run"]
    # Linux installs ripgrep from a pinned, SHA256-verified GitHub Releases asset rather
    # than apt: azure.archive.ubuntu.com has a known, chronic throughput instability that
    # repeatedly stalled this step for 10+ minutes in production (issue #4697). Assert the
    # apt path is gone so it can't silently regress back in.
    assert "sudo apt-get install --yes ripgrep" not in install_rg["run"]
    assert "github.com/BurntSushi/ripgrep/releases/download/" in install_rg["run"]
    assert "sha256sum -c" in install_rg["run"]
    assert "brew install ripgrep" in install_rg["run"]
    assert 'case "$RUNNER_OS" in' in install_rg["run"]
    assert run_tests["env"] == {
        "AUTOSKILLIT_FILTER_STATS_FILE": (
            "${{ github.workspace }}/.autoskillit/temp/filter-stats.json"
        ),
        "AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED": "true",
        "AUTOSKILLIT_TEST_FILTER": "${{ needs.preflight.outputs.test-filter-mode }}",
        "AUTOSKILLIT_TEST_BASE_REF": "${{ needs.preflight.outputs.test-base-revision }}",
    }
    assert "github.event" not in run_tests["run"]
    assert "develop" not in run_tests["run"]
    assert "task test-check 2>&1 | tee .autoskillit/temp/test-check.log" in run_tests["run"]
    assert 'TEST_CHECK_EXIT="${PIPESTATUS[0]}"' in run_tests["run"]
    assert '[[ "$AUTOSKILLIT_TEST_FILTER" == "conservative" ]]' in run_tests["run"]
    assert "rg -q '^PYTEST_EXIT_CODE=5$' .autoskillit/temp/test-check.log" in run_tests["run"]
    assert 'exit "$TEST_CHECK_EXIT"' in run_tests["run"]

    workflow_source = workflow_path.read_text(encoding="utf-8")
    assert "GITHUB_EVENT_NAME:" not in workflow_source
    assert "GITHUB_EVENT_PATH:" not in workflow_source


def test_workflow_uses_one_explicit_uv_cache_writer() -> None:
    workflow = load_yaml(_repo_root() / ".github" / "workflows" / "tests.yml")
    triggers = workflow.get("on", workflow.get(True))
    assert {"push", "pull_request", "merge_group", "schedule"} <= set(triggers)
    cron = triggers["schedule"][0]["cron"]
    assert cron == "17 3 * * *"
    assert int(cron.split()[0]) != 0

    jobs = workflow["jobs"]
    preflight = jobs["preflight"]
    test_job = jobs["test"]
    metadata = jobs["cache_metadata"]
    primer = jobs["cache_prime"]

    assert preflight["if"] == "github.event_name != 'schedule'"
    assert test_job["if"] == "github.event_name != 'schedule'"
    assert test_job["needs"] == ["preflight", "cache_metadata"]
    assert "if" not in metadata
    assert metadata["runs-on"] == "ubuntu-latest"
    assert primer["if"] == (
        "github.event_name == 'schedule' || "
        "(github.event_name == 'push' && github.ref == 'refs/heads/main')"
    )
    assert primer["needs"] == "cache_metadata"
    assert primer["runs-on"] == "ubuntu-latest"
    assert "strategy" not in primer

    metadata_step = next(step for step in metadata["steps"] if step.get("id") == "cache-identity")
    metadata_script = metadata_step["run"]
    project = tomllib.loads((_repo_root() / "pyproject.toml").read_text(encoding="utf-8"))
    expected_revision = project["tool"]["uv"]["sources"]["api-simulator"]["rev"]
    expected_python_tag = (
        (_repo_root() / ".python-version").read_text(encoding="utf-8").strip().replace(".", "")
    )
    assert ".python-version" in metadata_script
    assert '["tool"]["uv"]["sources"]["api-simulator"]["rev"]' in metadata_script
    assert "[0-9a-fA-F]{40}" in metadata_script
    assert r"\d+\.\d+" in metadata_script
    metadata_lines = metadata_script.splitlines()
    assert metadata_lines[0] == "python3 <<'PY' >> \"$GITHUB_OUTPUT\""
    assert metadata_lines[-1] == "PY"
    metadata_result = subprocess.run(
        [sys.executable, "-B", "-c", "\n".join(metadata_lines[1:-1])],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert metadata_result.returncode == 0, metadata_result.stderr
    assert metadata_result.stdout.splitlines() == [
        f"python_cache_tag={expected_python_tag}",
        f"api_simulator_rev={expected_revision}",
    ]
    assert metadata["outputs"] == {
        "python_cache_tag": "${{ steps.cache-identity.outputs.python_cache_tag }}",
        "api_simulator_rev": "${{ steps.cache-identity.outputs.api_simulator_rev }}",
    }

    setup_uv_steps = {
        job_name: next(
            step for step in job["steps"] if "astral-sh/setup-uv@" in step.get("uses", "")
        )
        for job_name, job in jobs.items()
        if any("astral-sh/setup-uv@" in step.get("uses", "") for step in job.get("steps", []))
    }
    assert set(setup_uv_steps) == {"preflight", "test", "cache_prime"}
    assert all(step["with"]["version"] == "0.9.21" for step in setup_uv_steps.values())
    assert setup_uv_steps["test"]["with"]["enable-cache"] is False
    assert setup_uv_steps["cache_prime"]["with"]["enable-cache"] is False

    restore_pin = "actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830"
    save_pin = "actions/cache/save@0057852bfaa89a56745cba8c7296529d2fc39830"
    primary_key = (
        "uv-${{ runner.os }}-py${{ needs.cache_metadata.outputs.python_cache_tag }}-"
        "${{ needs.cache_metadata.outputs.api_simulator_rev }}-${{ hashFiles('uv.lock') }}"
    )
    restore_prefix = (
        "uv-${{ runner.os }}-py${{ needs.cache_metadata.outputs.python_cache_tag }}-"
        "${{ needs.cache_metadata.outputs.api_simulator_rev }}-"
    )
    for job in (test_job, primer):
        cache_dir = next(step for step in job["steps"] if step.get("id") == "uv-cache-dir")
        restore = next(step for step in job["steps"] if step.get("id") == "uv-cache-restore")
        assert "uv cache dir" in cache_dir["run"]
        assert "UV_CACHE_DIR=" in cache_dir["run"]
        assert '"$GITHUB_ENV"' in cache_dir["run"]
        assert "path=" in cache_dir["run"]
        assert '"$GITHUB_OUTPUT"' in cache_dir["run"]
        assert restore["uses"] == restore_pin
        assert restore["with"] == {
            "path": "${{ steps.uv-cache-dir.outputs.path }}",
            "key": primary_key,
            "restore-keys": restore_prefix,
        }
        assert primary_key == restore_prefix + "${{ hashFiles('uv.lock') }}"

    test_restore = next(step for step in test_job["steps"] if step.get("id") == "uv-cache-restore")
    test_sync = next(step for step in test_job["steps"] if "uv sync" in step.get("run", ""))
    assert test_job["steps"].index(test_restore) < test_job["steps"].index(test_sync)
    assert not any(
        step.get("uses", "").startswith("actions/cache/save@") for step in test_job["steps"]
    )
    assert not any(step.get("uses", "").startswith("actions/cache@") for step in test_job["steps"])

    save_steps = [
        (job_name, step)
        for job_name, job in jobs.items()
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("actions/cache/save@")
    ]
    assert len(save_steps) == 1
    save_owner, save = save_steps[0]
    assert save_owner == "cache_prime"
    assert save["uses"] == save_pin
    assert save["if"] == "steps.uv-cache-restore.outputs.cache-hit != 'true'"
    assert save["with"] == {
        "path": "${{ steps.uv-cache-dir.outputs.path }}",
        "key": "${{ steps.uv-cache-restore.outputs.cache-primary-key }}",
    }

    primer_steps = primer["steps"]
    primer_restore = next(step for step in primer_steps if step.get("id") == "uv-cache-restore")
    primer_auth = next(
        step for step in primer_steps if "git config --global" in step.get("run", "")
    )
    primer_rust = next(
        step for step in primer_steps if "dtolnay/rust-toolchain@" in step.get("uses", "")
    )
    primer_sync = next(step for step in primer_steps if "uv sync" in step.get("run", ""))
    primer_prune = next(
        step for step in primer_steps if "uv cache prune --ci" in step.get("run", "")
    )
    assert primer_steps.index(primer_restore) < primer_steps.index(primer_auth)
    assert primer_steps.index(primer_restore) < primer_steps.index(primer_rust)
    assert primer_steps.index(primer_auth) < primer_steps.index(primer_sync)
    assert primer_steps.index(primer_rust) < primer_steps.index(primer_sync)
    assert primer_steps.index(primer_sync) < primer_steps.index(primer_prune)
    assert primer_steps.index(primer_prune) < primer_steps.index(save)

    preflight_runs = [step.get("run", "") for step in preflight["steps"]]
    assert any(run == "uv lock --check" for run in preflight_runs)
    assert not any("uv sync" in run for run in preflight_runs)
    test_checkout = next(
        step for step in test_job["steps"] if step.get("uses", "").startswith("actions/checkout@")
    )
    assert test_checkout["with"]["fetch-depth"] == 0
    sync_jobs = {
        job_name
        for job_name, job in jobs.items()
        if any("uv sync" in step.get("run", "") for step in job.get("steps", []))
    }
    rust_jobs = {
        job_name
        for job_name, job in jobs.items()
        if any("dtolnay/rust-toolchain@" in step.get("uses", "") for step in job.get("steps", []))
    }
    assert rust_jobs == sync_jobs == {"test", "cache_prime"}


def test_ci_policy_is_recorded_in_durable_contributor_instructions() -> None:
    contributing = (_repo_root() / "docs" / "developer" / "contributing.md").read_text(
        encoding="utf-8"
    )
    assert "| `develop` | `ubuntu-latest` | `conservative` |" in contributing
    assert "| `main` | `ubuntu-latest` | `none` |" in contributing
    assert "| `stable` | `ubuntu-latest`, `macos-15` | `none` |" in contributing
    assert "`pull_request` and `merge_group` each allow `develop|main|stable`" in contributing
    assert "`push` allows only `main|stable`; push-to-develop is rejected" in contributing
    assert "must not incidentally broaden or narrow CI runners or filtering" in contributing
    assert "explicit CI-policy task" in contributing
    for shard in (
        "execution-channel-b",
        "execution-top-level",
        "execution",
        "recipe",
        "general",
    ):
        assert f"| `{shard}` |" in contributing
    assert "`tests/execution/test_process_channel_b.py`" in contributing
    assert "Other direct `tests/execution/test_*.py` files" in contributing
    assert "Nested `tests/execution/**`" in contributing
    assert "before conservative filtering" in contributing
    assert "Import lint runs only on the retained `execution` shard" in contributing

    agent_rules = (_repo_root() / ".github" / "AGENTS.md").read_text(encoding="utf-8")
    assert "scripts/ci_target_policy.py" in agent_rules
    assert "docs/developer/contributing.md" in agent_rules
    assert "tests/infra/test_ci_workflow.py" in agent_rules
    assert "explicit CI-policy task" in agent_rules
    assert "explicit `actions/cache` owns their uv-cache I/O" in agent_rules
    assert "matrix jobs restore only" in agent_rules
    assert "sole saver" in agent_rules
    assert "`api-simulator.rev`" in agent_rules
    assert "regenerated `uv.lock`" in agent_rules
    assert "`fetch-depth: 0`" in agent_rules
    assert "`AUTOSKILLIT_TEST_FILTER=conservative`" in agent_rules
    assert "compute the merge base" in agent_rules


def test_ci_workflow_does_not_pre_regenerate_hooks_json() -> None:
    """The CI workflow must not have a 'Generate hooks.json (if absent)' step.

    That step was tautological: hooks.json is gitignored, CI generates it fresh,
    then the test compared on-disk vs generate_hooks_json() — always passing.
    The new test uses registry.sha256 (committed) which cannot be silenced by pre-regen.
    """
    workflow = load_yaml(_repo_root() / ".github" / "workflows" / "tests.yml")
    step_names = [s.get("name") for job in workflow["jobs"].values() for s in job.get("steps", [])]
    assert not any("Generate hooks.json" in (n or "") for n in step_names), (
        "CI must not pre-regenerate hooks.json — this defeats drift detection. "
        "Remove the 'Generate hooks.json (if absent)' step."
    )
