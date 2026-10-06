"""Tests for scripts/e2e/e2e_harness.py with a recording fake in place of real processes."""

from __future__ import annotations

import json
import math
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
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
_SMOKE_FAILURE = {
    "severity": "error",
    "check": "workflow_failed",
    "message": "model reported failure",
    "issue": "https://github.com/TalonT-Org/AutoSkillit/issues/5232",
}


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


def _smoke_case(expected_failures=None):
    catalog = e2e_catalog.load_catalog()
    test = catalog.get("headless-smoke")
    test = replace(test, expected_failures=tuple(expected_failures or ()))
    catalog = replace(
        catalog,
        tests=tuple(test if entry.name == test.name else entry for entry in catalog.tests),
    )
    return catalog, test


def _run_git(cwd: Path, *argv: str, env: dict[str, str]) -> str:
    result = subprocess.run(
        ["git", *argv], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _smoke_sources(*, valid_test: bool = True) -> tuple[str, str]:
    code = '"""Sandbox smoke target."""\n\ndef smoke_canary() -> bool:\n    return True\n'
    if valid_test:
        test_code = (
            "import unittest\n"
            "from sandbox.text import smoke_canary\n\n"
            "class SmokeCanaryTest(unittest.TestCase):\n"
            "    def test_smoke_canary(self):\n"
            "        self.assertTrue(smoke_canary())\n"
        )
    else:
        test_code = "import unittest\n\nclass SmokeCanaryTest:\n    pass\n"
    return code, test_code


def _smoke_metadata(
    argv: list[str], mode: str, state: dict[str, str], remote_url: str
) -> subprocess.CompletedProcess[str]:
    if argv[:2] == ["git", "remote"]:
        assert argv == ["git", "remote", "get-url", "origin"]
        return _completed(argv, remote_url + "\n")
    if argv[:2] == ["git", "branch"]:
        assert argv == ["git", "branch", "--show-current"]
        return _completed(argv, "main\n")
    assert argv[:2] == ["git", "ls-remote"]
    state["branch_name"] = argv[-1].removeprefix("refs/heads/")
    assert argv == [
        "git",
        "ls-remote",
        "--exit-code",
        "origin",
        f"refs/heads/{state['branch_name']}",
    ]
    collision = mode == "branch-collision"
    record = f"{'a' * 40}\trefs/heads/{state['branch_name']}\n" if collision else ""
    return _completed(argv, record, returncode=0 if collision else 2)


def _smoke_fleet(
    argv: list[str],
    call: RecordedCall,
    clone: Path,
    out: Path,
    fleet: subprocess.CompletedProcess[str] | BaseException | None,
    state: dict[str, str],
) -> subprocess.CompletedProcess[str] | BaseException:
    assert argv[:3] == ["autoskillit", "fleet", "run"] and call.cwd == clone
    assert (clone / ".autoskillit" / "recipes" / "sandbox-smoke.yaml").is_file()
    assert (out / "smoke-launched").is_file()
    descriptor = json.loads((out / "smoke-lifecycle.json").read_text(encoding="utf-8"))
    state.update(branch_name=descriptor["branch_name"], base_oid="b" * 40, head_oid="a" * 40)
    fields = [
        argv[index + 1].split("=", 1) for index, item in enumerate(argv[:-1]) if item == "-i"
    ]
    ingredients = dict(fields)
    assert len(fields) == len(ingredients) == 5
    assert ingredients == {
        "source_dir": str(clone),
        "repository": state["repository"],
        "remote_url": state["remote_url"],
        "base_branch": "main",
        "branch_name": descriptor["branch_name"],
    }
    if isinstance(fleet, BaseException):
        return fleet
    if fleet is not None:
        return _completed(
            argv,
            fleet.stdout or "",
            returncode=fleet.returncode,
            stderr=fleet.stderr or "",
        )
    return _completed(
        argv,
        json.dumps({"success": True, "kind": "completed", "dispatch_status": "success"}),
    )


def _smoke_pr_list(
    argv: list[str], mode: str, state: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    branch = argv[argv.index("--head") + 1]
    assert branch == state["branch_name"]
    assert argv[argv.index("--repo") + 1] == state["repository"]
    fields = (
        "number,state,headRefName,headRefOid,baseRefOid,additions,deletions,"
        "headRepository,headRepositoryOwner"
    )
    assert argv == [
        "gh",
        "pr",
        "list",
        "--repo",
        state["repository"],
        "--state",
        "all",
        "--limit",
        "100",
        "--json",
        fields,
        "--head",
        branch,
    ]
    if mode == "no-pr":
        rows = []
    else:
        rows = [
            {
                "number": 8,
                "state": "OPEN" if mode == "open-pr" else "CLOSED",
                "headRefName": "other-branch" if mode == "wrong-pr-branch" else branch,
                "headRefOid": "d" * 40 if mode == "wrong-pr-head" else state["head_oid"],
                "baseRefOid": state["base_oid"],
                "additions": 3,
                "deletions": 0,
                "headRepository": {"name": state["repository"].split("/", 1)[1]},
                "headRepositoryOwner": {"login": state["repository"].split("/", 1)[0]},
            }
        ]
    return _completed(argv, json.dumps(rows))


def _smoke_ref(
    argv: list[str], mode: str, state: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    assert argv[argv.index("--method") + 1] == "GET"
    branch = argv[-1].rsplit("/", 1)[-1]
    assert branch == state["branch_name"]
    assert argv == [
        "gh",
        "api",
        "--include",
        "--method",
        "GET",
        f"/repos/{state['repository']}/git/ref/heads/{branch}",
    ]
    if mode == "missing-ref":
        return _completed(argv, "HTTP/2.0 404 Not Found\r\n\r\n", returncode=1)
    ref_oid = "d" * 40 if mode == "wrong-ref" else state["head_oid"]
    body = json.dumps({"ref": f"refs/heads/{branch}", "object": {"sha": ref_oid}})
    return _completed(argv, f"HTTP/2.0 200 OK\r\n\r\n{body}")


def _smoke_git_verification(
    argv: list[str], mode: str, state: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    if argv[1] == "fetch":
        assert argv == [
            "git",
            "fetch",
            "--no-tags",
            "origin",
            state["head_oid"],
            state["base_oid"],
        ]
        return _completed(argv)
    if argv[1] == "cat-file":
        assert argv == ["git", "cat-file", "-t", state["head_oid"]]
        return _completed(argv, "commit\n")
    if argv[1] == "merge-base":
        assert argv == ["git", "merge-base", state["base_oid"], state["head_oid"]]
        return _completed(argv, state["base_oid"] + "\n")
    if argv[1] == "diff":
        assert argv == ["git", "diff", "--name-only", state["base_oid"], state["head_oid"], "--"]
        paths = ["sandbox/text.py", "tests/test_smoke_canary.py"]
        if mode == "earlier-unrelated-commit":
            paths.append("sandbox/unrelated.py")
        return _completed(argv, "\n".join(paths) + "\n")
    prefix = state["head_oid"] + ":"
    assert argv[1] == "show" and argv[2].startswith(prefix)
    path = argv[2][len(prefix) :]
    assert path in ("sandbox/text.py", "tests/test_smoke_canary.py")
    code, test_code = _smoke_sources(valid_test=mode != "invalid-canary")
    return _completed(argv, code if path == "sandbox/text.py" else test_code)


def _smoke_unittest(argv: list[str], call: RecordedCall) -> subprocess.CompletedProcess[str]:
    assert argv[0] == harness.sys.executable and argv[1:2] == ["-c"]
    assert "unittest.defaultTestLoader.discover" in argv[2]
    return subprocess.run(
        argv,
        cwd=call.cwd,
        env=call.env,
        capture_output=True,
        text=True,
        timeout=call.timeout,
        check=False,
    )


def _smoke_setup(
    argv: list[str], clone: Path, repository: str
) -> subprocess.CompletedProcess[str]:
    if argv[:3] == ["git", "config", "--global"]:
        assert argv in (
            ["git", "config", "--global", "user.name", "AutoSkillit E2E"],
            [
                "git",
                "config",
                "--global",
                "user.email",
                "autoskillit-e2e@users.noreply.github.com",
            ],
        )
        return _completed(argv)
    if argv[:2] == ["gh", "auth"]:
        assert argv[2:] in (["login", "--hostname", "github.com", "--with-token"], ["setup-git"])
        return _completed(argv)
    assert argv == ["gh", "repo", "clone", repository, str(clone)]
    return _completed(argv)


def _smoke_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str = "success",
    fleet: subprocess.CompletedProcess[str] | BaseException | None = None,
) -> tuple[FakeRunner, Path]:
    clone = tmp_path / "sandbox"
    clone.mkdir()
    monkeypatch.setattr(harness, "SANDBOX_CLONE", clone)
    out = tmp_path / "out"
    catalog, _test = _smoke_case()
    repository = catalog.sandbox_repository
    remote_url = f"https://github.com/{repository}.git"
    state = {"repository": repository, "remote_url": remote_url}
    routes = {
        ("git", "config"): lambda argv, _call: _smoke_setup(argv, clone, repository),
        ("gh", "auth"): lambda argv, _call: _smoke_setup(argv, clone, repository),
        ("gh", "repo"): lambda argv, _call: _smoke_setup(argv, clone, repository),
        ("git", "remote"): lambda argv, _call: _smoke_metadata(argv, mode, state, remote_url),
        ("git", "branch"): lambda argv, _call: _smoke_metadata(argv, mode, state, remote_url),
        ("git", "ls-remote"): lambda argv, _call: _smoke_metadata(argv, mode, state, remote_url),
        ("autoskillit", "fleet"): lambda argv, call: _smoke_fleet(
            argv, call, clone, out, fleet, state
        ),
        ("gh", "pr"): lambda argv, _call: _smoke_pr_list(argv, mode, state),
        ("gh", "api"): lambda argv, _call: _smoke_ref(argv, mode, state),
        ("git", "fetch"): lambda argv, _call: _smoke_git_verification(argv, mode, state),
        ("git", "cat-file"): lambda argv, _call: _smoke_git_verification(argv, mode, state),
        ("git", "merge-base"): lambda argv, _call: _smoke_git_verification(argv, mode, state),
        ("git", "diff"): lambda argv, _call: _smoke_git_verification(argv, mode, state),
        ("git", "show"): lambda argv, _call: _smoke_git_verification(argv, mode, state),
        (harness.sys.executable, "-c"): _smoke_unittest,
    }
    runner: FakeRunner

    def handle(argv: list[str]):
        if len(argv) < 2:
            raise AssertionError(f"unexpected smoke command: {argv!r}")
        route = routes.get((argv[0], argv[1]))
        if route is None:
            raise AssertionError(f"unexpected smoke command: {argv!r}")
        return route(argv, runner.calls[-1])

    runner = FakeRunner(handle)
    return runner, clone


def _run_smoke(tmp_path: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, env=None):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    catalog, test = _smoke_case()
    out = tmp_path / "out"
    failures = harness.run_test(
        test,
        catalog,
        out=out,
        home=tmp_path / "home",
        env=env if env is not None else _env(HOME=str(tmp_path / "home")),
        runner=runner,
    )
    return failures, json.loads((out / "result.json").read_text(encoding="utf-8")), out, catalog


def test_atomic_json_cleans_failed_write_without_replacing_target(tmp_path: Path) -> None:
    target = tmp_path / "evidence.json"
    target.write_text('{"existing": true}\n', encoding="utf-8")

    with pytest.raises(TypeError):
        harness._atomic_json(target, {"invalid": object()})

    assert target.read_text(encoding="utf-8") == '{"existing": true}\n'
    assert list(tmp_path.iterdir()) == [target]


class TestSandboxSmokeFixture:
    @pytest.mark.parametrize(
        ("original", "replacement"),
        [
            pytest.param("from sandbox.text import smoke_canary\n", "", id="missing-import"),
            pytest.param("unittest.TestCase", "object", id="wrong-base-class"),
            pytest.param("def test_smoke_canary", "def helper_smoke_canary", id="no-test-method"),
            pytest.param("self.assertTrue", "print", id="no-assert-call"),
            pytest.param(
                "self.assertTrue(smoke_canary())",
                "self.assertIs(True, True)",
                id="constant-assertion",
            ),
            pytest.param("self.assertTrue(smoke_canary())", "(", id="invalid-syntax"),
        ],
    )
    def test_canary_sources_reject_each_missing_requirement(self, original, replacement):
        code, test_code = _smoke_sources()
        assert harness._canary_sources_valid(code, test_code)
        assert not harness._canary_sources_valid(code, test_code.replace(original, replacement))

    def test_lifecycle_descriptor_returns_only_validated_fields(self):
        catalog, test = _smoke_case()
        expected = {
            "test": test.name,
            "repository": catalog.sandbox_repository,
            "branch_name": "e2e-smoke-" + "a" * 32,
            "base_branch": "main",
        }
        descriptor = {**expected, "extra": {"unvalidated": True}}

        assert (
            harness._validate_smoke_descriptor(descriptor, test, catalog.sandbox_repository)
            == expected
        )

    def test_fixture_stages_discovers_and_passes_the_production_validator(self, tmp_path: Path):
        from autoskillit.recipe.io import find_recipe_by_name, load_recipe
        from autoskillit.recipe.validator import validate_recipe_structure

        _catalog, test = _smoke_case()
        assert test.recipe is not None and test.recipe_fixture is not None
        project = tmp_path / "project"
        target = project / ".autoskillit" / "recipes" / f"{test.recipe}.yaml"
        target.parent.mkdir(parents=True)
        source = Path(harness.__file__).with_name("recipes") / test.recipe_fixture
        target.write_bytes(source.read_bytes())

        discovered = find_recipe_by_name(str(test.recipe), project)
        assert discovered is not None
        assert discovered.path == target
        recipe = load_recipe(discovered.path)
        assert validate_recipe_structure(recipe) == []

        skill_steps = [step for step in recipe.steps.values() if step.tool == "run_skill"]
        assert len(skill_steps) == 1
        worker = skill_steps[0]
        assert worker.skill_name == "smoke-task"
        prompt = worker.with_args["skill_command"].lower()
        assert "one new commit" in prompt and "only sandbox/text.py" in prompt
        discipline = " ".join(recipe.kitchen_rules).lower()
        assert "child agents" in discipline and "replay" in discipline
        assert recipe.steps["setup"].with_args["cwd"] == "${{ context.worktree_path }}"
        assert worker.with_args["cwd"] == "${{ context.worktree_path }}"
        assert recipe.steps["test"].tool == "test_check"
        assert recipe.steps["test"].with_args["worktree_path"] == "${{ context.worktree_path }}"
        assert recipe.steps["push"].with_args["clone_path"] == "${{ context.worktree_path }}"
        assert recipe.steps["push"].on_success == "create_pull_request"
        assert recipe.steps["create_pull_request"].on_success == "close_pull_request"
        assert recipe.steps["close_pull_request"].on_success == "done"
        assert "DO NOT MERGE" in recipe.steps["create_pull_request"].with_args["cmd"]
        executable = [step for step in recipe.steps.values() if step.tool or step.python]
        assert all(
            step.retries == 0 and step.on_failure == "failed" and step.on_exhausted == "failed"
            for step in executable
        )
        done = recipe.steps["done"]
        failed = recipe.steps["failed"]
        assert done.action == "stop" and '"success": true' in done.message
        assert failed.action == "stop"
        assert all(
            word in failed.message.lower() for word in ("success", "false", "reason", "evidence")
        )
        assert "do not call any tools" in failed.message.lower()

    def test_config_branch_survives_fleet_validation_and_binds_to_staged_recipe(
        self, tmp_path: Path
    ) -> None:
        from typing import Any, cast

        from autoskillit.config import (
            build_config_authoritative_layer,
            resolve_ingredient_defaults,
            strip_server_authoritative_overrides,
        )
        from autoskillit.fleet.dispatch._lineage import _validate_and_materialize_ingredients
        from autoskillit.recipe import bind_recipe
        from autoskillit.recipe.io import load_recipe

        catalog, test = _smoke_case()
        assert test.recipe is not None and test.recipe_fixture is not None
        project = tmp_path / "sandbox"
        recipes = project / ".autoskillit" / "recipes"
        recipes.mkdir(parents=True)
        (project / ".autoskillit" / "config.yaml").write_text(
            "branching:\n  default_base_branch: main\n", encoding="utf-8"
        )
        source = Path(harness.__file__).with_name("recipes") / test.recipe_fixture
        staged = recipes / f"{test.recipe}.yaml"
        staged.write_bytes(source.read_bytes())
        recipe = load_recipe(staged)
        base_branch = recipe.ingredients["base_branch"]
        assert base_branch.default == "" and base_branch.authority == "config"
        assert base_branch.required is False

        caller_ingredients = {
            "source_dir": str(project),
            "repository": catalog.sandbox_repository,
            "remote_url": f"https://github.com/{catalog.sandbox_repository}.git",
            "branch_name": "e2e-smoke-" + "a" * 32,
            "base_branch": "main",
        }
        stripped, stripped_names = strip_server_authoritative_overrides(caller_ingredients)
        assert stripped_names == frozenset({"base_branch"})
        defaults = resolve_ingredient_defaults(project)
        config_layer = build_config_authoritative_layer(defaults)
        assert config_layer["base_branch"] == "main"

        materialized = _validate_and_materialize_ingredients(
            effective_ingredients=stripped,
            full_recipe=recipe,
            dispatches_dir=tmp_path / "dispatches",
            campaign_id="e2e-smoke",
            dispatch_id="smoke-dispatch",
            provenance=cast(Any, None),
            state_path=project / ".autoskillit" / "state.json",
            effective_name=test.recipe,
            tool_ctx=cast(Any, None),
        )
        assert isinstance(materialized, dict)
        assert "base_branch" not in materialized

        projection = bind_recipe(recipe, ingredient_values={**materialized, **config_layer})
        invocation = projection.invocations["create_pull_request"]
        command = next(
            value.effective_value for value in invocation.mcp_kwargs if value.name == "cmd"
        )
        assert invocation.is_valid
        assert isinstance(command, str) and '--base "main"' in command


class TestSandboxSmokeFlow:
    def test_recipe_path_traversal_is_rejected_before_staging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, test = _smoke_case()
        runner, clone = _smoke_runner(tmp_path, monkeypatch)
        test = replace(test, recipe="../escaped")

        failures, runtime = harness.prepare_smoke(
            test, catalog.sandbox_repository, tmp_path / "out", _env(), runner
        )

        assert failures == ["smoke setup: recipe must be a basename"]
        assert runtime is None
        assert not (clone / ".autoskillit").exists()
        assert runner.calls == []

    def test_success_verifies_exact_closed_pr_ref_complete_diff_and_canary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner, clone = _smoke_runner(tmp_path, monkeypatch)
        external_env = _env(HOME=str(tmp_path / "home"))
        failures, result, out, _catalog = _run_smoke(
            tmp_path, runner, monkeypatch, env=external_env
        )

        descriptor = json.loads((out / "smoke-lifecycle.json").read_text(encoding="utf-8"))
        evidence = json.loads((out / "smoke-evidence.json").read_text(encoding="utf-8"))
        settings = json.loads(
            (tmp_path / "home" / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        assert failures == []
        assert result["outcome"] == "passed"
        assert result["expected_findings"] == []
        assert descriptor == {
            "repository": _catalog.sandbox_repository,
            "base_branch": "main",
            "branch_name": descriptor["branch_name"],
            "test": "headless-smoke",
        }
        assert descriptor["branch_name"].startswith("e2e-smoke-")
        assert len(descriptor["branch_name"]) == len("e2e-smoke-") + 32
        assert (out / "smoke-launched").read_text(encoding="utf-8") == "headless-smoke\n"
        assert (clone / ".autoskillit" / "recipes" / "sandbox-smoke.yaml").is_file()
        assert evidence["branch_name"] == descriptor["branch_name"]
        assert evidence["pre_cleanup_ref"]["object"]["sha"] == evidence["head_commit"]
        assert evidence["canary_verified"] is True
        assert settings["env"] == _EXPECTED_SETTINGS_ENV
        assert not (clone / ".claude" / "settings.json").exists()
        assert not (clone / ".autoskillit" / "config.yaml").exists()
        assert all(
            not any(name.startswith("ANTHROPIC_") or "OAUTH" in name for name in call.env)
            for call in runner.calls
            if call.env is not None
        )
        assert [call.input for call in runner.calls if call.input] == [SANDBOX_TOKEN]
        assert MINIMAX_KEY not in (out / "smoke-lifecycle.json").read_text(encoding="utf-8")
        assert SANDBOX_TOKEN not in (out / "smoke-evidence.json").read_text(encoding="utf-8")
        assert sum(call.argv[:2] == ["git", "show"] for call in runner.calls) == 2
        assert sum(call.argv[:3] == ["gh", "pr", "list"] for call in runner.calls) == 2

    @pytest.mark.parametrize(
        "fault",
        [
            "nonzero",
            "timeout",
            "exception",
            "no-pr",
            "open-pr",
            "wrong-pr-branch",
            "wrong-pr-head",
            "wrong-ref",
            "missing-ref",
            "earlier-unrelated-commit",
            "invalid-canary",
        ],
    )
    def test_failed_launch_or_verification_keeps_lifecycle_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
    ) -> None:
        mode = fault if fault not in {"nonzero", "timeout", "exception"} else "success"
        fleet = None
        if fault == "nonzero":
            fleet = _completed(
                [],
                json.dumps(
                    {
                        "success": False,
                        "kind": "completed",
                        "dispatch_status": "failed",
                        "error": "workflow_failed",
                        "user_visible_message": "model reported failure",
                    }
                ),
                returncode=1,
            )
        elif fault == "timeout":
            fleet = subprocess.TimeoutExpired(["autoskillit", "fleet", "run"], 600)
        elif fault == "exception":
            fleet = RuntimeError("fleet runner failed")
        runner, _clone = _smoke_runner(tmp_path, monkeypatch, mode=mode, fleet=fleet)
        failures, result, out, _catalog = _run_smoke(tmp_path, runner, monkeypatch)

        assert failures
        assert result["outcome"] == "failed"
        assert (out / "smoke-lifecycle.json").is_file()
        assert (out / "smoke-launched").is_file()
        assert not any(call.argv[:2] == ["gh", "pr", "close"] for call in runner.calls)

    def test_branch_collision_stops_before_launch_marker(self, tmp_path, monkeypatch):
        runner, _clone = _smoke_runner(tmp_path, monkeypatch, mode="branch-collision")
        failures, result, out, _catalog = _run_smoke(tmp_path, runner, monkeypatch)

        assert failures
        assert result["outcome"] == "failed"
        assert (out / "smoke-lifecycle.json").exists() is False
        assert (out / "smoke-launched").exists() is False
        assert not any(call.argv[:3] == ["autoskillit", "fleet", "run"] for call in runner.calls)

    def test_expected_failure_matches_only_the_exact_structured_envelope(
        self, tmp_path, monkeypatch
    ):
        expected = _SMOKE_FAILURE
        runner, _clone = _smoke_runner(
            tmp_path,
            monkeypatch,
            fleet=_completed(
                [],
                json.dumps(
                    {
                        "success": False,
                        "kind": "completed",
                        "dispatch_status": "failed",
                        "error": "workflow_failed",
                        "user_visible_message": "model reported failure",
                    }
                ),
                returncode=1,
            ),
        )
        catalog, test = _smoke_case([expected])
        out = tmp_path / "out"
        failures = harness.run_test(
            test,
            catalog,
            out=out,
            home=tmp_path / "home",
            env=_env(HOME=str(tmp_path / "home")),
            runner=runner,
        )
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))

        assert failures == []
        assert result["passed"] is False
        assert result["outcome"] == "expected_failure"
        assert result["expected_findings"] == [expected]

    @pytest.mark.parametrize(
        ("envelope_overrides", "initial_failures", "expected_failures", "expected_findings"),
        [
            pytest.param(
                {},
                ["cleanup: delete failed"],
                ["cleanup: delete failed"],
                [_SMOKE_FAILURE],
                id="matched-with-extra-errors",
            ),
            pytest.param(
                {"user_visible_message": " "},
                ["fleet run failed"],
                ["fleet run failed"],
                [],
                id="malformed-envelope",
            ),
            pytest.param(
                {"success": True},
                [],
                [
                    "fleet run: remove stale expected failure for fixed bug "
                    f"{_SMOKE_FAILURE['issue']}"
                ],
                [],
                id="stale-expectation",
            ),
        ],
    )
    def test_recipe_matching_preserves_extra_errors_and_rejects_malformed_or_stale_rows(
        self,
        envelope_overrides,
        initial_failures,
        expected_failures,
        expected_findings,
    ) -> None:
        _catalog, test = _smoke_case([_SMOKE_FAILURE])
        matching = {
            "success": False,
            "kind": "completed",
            "dispatch_status": "failed",
            "error": "workflow_failed",
            "user_visible_message": "model reported failure",
            **envelope_overrides,
        }
        failures, findings = harness.match_recipe_failure(test, initial_failures, matching)
        assert failures == expected_failures
        assert findings == expected_findings

    def test_expected_failure_console_outcome_comes_from_result_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        catalog, test = _smoke_case([_SMOKE_FAILURE])

        def record_expected_failure(test_arg, _catalog, *, out, **_kwargs):
            out.mkdir(parents=True, exist_ok=True)
            (out / "result.json").write_text(
                json.dumps(
                    {
                        "test": test_arg.name,
                        "passed": False,
                        "failures": [],
                        "outcome": "expected_failure",
                        "expected_findings": [_SMOKE_FAILURE],
                    }
                ),
                encoding="utf-8",
            )
            return []

        monkeypatch.setattr(harness.e2e_catalog, "load_catalog", lambda _path: catalog)
        monkeypatch.setattr(harness, "run_test", record_expected_failure)
        status = harness._run(test.name, tmp_path / "out", tmp_path / "catalog.json")

        assert status == 0
        assert capsys.readouterr().out.strip() == f"e2e_harness: {test.name} expected_failure"


def _make_bare_remote(tmp_path: Path, branch: str) -> tuple[Path, dict[str, str], str]:
    git_home = tmp_path / "git-home"
    git_home.mkdir()
    env = {
        "HOME": str(git_home),
        "PATH": os.environ.get("PATH", "/usr/bin"),
        "GIT_CONFIG_GLOBAL": str(git_home / "config"),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    (git_home / "config").write_text("", encoding="utf-8")
    remote = tmp_path / "sandbox.git"
    builder = tmp_path / "builder"
    builder.mkdir()
    _run_git(builder, "init", "--bare", "--initial-branch=main", str(remote), env=env)
    clone = builder / "clone"
    _run_git(builder, "init", "--initial-branch=main", str(clone), env=env)
    _run_git(clone, "config", "user.name", "Harness test", env=env)
    _run_git(clone, "config", "user.email", "harness-test@example.invalid", env=env)
    (clone / "README.md").write_text("base\n", encoding="utf-8")
    _run_git(clone, "add", "README.md", env=env)
    _run_git(clone, "commit", "-m", "base", env=env)
    _run_git(clone, "remote", "add", "origin", str(remote), env=env)
    _run_git(clone, "push", "-u", "origin", "main", env=env)

    _run_git(clone, "checkout", "-b", branch, env=env)
    (clone / "smoke.txt").write_text("smoke\n", encoding="utf-8")
    _run_git(clone, "add", "smoke.txt", env=env)
    _run_git(clone, "commit", "-m", "smoke branch", env=env)
    _run_git(clone, "push", "-u", "origin", branch, env=env)

    _run_git(clone, "checkout", "main", env=env)
    _run_git(clone, "checkout", "-b", "feature/unrelated", env=env)
    (clone / "unrelated.txt").write_text("preserve\n", encoding="utf-8")
    _run_git(clone, "add", "unrelated.txt", env=env)
    _run_git(clone, "commit", "-m", "unrelated branch", env=env)
    _run_git(clone, "push", "-u", "origin", "feature/unrelated", env=env)
    unrelated_oid = _bare_ref(remote, "feature/unrelated")
    assert unrelated_oid is not None
    return remote, env, unrelated_oid


def _bare_ref(remote: Path, branch: str) -> str | None:
    result = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "--verify", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _smoke_cleanup_runner(
    remote: Path,
    branch: str,
    repository: str,
    *,
    fault: str | None = None,
    with_pr: bool = True,
) -> tuple[FakeRunner, dict[str, object]]:
    state: dict[str, object] = {"pr_open": True, "unrelated_pr_open": True}
    runner: FakeRunner

    def handle(argv: list[str]):
        if argv[:2] == ["gh", "auth"]:
            return _completed(argv)
        if argv[:2] == ["gh", "api"]:
            method = argv[argv.index("--method") + 1]
            endpoint = argv[-1]
            requested = endpoint.rsplit("/heads/", 1)[-1]
            assert endpoint.startswith(f"/repos/{repository}/git/")
            if method == "GET":
                oid = _bare_ref(remote, requested)
                if oid is None:
                    return _completed(argv, "HTTP/2.0 404 Not Found\r\n\r\n", returncode=1)
                body = json.dumps(
                    {
                        "ref": f"refs/heads/{requested}",
                        "object": {"sha": oid},
                    }
                )
                return _completed(argv, f"HTTP/2.0 200 OK\r\n\r\n{body}")
            assert method == "DELETE"
            assert requested == branch
            if fault == "delete":
                return _completed(
                    argv, "HTTP/2.0 500 Server Error\r\n\r\n", returncode=1, stderr="delete failed"
                )
            result = subprocess.run(
                ["git", "--git-dir", str(remote), "update-ref", "-d", f"refs/heads/{branch}"],
                capture_output=True,
                text=True,
                check=False,
            )
            http_status, returncode = {
                "already-deleted": ("HTTP/2.0 404 Not Found", 1),
            }.get(fault, ("HTTP/2.0 204 No Content", result.returncode))
            return _completed(
                argv,
                f"{http_status}\r\n\r\n",
                returncode=returncode,
                stderr=result.stderr,
            )
        if argv[:3] == ["gh", "pr", "list"]:
            assert argv[argv.index("--repo") + 1] == repository
            assert argv[argv.index("--head") + 1] == branch
            rows = (
                []
                if not with_pr
                else [
                    {
                        "number": 8,
                        "state": "OPEN" if state["pr_open"] else "CLOSED",
                        "headRefName": branch,
                        "headRepository": {"name": repository.split("/", 1)[1]},
                        "headRepositoryOwner": {"login": repository.split("/", 1)[0]},
                    }
                ]
            )
            return _completed(argv, json.dumps(rows))
        if argv[:3] == ["gh", "pr", "close"]:
            assert argv[3:] == ["8", "--repo", repository]
            state["close_attempts"] = int(state.get("close_attempts", 0)) + 1
            if fault == "close":
                return _completed(argv, returncode=1, stderr="close failed")
            state["pr_open"] = False
            return _completed(argv)
        raise AssertionError(f"unexpected cleanup command: {argv!r}")

    runner = FakeRunner(handle)
    return runner, state


def _write_cleanup_inputs(
    out: Path, test: e2e_catalog.CatalogTest, repository: str, *, malformed_result: bool = False
) -> str:
    branch = "e2e-smoke-" + "a" * 32
    out.mkdir(parents=True)
    (out / "smoke-launched").write_text(test.name + "\n", encoding="utf-8")
    (out / "smoke-lifecycle.json").write_text(
        json.dumps(
            {
                "test": test.name,
                "repository": repository,
                "base_branch": "main",
                "branch_name": branch,
            }
        ),
        encoding="utf-8",
    )
    if malformed_result:
        (out / "result.json").write_text("{broken", encoding="utf-8")
    else:
        (out / "result.json").write_text(
            json.dumps(
                {
                    "test": test.name,
                    "passed": False,
                    "failures": [],
                    "outcome": "expected_failure",
                    "expected_findings": [_SMOKE_FAILURE],
                }
            ),
            encoding="utf-8",
        )
    return branch


class TestSmokeCleanup:
    def test_real_bare_remote_cleanup_preserves_unrelated_branch_and_expected_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, test = _smoke_case()
        branch = "e2e-smoke-" + "a" * 32
        remote, git_env, unrelated_oid = _make_bare_remote(tmp_path, branch)
        out = tmp_path / "out"
        _write_cleanup_inputs(out, test, catalog.sandbox_repository)
        runner, state = _smoke_cleanup_runner(remote, branch, catalog.sandbox_repository)
        env = _env(HOME=git_env["HOME"], PATH=git_env["PATH"], E2E_SANDBOX_TOKEN=SANDBOX_TOKEN)
        sleeps: list[float] = []
        monkeypatch.setattr(harness.time, "sleep", sleeps.append)

        status = harness.cleanup_smoke_test(catalog, test.name, runner, out, env)
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        evidence = json.loads((out / "smoke-cleanup.json").read_text(encoding="utf-8"))

        assert status == 0
        assert result["outcome"] == "expected_failure"
        assert result["expected_findings"] == [_SMOKE_FAILURE]
        assert result["failures"] == []
        assert _bare_ref(remote, branch) is None
        assert _bare_ref(remote, "feature/unrelated") == unrelated_oid
        assert state["pr_open"] is False and state["unrelated_pr_open"] is True
        assert sleeps == [1, 1]
        assert evidence["final_ref_status"] == 404
        assert evidence["final_pull_requests"][0]["state"] == "CLOSED"
        assert [call.input for call in runner.calls if call.input] == [SANDBOX_TOKEN]
        assert all(
            "E2E_SANDBOX_TOKEN" not in call.env for call in runner.calls if call.env is not None
        )
        close_calls = [
            call.argv for call in runner.calls if call.argv[:3] == ["gh", "pr", "close"]
        ]
        assert close_calls == [["gh", "pr", "close", "8", "--repo", catalog.sandbox_repository]]

    @pytest.mark.parametrize("fault", [None, "already-deleted"])
    def test_cleanup_deletes_a_pushed_branch_even_without_a_pull_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str | None
    ) -> None:
        catalog, test = _smoke_case()
        branch = "e2e-smoke-" + "a" * 32
        remote, git_env, unrelated_oid = _make_bare_remote(tmp_path, branch)
        out = tmp_path / "out"
        _write_cleanup_inputs(out, test, catalog.sandbox_repository)
        runner, _state = _smoke_cleanup_runner(
            remote, branch, catalog.sandbox_repository, with_pr=False, fault=fault
        )
        env = _env(HOME=git_env["HOME"], PATH=git_env["PATH"], E2E_SANDBOX_TOKEN=SANDBOX_TOKEN)
        sleeps: list[float] = []
        monkeypatch.setattr(harness.time, "sleep", sleeps.append)

        assert harness.cleanup_smoke_test(catalog, test.name, runner, out, env) == 0

        evidence = json.loads((out / "smoke-cleanup.json").read_text(encoding="utf-8"))
        assert _bare_ref(remote, branch) is None
        assert _bare_ref(remote, "feature/unrelated") == unrelated_oid
        assert evidence["final_pull_requests"] == []
        assert evidence["final_ref_status"] == 404
        assert sleeps == [1]
        assert not any(call.argv[:3] == ["gh", "pr", "close"] for call in runner.calls)

    @pytest.mark.parametrize("fault", ["close", "delete"])
    def test_cleanup_errors_fail_and_attempt_the_other_resource_operation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
    ) -> None:
        catalog, test = _smoke_case()
        branch = "e2e-smoke-" + "a" * 32
        remote, git_env, _unrelated_oid = _make_bare_remote(tmp_path, branch)
        out = tmp_path / "out"
        _write_cleanup_inputs(out, test, catalog.sandbox_repository)
        runner, state = _smoke_cleanup_runner(
            remote, branch, catalog.sandbox_repository, fault=fault
        )
        env = _env(HOME=git_env["HOME"], PATH=git_env["PATH"], E2E_SANDBOX_TOKEN=SANDBOX_TOKEN)
        monkeypatch.setattr(harness.time, "sleep", lambda _duration: None)

        status = harness.cleanup_smoke_test(catalog, test.name, runner, out, env)
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        close_called = any(call.argv[:3] == ["gh", "pr", "close"] for call in runner.calls)
        delete_called = any(
            call.argv[:2] == ["gh", "api"] and "DELETE" in call.argv for call in runner.calls
        )

        assert status == 1
        assert result["outcome"] == "failed"
        assert result["expected_findings"] == [_SMOKE_FAILURE]
        assert close_called and delete_called
        assert state["pr_open"] is (fault == "close")
        assert (_bare_ref(remote, branch) is not None) is (fault == "delete")

    def test_unexpected_cleanup_exception_preserves_type_and_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog, test = _smoke_case()
        out = tmp_path / "out"
        _write_cleanup_inputs(out, test, catalog.sandbox_repository)
        runner = FakeRunner(lambda argv: _completed(argv))

        def fail_cleanup(*_args: object) -> None:
            raise RuntimeError("unexpected cleanup fault")

        monkeypatch.setattr(harness, "_cleanup_smoke_resources", fail_cleanup)

        assert harness.cleanup_smoke_test(catalog, test.name, runner, out, _env()) == 1
        evidence = json.loads((out / "smoke-cleanup.json").read_text(encoding="utf-8"))
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        failure = "cleanup error: RuntimeError: unexpected cleanup fault"
        assert evidence["failures"] == [failure]
        assert "Traceback (most recent call last)" in evidence["exception"]
        assert "fail_cleanup" in evidence["exception"]
        assert "RuntimeError: unexpected cleanup fault" in evidence["exception"]
        assert result["failures"] == [failure]
        assert result["outcome"] == "failed"

    def test_missing_marker_is_a_noop_but_bad_descriptor_and_result_are_saved(
        self, tmp_path: Path
    ) -> None:
        catalog, test = _smoke_case()
        runner = FakeRunner(lambda argv: _completed(argv))
        out = tmp_path / "not-launched"
        assert harness.cleanup_smoke_test(catalog, test.name, runner, out, _env()) == 0
        assert runner.calls == []
        assert not out.exists()

        bad = tmp_path / "bad-evidence"
        bad.mkdir()
        (bad / "smoke-launched").write_text(test.name + "\n", encoding="utf-8")
        (bad / "smoke-lifecycle.json").write_text("{broken", encoding="utf-8")
        (bad / "result.json").write_text("{also broken", encoding="utf-8")
        assert harness.cleanup_smoke_test(catalog, test.name, runner, bad, _env()) == 1
        assert (bad / "result-before-cleanup.json").read_text(encoding="utf-8") == "{also broken"
        result = json.loads((bad / "result.json").read_text(encoding="utf-8"))
        evidence = json.loads((bad / "smoke-cleanup.json").read_text(encoding="utf-8"))
        assert result["outcome"] == "failed"
        assert len(evidence["failures"]) == 2
        assert runner.calls == []


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
        assert harness.check_envelope(0, '{"success": true}\n') == ([], {"success": True})

    def test_envelope_is_the_last_line_after_plain_text(self) -> None:
        stdout = f'{_SKILL_NOTICE}\n{{"success": true}}\n\n'
        assert harness.last_line(stdout) == '{"success": true}'
        assert harness.check_envelope(0, stdout) == ([], {"success": True})

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
        failures, envelope = harness.check_envelope(returncode, stdout)
        assert failures
        if stdout in ('{"success": true}', '{"success": false}'):
            assert envelope is not None
        else:
            assert envelope is None


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
        assert result == {
            "test": "impl",
            "passed": True,
            "failures": [],
            "outcome": "passed",
            "expected_findings": [],
        }
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
        assert result == {
            "test": "impl",
            "passed": False,
            "failures": failures,
            "outcome": "failed",
            "expected_findings": [],
        }
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
        assert result == {
            "test": "impl",
            "passed": False,
            "failures": failures,
            "outcome": "failed",
            "expected_findings": [],
        }
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
        ("doctor_stdout", "expected_failure"),
        [
            ("not json", "doctor: invalid JSON:"),
            ("[]", "doctor: report must be an object"),
            ("{}", "doctor: results must be a non-empty list"),
            ('{"results": {}}', "doctor: results must be a non-empty list"),
            ('{"results": []}', "doctor: results must be a non-empty list"),
            ('{"results": [null]}', "doctor: invalid result at index 0"),
            (
                '{"results": [{"severity": "fatal", "check": "x", "message": "y"}]}',
                "doctor: invalid result at index 0",
            ),
            (
                '{"results": [{"severity": "warning", "check": "x", "message": 3}]}',
                "doctor: invalid result at index 0",
            ),
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
    def test_invalid_doctor_output_fails(
        self, tmp_path, monkeypatch, doctor_stdout, expected_failure
    ):
        runner, _ = _clean_install_runner(doctor_stdout=doctor_stdout)
        failures, result, _out, _catalog = _run_clean_install(tmp_path, runner, monkeypatch)

        assert len(failures) == 1
        assert failures[0].startswith(expected_failure)
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
        (source / "smoke-lifecycle.json").write_text(
            f'{{"credential":"{MINIMAX_KEY}","token":"{SANDBOX_TOKEN}"}}\n',
            encoding="utf-8",
        )
        dest = tmp_path / "upload"

        harness.redact_tree([source, tmp_path / "missing"], dest, [MINIMAX_KEY, SANDBOX_TOKEN, ""])

        assert (dest / "out" / "result.json").read_text(encoding="utf-8") == "key=[REDACTED]\n"
        assert (dest / "out" / "smoke-lifecycle.json").read_text(encoding="utf-8") == (
            '{"credential":"[REDACTED]","token":"[REDACTED]"}\n'
        )
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
