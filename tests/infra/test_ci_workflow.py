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
_PRODUCER_MISS = "steps.cache-lookup.outputs.cache-hit != 'true'"
_PRODUCER_KEY = "${{ steps.cache-identity.outputs.key }}"
_CONSUMER_KEY = "${{ needs.api_simulator_cache.outputs.cache-key }}"


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
        ("pull_request", "main", ("ubuntu-latest",), "none", _BASE_SHA),
        ("merge_group", "main", ("ubuntu-latest",), "none", _BASE_SHA),
        (
            "pull_request",
            "stable",
            ("ubuntu-latest", "macos-15"),
            "none",
            _BASE_SHA,
        ),
        (
            "merge_group",
            "stable",
            ("ubuntu-latest", "macos-15"),
            "none",
            _BASE_SHA,
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
                f"test-base-revision={_BASE_SHA}",
            ),
        ),
        (
            "merge_group",
            "main",
            (
                'os-matrix=["ubuntu-latest"]',
                "test-filter-mode=none",
                f"test-base-revision={_BASE_SHA}",
            ),
        ),
        (
            "pull_request",
            "stable",
            (
                'os-matrix=["ubuntu-latest","macos-15"]',
                "test-filter-mode=none",
                f"test-base-revision={_BASE_SHA}",
            ),
        ),
        (
            "merge_group",
            "stable",
            (
                'os-matrix=["ubuntu-latest","macos-15"]',
                "test-filter-mode=none",
                f"test-base-revision={_BASE_SHA}",
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


@pytest.mark.parametrize(
    ("event_name", "target"),
    [
        ("pull_request", "main"),
        ("merge_group", "main"),
        ("pull_request", "stable"),
        ("merge_group", "stable"),
    ],
)
def test_base_revision_emitted_for_every_event_with_base_sha(
    event_name: str,
    target: str,
) -> None:
    """The relaxation gate needs a trusted base on every reviewed event, not
    only on the develop-target PRs that also run the conservative test filter."""
    _, base_revision = ci_target_policy.resolve_ci_profile(
        event_name,
        _event_payload(event_name, target),
    )

    assert ci_target_policy._COMMIT_SHA_PATTERN.fullmatch(base_revision)


def test_events_requiring_a_base_sha_are_exact() -> None:
    """A reviewed event with no base SHA must fail, not emit an empty base.

    An empty test-base-revision makes every diff-scoped gate skip, which is the
    silent-skip failure the relaxation gate exists to prevent.
    """
    assert ci_target_policy.BASE_SHA_EVENTS == frozenset({"pull_request", "merge_group"})


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
        ("pull_request", {"pull_request": {"base": {"ref": "main"}}}),
        ("pull_request", {"pull_request": {"base": {"ref": "stable"}}}),
        (
            "merge_group",
            {"merge_group": {"base_ref": "refs/heads/main"}},
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
    # Its value is guarded against the Dockerfile pin by test_docker_image.py.
    assert "RG_VERSION" in install_rg["env"]
    # Every runner installs ripgrep from a pinned, SHA256-verified GitHub Releases asset
    # rather than apt or Homebrew: azure.archive.ubuntu.com has a known, chronic throughput
    # instability that repeatedly stalled this step for 10+ minutes in production (issue
    # #4697), and Homebrew cannot pin a version. Assert both paths are gone so neither can
    # silently regress back in.
    assert "sudo apt-get install --yes ripgrep" not in install_rg["run"]
    assert "brew install" not in install_rg["run"]
    assert "github.com/BurntSushi/ripgrep/releases/download/" in install_rg["run"]
    assert '"${CHECKSUM[@]}" -c -' in install_rg["run"]
    assert 'case "${RUNNER_OS}-${RUNNER_ARCH}" in' in install_rg["run"]
    assert "aarch64-apple-darwin" in install_rg["run"]
    assert "x86_64-unknown-linux-musl" in install_rg["run"]
    assert '>> "$GITHUB_PATH"' in install_rg["run"]
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
    channel_b_override = (
        'if [[ "${{ matrix.shard }}" == "execution-channel-b" ]]; then\n'
        "  export AUTOSKILLIT_TEST_FILTER=none\n"
        "fi"
    )
    assert channel_b_override in run_tests["run"]
    assert run_tests["run"].count("export AUTOSKILLIT_TEST_FILTER=none") == 1
    assert run_tests["run"].index(channel_b_override) < run_tests["run"].index(
        "task test-check 2>&1"
    )
    assert "task test-check 2>&1 | tee .autoskillit/temp/test-check.log" in run_tests["run"]
    assert 'TEST_CHECK_EXIT="${PIPESTATUS[0]}"' in run_tests["run"]
    assert '[[ "$AUTOSKILLIT_TEST_FILTER" == "conservative" ]]' in run_tests["run"]
    assert "rg -q '^PYTEST_EXIT_CODE=5$' .autoskillit/temp/test-check.log" in run_tests["run"]
    assert 'exit "$TEST_CHECK_EXIT"' in run_tests["run"]

    workflow_source = workflow_path.read_text(encoding="utf-8")
    assert "GITHUB_EVENT_NAME:" not in workflow_source
    assert "GITHUB_EVENT_PATH:" not in workflow_source


@pytest.mark.parametrize(
    ("step_name", "checker"),
    [
        ("Policy relaxation gate", "check_policy_relaxation.py"),
        ("Cyclomatic complexity gate", "check_complexity.py"),
    ],
)
def test_trusted_base_gate_steps_copy_only_same_revision_helper(
    step_name: str,
    checker: str,
) -> None:
    workflow = load_yaml(_repo_root() / ".github" / "workflows" / "tests.yml")
    test_job_steps = workflow["jobs"]["test"]["steps"]
    gate_steps = [step for step in test_job_steps if step.get("name") == step_name]
    assert len(gate_steps) == 1
    step = gate_steps[0]
    assert step["if"] == (
        "matrix.shard == 'general' && "
        "(github.event_name == 'pull_request' || github.event_name == 'merge_group')"
    )
    assert step["env"] == {"BASE_REVISION": "${{ needs.preflight.outputs.test-base-revision }}"}

    run = step["run"]
    cat_file_index = run.index('git cat-file -e "${base}^{commit}"')
    ancestor_index = run.index('git merge-base --is-ancestor "${base}" HEAD')
    bootstrap_guard = f'git cat-file -e "${{base}}:scripts/{checker}" 2>/dev/null'
    bootstrap_guard_index = run.index(bootstrap_guard)
    # Guard-before-execution order: both reachability guards must precede the
    # bootstrap-branching logic entirely, not merely appear somewhere in the script.
    assert cat_file_index < ancestor_index < bootstrap_guard_index

    copied_checker = f'git show "${{base}}:scripts/{checker}" > "${{RUNNER_TEMP}}/{checker}"'
    helper_probe = 'git cat-file -e "${base}:scripts/_git_plumbing.py" 2>/dev/null'
    copied_helper = (
        'git show "${base}:scripts/_git_plumbing.py" > "${RUNNER_TEMP}/_git_plumbing.py"'
    )
    copied_invocation = (
        f'uv run python "${{RUNNER_TEMP}}/{checker}" --base "${{base}}" '
        '--repo-root "${GITHUB_WORKSPACE}"'
    )
    bootstrap_invocation = f'uv run python scripts/{checker} --base "${{base}}"'
    nested_contract = (
        f"if {bootstrap_guard}; then\n"
        f"  {copied_checker}\n"
        f"  if {helper_probe}; then\n"
        f"    {copied_helper}\n"
        "  fi\n"
        f"  {copied_invocation}\n"
        "else\n"
    )
    assert nested_contract in run
    assert bootstrap_invocation in run[run.index(nested_contract) + len(nested_contract) :]
    assert "${GITHUB_WORKSPACE}/scripts/_git_plumbing.py" not in run
    assert 'scripts/_git_plumbing.py" > "${RUNNER_TEMP}/_git_plumbing.py"' in run


def test_complexity_gate_follows_policy_gate() -> None:
    workflow = load_yaml(_repo_root() / ".github" / "workflows" / "tests.yml")
    test_job_steps = workflow["jobs"]["test"]["steps"]
    complexity_index = next(
        index
        for index, candidate in enumerate(test_job_steps)
        if candidate.get("name") == "Cyclomatic complexity gate"
    )

    relaxation_index = next(
        index
        for index, candidate in enumerate(test_job_steps)
        if candidate.get("name") == "Policy relaxation gate"
    )
    assert complexity_index == relaxation_index + 1


@pytest.mark.parametrize("empty_uv_version", [False, True])
def test_api_simulator_cache_key_is_the_build_identity_not_the_lockfile(
    empty_uv_version: bool,
) -> None:
    workflow_path = _repo_root() / ".github" / "workflows" / "tests.yml"
    producer = load_yaml(workflow_path)["jobs"]["api_simulator_cache"]
    identity = next(step for step in producer["steps"] if step.get("id") == "cache-identity")
    lines = identity["run"].splitlines()
    assert lines[0] == "python3 <<'PY' >> \"$GITHUB_OUTPUT\""
    assert lines[-1] == "PY"
    setup_uv = next(
        step
        for step in producer["steps"]
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    uv_version = setup_uv["with"]["version"]
    rust = next(
        step
        for step in producer["steps"]
        if step.get("uses", "").startswith("dtolnay/rust-toolchain@")
    )
    rust_version = rust["with"]["toolchain"]
    result = subprocess.run(
        [sys.executable, "-B", "-c", "\n".join(lines[1:-1])],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        env=production_interpreter_env()
        | {
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
            "ImageOS": "ubuntu24",
            "UV_VERSION": "" if empty_uv_version else uv_version,
        },
        check=False,
    )
    project = tomllib.loads((_repo_root() / "pyproject.toml").read_text(encoding="utf-8"))
    revision = project["tool"]["uv"]["sources"]["api-simulator"]["rev"]
    python_tag = (
        (_repo_root() / ".python-version").read_text(encoding="utf-8").strip().replace(".", "")
    )
    if empty_uv_version:
        assert result.returncode != 0
        assert "invalid resolved uv version" in result.stderr
        assert not result.stdout
        return
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"key=uv-api-simulator-Linux-X64-ubuntu24-uv{uv_version}-rust{rust_version}-py{python_tag}-{revision}"
    ]
    assert identity["env"] == {"UV_VERSION": "${{ steps.setup-uv.outputs.uv-version }}"}
    assert "hashFiles('uv.lock')" not in workflow_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("missing_secret", ["GH_USER", "GH_PAT"])
def test_cache_build_reports_missing_credentials(tmp_path: Path, missing_secret: str) -> None:
    workflow = load_yaml(_repo_root() / ".github" / "workflows" / "tests.yml")
    auth = next(
        step
        for step in workflow["jobs"]["api_simulator_cache"]["steps"]
        if "git config --global" in step.get("run", "")
    )
    assert auth["env"] == {
        "GH_USER": "${{ secrets.GH_USER }}",
        "GH_PAT": "${{ secrets.GH_PAT }}",
    }
    config = tmp_path / "gitconfig"
    result = subprocess.run(
        ["bash", "-c", auth["run"]],
        capture_output=True,
        text=True,
        check=False,
        env=production_interpreter_env()
        | {
            "GH_USER": "test-user",
            "GH_PAT": "test-token",
            missing_secret: "",
            "GIT_CONFIG_GLOBAL": str(config),
        },
    )
    assert result.returncode != 0
    assert f"private-dependency credentials unavailable: {missing_secret}" in result.stderr
    assert not config.exists()


def test_one_producer_builds_api_simulator_and_test_jobs_only_restore_it() -> None:
    workflow = load_yaml(_repo_root() / ".github" / "workflows" / "tests.yml")
    assert set(workflow.get("on", workflow.get(True))) == {"push", "pull_request", "merge_group"}
    jobs = workflow["jobs"]
    producer = jobs["api_simulator_cache"]
    producer_steps = producer["steps"]
    assert "if" not in producer
    assert producer["runs-on"] == "ubuntu-latest"
    assert "strategy" not in producer
    assert producer["cache-mode"] == "write"
    assert producer["outputs"] == {"cache-key": _PRODUCER_KEY}
    producer_uv = next(
        step for step in producer_steps if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    test_job = jobs["test"]
    test_steps = test_job["steps"]
    test_uv = next(
        step for step in test_steps if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    for steps in (producer_steps, test_steps):
        cache_dir = next(step for step in steps if step.get("id") == "uv-cache-dir")
        assert cache_dir["shell"] == "bash"
        assert cache_dir["run"].splitlines() == [
            'UV_CACHE_DIR="$(uv cache dir)"',
            'echo "UV_CACHE_DIR=$UV_CACHE_DIR" >> "$GITHUB_ENV"',
            'echo "path=$UV_CACHE_DIR" >> "$GITHUB_OUTPUT"',
        ]
    assert producer_uv["id"] == "setup-uv"
    assert producer_uv["with"]["enable-cache"] is False
    assert test_uv["with"]["enable-cache"] is False
    assert producer_uv["with"]["version"] == test_uv["with"]["version"]
    lookup = next(step for step in producer_steps if step.get("id") == "cache-lookup")
    assert lookup["uses"] == "actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830"
    assert lookup["with"] == {
        "lookup-only": True,
        "key": _PRODUCER_KEY,
        "path": "${{ steps.uv-cache-dir.outputs.path }}",
    }
    auth = [step for step in producer_steps if "git config --global" in step.get("run", "")]
    rust = [
        step
        for step in producer_steps
        if step.get("uses", "").startswith("dtolnay/rust-toolchain@")
    ]
    builds = [step for step in producer_steps if "uv sync" in step.get("run", "")]
    saves = [
        step for step in producer_steps if step.get("uses", "").startswith("actions/cache/save@")
    ]
    assert auth and rust and builds and saves
    assert all(step["if"] == _PRODUCER_MISS for step in auth + rust + builds + saves)
    assert all(
        producer_steps.index(lookup) < producer_steps.index(step)
        for step in auth + rust + builds + saves
    )
    build = builds[0]
    assert "uv sync --locked --extra dev" in build["run"]
    assert "uv cache prune --ci" in build["run"]
    assert build["env"]["CARGO_TARGET_DIR"] == "${{ runner.temp }}/api-simulator-target"
    save_steps = [
        (job_name, step)
        for job_name, job in jobs.items()
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("actions/cache/save@")
    ]
    assert len(save_steps) == 1
    save_owner, save = save_steps[0]
    assert save_owner == "api_simulator_cache"
    assert save["uses"] == "actions/cache/save@0057852bfaa89a56745cba8c7296529d2fc39830"
    assert save["with"] == {
        "key": _PRODUCER_KEY,
        "path": "${{ steps.uv-cache-dir.outputs.path }}",
    }
    assert producer_steps.index(build) < producer_steps.index(save)
    assert not any(
        step.get("uses", "").startswith("actions/cache@")
        for job in jobs.values()
        for step in job.get("steps", [])
    )
    preflight = jobs["preflight"]
    preflight_steps = preflight["steps"]
    assert not preflight.get("needs")
    assert any(step.get("run") == "uv lock --check" for step in preflight_steps)
    assert not any("uv sync" in step.get("run", "") for step in preflight_steps)
    preflight_uv = next(
        step for step in preflight_steps if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    assert preflight_uv["with"]["version"] == producer_uv["with"]["version"]
    assert test_job["cache-mode"] == "read"
    assert {"preflight", "api_simulator_cache"} <= set(test_job["needs"])
    assert test_job["if"] == "${{ !cancelled() && needs.preflight.result == 'success' }}"
    require = next(step for step in test_steps if step.get("id") == "require-api-simulator-cache")
    assert require["if"] == (
        "${{ !cancelled() && runner.os == 'Linux' && "
        "needs.api_simulator_cache.result != 'success' }}"
    )
    failure = subprocess.run(
        ["bash", "-c", require["run"]], capture_output=True, text=True, check=False
    )
    assert failure.returncode == 1
    assert failure.stderr.strip() == "api-simulator cache producer did not succeed"
    restore = next(step for step in test_steps if step.get("id") == "uv-cache-restore")
    assert restore["uses"] == "actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830"
    assert restore["if"] == "runner.os == 'Linux'"
    assert restore["with"] == {
        "path": "${{ steps.uv-cache-dir.outputs.path }}",
        "key": _CONSUMER_KEY,
        "fail-on-cache-miss": True,
    }
    install = next(step for step in test_steps if step.get("name") == "Install dependencies")
    verify = next(step for step in test_steps if step.get("id") == "verify-api-simulator")
    assert test_steps.index(require) < test_steps.index(restore) < test_steps.index(install)
    assert verify["if"] == "runner.os == 'Linux'"
    assert verify["run"] == (
        'uv run --no-sync python -B -c "import api_simulator.claude, '
        'api_simulator.http, api_simulator.mcp"'
    )
    assert test_steps.index(verify) == test_steps.index(install) + 1
    project = tomllib.loads((_repo_root() / "pyproject.toml").read_text(encoding="utf-8"))
    assert (
        "api-simulator; sys_platform == 'linux'"
        in project["project"]["optional-dependencies"]["dev"]
    )
    assert not any(
        step.get("uses", "").startswith("dtolnay/rust-toolchain@")
        or "git config --global" in step.get("run", "")
        for job_name, job in jobs.items()
        if job_name != "api_simulator_cache"
        for step in job.get("steps", [])
    )


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
    assert "`task test-local-gate`" in contributing
    assert "`task test-all`" in contributing
    assert "complete\nmanual suite" in contributing
    assert "Conservative filtering\nintersects the other shards" in contributing
    assert "`execution-channel-b` shard sets `AUTOSKILLIT_TEST_FILTER=none`" in contributing
    assert (
        "all\nChannel B tests run for each supported `pull_request` and `merge_group` event"
        in contributing
    )
    assert "Import lint runs only on the retained `execution` shard" in contributing

    agent_rules = (_repo_root() / ".github" / "AGENTS.md").read_text(encoding="utf-8")
    assert "scripts/ci_target_policy.py" in agent_rules
    assert "docs/developer/contributing.md" in agent_rules
    assert "tests/infra/test_ci_workflow.py" in agent_rules
    assert "explicit CI-policy task" in agent_rules
    assert "explicit `actions/cache` owns their uv-cache I/O" in agent_rules
    assert "matrix jobs restore only" in agent_rules
    assert "sole saver" in agent_rules
    assert "never `uv.lock`" in agent_rules
    assert "`gh cache delete <key>`" in agent_rules
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
