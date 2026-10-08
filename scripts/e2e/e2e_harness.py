#!/usr/bin/env python3
"""Run one catalog E2E test inside the AutoSkillit user image, and redact its artifacts."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

import e2e_catalog
import e2e_sessions

MINIMAX_BASE_URL = "https://api.minimax.io/anthropic"
MINIMAX_MODEL = "MiniMax-M3[1m]"
MINIMAX_COMPACT_WINDOW_TOKENS = "1000000"
# implement runs four concurrent children and resolve-review three to six; a spawn past the
# limit fails without retry.
MAX_CONCURRENT_SUBAGENTS = "6"
CANARY_TOKEN = "AUTOSKILLIT-E2E-CANARY-OK"
CANARY_PROMPT = f"Reply with exactly {CANARY_TOKEN} and nothing else."
GIT_AUTHOR = ("AutoSkillit E2E", "autoskillit-e2e@users.noreply.github.com")
SETUP_COMMAND_TIMEOUT_SEC = 120
SECRET_ENV = ("MINIMAX_API_KEY", "E2E_SANDBOX_TOKEN")
REDACTED = b"[REDACTED]"
CONFIG_TEMPLATE = Path(__file__).with_name("autoskillit-config.yaml")
SANDBOX_CLONE = Path("/workspace/sandbox")

_SCRUBBED_ENV = frozenset({"CLAUDE_CODE_OAUTH_TOKEN", *SECRET_ENV})
_STDERR_TAIL_CHARS = 2000
CLEAN_INSTALL_ALLOWED_WARNINGS: dict[tuple[str, str], str] = {
    (
        "pytest_temp_capacity",
        "/dev/shm: 67108864 bytes free of 67108864 total; 0 pytest generations under "
        "/dev/shm/autoskillit-pytest-1000 (count unavailable: [Errno 2] No such file or "
        "directory: '/dev/shm/autoskillit-pytest-1000') (below 2000000000-byte threshold); "
        "run: task cleanup-shm",
    ): "Docker's default shared-memory allocation is sufficient for this scenario, which "
    "runs no pytest and creates no pytest generations.",
}

Runner = Callable[..., subprocess.CompletedProcess[str]]
CallResult = tuple[subprocess.CompletedProcess[str] | None, str | None]


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=env,
        input=input,
        timeout=timeout,
        capture_output=True,
        text=True,
        check=False,
    )


def _call(
    runner: Runner, argv: Sequence[str], *, what: str, evidence: Path | None = None, **kwargs: Any
) -> CallResult:
    """Run *argv*, turning a nonzero exit, a timeout or a launch error into a failure string."""
    result = None
    failure: str | None
    stdout: str | bytes | None
    stderr: str | bytes | None
    try:
        result = runner(argv, **kwargs)
    except subprocess.TimeoutExpired as exc:
        failure = f"{what}: timed out after {exc.timeout}s"
        stdout, stderr, outcome = exc.stdout, exc.stderr, "timeout"
    except OSError as exc:
        failure = f"{what}: {exc}"
        stdout, stderr, outcome = "", str(exc), "launch_error"
    else:
        stdout, stderr, outcome = result.stdout, result.stderr, "completed"
        failure = None
        if result.returncode != 0:
            detail = (result.stderr or "").strip()[-_STDERR_TAIL_CHARS:]
            failure = f"{what}: exit {result.returncode}: {detail}"
    if evidence is not None:
        for stream, value in (("stdout", stdout), ("stderr", stderr)):
            data = value if isinstance(value, bytes) else (value or "").encode("utf-8")
            evidence.with_suffix(f".{stream}.txt").write_bytes(data)
        record = {
            "argv": list(argv),
            "outcome": outcome,
            "returncode": result.returncode if result is not None else None,
            "failure": failure,
        }
        evidence.with_suffix(".command.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
    return result, failure


def child_env(env: Mapping[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in env.items()
        if name not in _SCRUBBED_ENV and not name.startswith("ANTHROPIC_")
    }


def claude_settings_env(api_key: str) -> dict[str, str]:
    return {
        "ANTHROPIC_BASE_URL": MINIMAX_BASE_URL,
        "ANTHROPIC_AUTH_TOKEN": api_key,
        "ANTHROPIC_MODEL": MINIMAX_MODEL,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": MINIMAX_MODEL,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": MINIMAX_MODEL,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": MINIMAX_MODEL,
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": MINIMAX_COMPACT_WINDOW_TOKENS,
        "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": MAX_CONCURRENT_SUBAGENTS,
    }


def write_claude_settings(home: Path, api_key: str) -> Path:
    path = home / ".claude" / "settings.json"
    settings: dict[str, Any] = {}
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(existing, dict):
            settings = existing
    settings["env"] = claude_settings_env(api_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)
    return path


def install_autoskillit_config(home: Path) -> Path:
    target = home / ".autoskillit" / "config.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CONFIG_TEMPLATE, target)
    return target


def check_canary_output(stdout: str) -> list[str]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return ["canary: claude output is not JSON"]
    if not isinstance(payload, dict):
        return ["canary: claude output is not a JSON object"]
    if payload.get("is_error") is not False:
        return [f"canary: claude reported an error: {str(payload.get('result'))[:500]}"]
    reply = payload.get("result")
    if not isinstance(reply, str) or CANARY_TOKEN not in reply:
        return [f"canary: reply does not contain {CANARY_TOKEN}"]
    return []


def run_canary(
    test: e2e_catalog.CatalogTest, out: Path, env: Mapping[str, str], runner: Runner
) -> list[str]:
    workdir = Path(tempfile.mkdtemp(prefix="e2e-canary-"))
    result, failure = _call(
        runner,
        ["claude", "-p", CANARY_PROMPT, "--output-format", "json"],
        what="claude -p",
        cwd=workdir,
        env=env,
        timeout=test.timeout_sec,
    )
    if result is not None:
        (out / "canary.json").write_text(result.stdout, encoding="utf-8")
        (out / "canary.stderr.log").write_text(result.stderr, encoding="utf-8")
    if failure is not None or result is None:
        return [str(failure)]
    return check_canary_output(result.stdout)


def _setup_call(
    runner: Runner, argv: Sequence[str], env: Mapping[str, str], **kw: Any
) -> CallResult:
    return _call(
        runner, argv, what=" ".join(argv[:3]), env=env, timeout=SETUP_COMMAND_TIMEOUT_SEC, **kw
    )


def configure_git(env: Mapping[str, str], runner: Runner) -> str | None:
    for key, value in (("user.name", GIT_AUTHOR[0]), ("user.email", GIT_AUTHOR[1])):
        _, failure = _setup_call(runner, ["git", "config", "--global", key, value], env)
        if failure is not None:
            return failure
    return None


def authenticate_gh(token: str, env: Mapping[str, str], runner: Runner) -> str | None:
    login = ["gh", "auth", "login", "--hostname", "github.com", "--with-token"]
    _, failure = _setup_call(runner, login, env, input=token)
    if failure is not None:
        return failure
    _, failure = _setup_call(runner, ["gh", "auth", "setup-git"], env)
    return failure


def clone_sandbox(repository: str, env: Mapping[str, str], runner: Runner) -> str | None:
    _, failure = _setup_call(runner, ["gh", "repo", "clone", repository, str(SANDBOX_CLONE)], env)
    return failure


def _list_pull_requests(
    repository: str,
    limit: int,
    fields: str,
    env: Mapping[str, str],
    runner: Runner,
    *,
    head: str | None = None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    argv = ["gh", "pr", "list", "--repo", repository, "--state", "all"]
    argv += ["--limit", str(limit), "--json", fields]
    if head is not None:
        argv += ["--head", head]
    result, failure = _setup_call(runner, argv, env)
    if failure is not None or result is None:
        return None, failure
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "gh pr list: output is not JSON"
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return None, "gh pr list: output is not a list of objects"
    if any(type(row.get("number")) is not int or row["number"] <= 0 for row in rows):
        return None, "gh pr list: pull request number is missing or invalid"
    return rows, None


def latest_pull_request_number(
    repository: str, env: Mapping[str, str], runner: Runner
) -> tuple[int | None, str | None]:
    rows, failure = _list_pull_requests(repository, 1, "number", env, runner)
    if rows is None:
        return None, failure
    return (int(rows[0]["number"]) if rows else 0), None


def new_pull_requests(
    repository: str,
    baseline: int,
    env: Mapping[str, str],
    runner: Runner,
    *,
    pipeline: bool = False,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    limit = 20
    fields = "number,state,additions,deletions"
    if pipeline:
        fields += (
            ",body,headRefOid,headRefName,headRepository,baseRefName,autoMergeRequest,mergedAt"
        )
    while True:
        rows, failure = _list_pull_requests(repository, limit, fields, env, runner)
        if rows is None:
            return None, failure
        if not pipeline or len(rows) < limit or any(row["number"] <= baseline for row in rows):
            return [row for row in rows if row["number"] > baseline], None
        limit *= 2


def fleet_run_argv(
    test: e2e_catalog.CatalogTest, *, ingredients: Mapping[str, str] | None = None
) -> list[str]:
    values = dict(test.ingredients)
    values.update(ingredients or {})
    arguments = [arg for key, value in values.items() for arg in ("-i", f"{key}={value}")]
    return [
        "autoskillit",
        "fleet",
        "run",
        str(test.recipe),
        *arguments,
        "--disable-quota-guard",
        "--timeout-sec",
        str(test.timeout_sec),
    ]


def last_line(stdout: str) -> str:
    lines = [line for line in stdout.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def check_envelope(returncode: int, stdout: str) -> tuple[list[str], dict[str, Any] | None]:
    failures = [] if returncode == 0 else [f"fleet run: exit {returncode}"]
    line = last_line(stdout)
    try:
        envelope = json.loads(line)
    except json.JSONDecodeError:
        return [*failures, "fleet run: the last stdout line is not a JSON envelope"], None
    if not isinstance(envelope, dict):
        failures.append("fleet run: the envelope is not a JSON object")
        return failures, None
    elif envelope.get("success") is not True:
        failures.append("fleet run: the envelope does not report success")
    return failures, envelope


def check_implementation_envelope(
    envelope: Mapping[str, Any],
) -> tuple[list[str], list[dict[str, str]]]:
    failures: list[str] = []
    findings: list[dict[str, str]] = []
    payload = envelope.get("l3_payload")
    reason = payload.get("reason") if isinstance(payload, dict) else None
    status = envelope.get("dispatch_status")
    if envelope.get("success") is not True or status != "success":
        stable_reason = envelope.get("reason") or reason
        if isinstance(stable_reason, str) and stable_reason:
            findings.append({"check": "fleet_envelope", "message": f"{status}: {stable_reason}"})
        else:
            failures.append("fleet run: unsuccessful envelope has no specific failure reason")
    elif reason != "implementation_complete":
        if isinstance(reason, str) and reason:
            findings.append({"check": "implementation_terminal", "message": reason})
        else:
            failures.append("fleet run: implementation terminal reason is missing")
    if not all(
        isinstance(envelope.get(key), str) and envelope[key]
        for key in ("dispatch_id", "dispatched_session_id")
    ):
        failures.append("fleet run: dispatch/session identity is missing")
    return failures, findings


def run_fleet(
    test: e2e_catalog.CatalogTest,
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
    *,
    ingredients: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any] | None, list[str], list[dict[str, str]]]:
    result, failure = _call(
        runner,
        fleet_run_argv(test, ingredients=ingredients),
        what="fleet run",
        evidence=out / "fleet-run",
        cwd=SANDBOX_CLONE,
        env=env,
        timeout=test.timeout_sec + e2e_catalog.HARNESS_GRACE_SEC,
    )
    stdout = (out / "fleet-run.stdout.txt").read_text(encoding="utf-8", errors="replace")
    shutil.copyfile(out / "fleet-run.stdout.txt", out / "fleet-run.stdout.log")
    shutil.copyfile(out / "fleet-run.stderr.txt", out / "fleet-run.stderr.log")
    line = last_line(stdout)
    (out / "envelope.json").write_text(line + "\n", encoding="utf-8")
    if not test.pipeline:
        if result is None:
            return None, [str(failure)], []
        envelope_failures, envelope = check_envelope(result.returncode, stdout)
        return envelope, envelope_failures, []
    failures: list[str] = []
    findings: list[dict[str, str]] = []
    try:
        envelope = json.loads(line)
    except json.JSONDecodeError:
        envelope = None
    if not isinstance(envelope, dict):
        envelope = None
        failures.append("fleet run: the last stdout line is not a JSON object envelope")
    if result is None:
        failures.append(str(failure))
    elif envelope is not None and test.pipeline:
        envelope_failures, findings = check_implementation_envelope(envelope)
        failures += envelope_failures
        if failure and not findings:
            failures.append(failure)
    return envelope, failures, findings


def match_recipe_failure(
    test: e2e_catalog.CatalogTest,
    failures: list[str],
    envelope: Mapping[str, Any] | None,
) -> tuple[list[str], list[dict[str, str]]]:
    if envelope is None:
        return failures, []
    if envelope.get("success") is True:
        stale = [
            f"fleet run: remove stale expected failure for fixed bug {row['issue']}"
            for row in test.expected_failures
        ]
        return [*failures, *stale], []
    valid = (
        envelope.get("success") is False
        and envelope.get("kind") in ("completed", "rejected")
        and (
            envelope.get("kind") == "rejected"
            or isinstance(envelope.get("dispatch_status"), str)
            and bool(envelope["dispatch_status"].strip())
        )
        and all(
            isinstance(envelope.get(k), str) and envelope[k].strip()
            for k in ("error", "user_visible_message")
        )
    )
    if not valid:
        return failures, []
    matched = [
        dict(row)
        for row in test.expected_failures
        if row["check"] == envelope["error"] and row["message"] == envelope["user_visible_message"]
    ]
    if not matched:
        return failures, []
    consumed = {
        "fleet run: the envelope does not report success",
        *(f"fleet run: exit {code}" for code in (1, 2, 3)),
    }
    return [failure for failure in failures if failure not in consumed], matched


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    stream = tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False)
    staged = Path(stream.name)
    try:
        with stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def _validate_smoke_descriptor(
    descriptor: Any, test: e2e_catalog.CatalogTest, repository: str
) -> dict[str, str]:
    if not isinstance(descriptor, dict) or descriptor.get("test") != test.name:
        raise ValueError("invalid smoke lifecycle test")
    if descriptor.get("repository") != repository:
        raise ValueError("invalid smoke lifecycle repository")
    if not re.fullmatch(r"e2e-smoke-[0-9a-f]{32}", str(descriptor.get("branch_name", ""))):
        raise ValueError("invalid smoke lifecycle branch")
    base = descriptor.get("base_branch")
    if (
        not isinstance(base, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", base)
        or ".." in base
    ):
        raise ValueError("invalid smoke lifecycle base branch")
    if base == descriptor["branch_name"]:
        raise ValueError("smoke lifecycle branch equals base")
    return {key: descriptor[key] for key in ("test", "repository", "branch_name", "base_branch")}


def prepare_smoke(
    test: e2e_catalog.CatalogTest,
    repository: str,
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> tuple[list[str], dict[str, str] | None]:
    if test.recipe is None or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", test.recipe):
        return ["smoke setup: recipe must be a basename"], None
    source = Path(__file__).with_name("recipes") / str(test.recipe_fixture)
    target = SANDBOX_CLONE / ".autoskillit" / "recipes" / f"{test.recipe}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    runtime = {
        "source_dir": str(SANDBOX_CLONE),
        "repository": repository,
        "branch_name": f"e2e-smoke-{uuid.uuid4().hex}",
    }
    for key, argv in (
        ("remote_url", ["git", "remote", "get-url", "origin"]),
        ("base_branch", ["git", "branch", "--show-current"]),
    ):
        result, failure = _setup_call(runner, argv, env, cwd=SANDBOX_CLONE)
        if failure or result is None or not result.stdout.strip():
            return [failure or f"smoke setup: empty {key}"], None
        runtime[key] = result.stdout.strip()
    result, failure = _setup_call(
        runner,
        ["git", "ls-remote", "--exit-code", "origin", f"refs/heads/{runtime['branch_name']}"],
        env,
        cwd=SANDBOX_CLONE,
    )
    if result is None or result.returncode != 2:
        return [failure or "smoke setup: assigned remote branch already exists"], None
    descriptor = {key: runtime[key] for key in ("repository", "base_branch", "branch_name")}
    descriptor["test"] = test.name
    _validate_smoke_descriptor(descriptor, test, repository)
    _atomic_json(out / "smoke-lifecycle.json", descriptor)
    return [], runtime


def run_smoke_recipe(
    test: e2e_catalog.CatalogTest,
    repository: str,
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> tuple[list[str], list[dict[str, str]]]:
    failures, runtime = prepare_smoke(test, repository, out, env, runner)
    if runtime is None:
        return failures, []
    (out / "smoke-launched").write_text(test.name + "\n", encoding="utf-8")
    envelope, fleet_failures, _ = run_fleet(test, out, env, runner, ingredients=runtime)
    fleet_failures, matched = match_recipe_failure(test, fleet_failures, envelope)
    failures += fleet_failures
    if (
        envelope is not None
        and envelope.get("success") is True
        and (envelope.get("kind") != "completed" or envelope.get("dispatch_status") != "success")
    ):
        failures.append("smoke: invalid successful fleet envelope")
    if not matched and envelope is not None and envelope.get("success") is True:
        failures += verify_smoke(test, runtime, out, env, runner)
    return failures, matched


def _smoke_pull_requests(
    descriptor: Mapping[str, str], env: Mapping[str, str], runner: Runner
) -> tuple[list[dict[str, Any]] | None, str | None]:
    return _list_pull_requests(
        descriptor["repository"],
        100,
        "number,state,headRefName,headRefOid,baseRefOid,additions,deletions,headRepository,headRepositoryOwner",
        env,
        runner,
        head=descriptor["branch_name"],
    )


def _smoke_pr_matches(pr: Mapping[str, Any], descriptor: Mapping[str, str]) -> bool:
    owner, repository = pr.get("headRepositoryOwner"), pr.get("headRepository")
    identity = (
        f"{owner.get('login')}/{repository.get('name')}"
        if isinstance(owner, dict) and isinstance(repository, dict)
        else ""
    )
    return (
        pr.get("headRefName") == descriptor["branch_name"]
        and identity.lower() == descriptor["repository"].lower()
    )


def _ref_request(
    descriptor: Mapping[str, str],
    branch: str,
    env: Mapping[str, str],
    runner: Runner,
    *,
    method: str = "GET",
) -> tuple[int | None, dict[str, Any] | None, str | None]:
    plural = "refs" if method == "DELETE" else "ref"
    endpoint = f"/repos/{descriptor['repository']}/git/{plural}/heads/{branch}"
    result, failure = _setup_call(
        runner, ["gh", "api", "--include", "--method", method, endpoint], env
    )
    if result is None:
        return None, None, failure
    header, _, body = result.stdout.replace("\r\n", "\n").partition("\n\n")
    match = re.search(r"^HTTP/\S+ (\d{3})\b", header)
    status = int(match[1]) if match else None
    if method == "DELETE" and status == 204 and not failure:
        return status, None, None
    if method in ("GET", "DELETE") and status == 404:
        return status, None, None
    if status != 200 or failure:
        return status, None, failure or f"gh api: unexpected HTTP status {status}"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return status, None, "gh api: invalid ref JSON"
    if (
        not isinstance(payload, dict)
        or payload.get("ref") != f"refs/heads/{branch}"
        or not isinstance(payload.get("object"), dict)
        or not re.fullmatch(
            r"(?:[0-9a-f]{40}|[0-9a-f]{64})", str(payload["object"].get("sha", ""))
        )
    ):
        return status, None, "gh api: ref or commit OID does not match the request"
    return status, payload, None


def _canary_sources_valid(code: str, test_code: str) -> bool:
    try:
        tree = ast.parse(test_code)
        ast.parse(code)
    except SyntaxError:
        return False
    imports = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "sandbox.text"
        and any(alias.name == "smoke_canary" for alias in node.names)
        for node in ast.walk(tree)
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        is_case = any(
            isinstance(base, ast.Attribute)
            and base.attr == "TestCase"
            or isinstance(base, ast.Name)
            and base.id == "TestCase"
            for base in node.bases
        )
        for method in node.body:
            if (
                not is_case
                or not isinstance(method, ast.FunctionDef)
                or not method.name.startswith("test")
            ):
                continue
            for call in ast.walk(method):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr.startswith("assert")
                    and any(
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == "smoke_canary"
                        for inner in ast.walk(call)
                    )
                ):
                    return imports
    return False


def _verify_smoke_commit(
    head: str, base: str, out: Path, env: Mapping[str, str], runner: Runner
) -> tuple[list[str], str | None]:
    results: dict[str, str] = {}
    for name, args in (
        ("fetch", ["fetch", "--no-tags", "origin", head, base]),
        ("type", ["cat-file", "-t", head]),
        ("base", ["merge-base", base, head]),
    ):
        result, failure = _setup_call(runner, ["git", *args], env, cwd=SANDBOX_CLONE)
        if failure or result is None:
            return [failure or f"smoke: missing {name} result"], None
        results[name] = result.stdout.strip()
    if results["type"] != "commit" or not re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}", results["base"]
    ):
        return ["smoke: fetched immutable commit or comparison base is invalid"], None
    result, failure = _setup_call(
        runner,
        ["git", "diff", "--name-only", results["base"], head, "--"],
        env,
        cwd=SANDBOX_CLONE,
    )
    paths = ("sandbox/text.py", "tests/test_smoke_canary.py")
    if failure or result is None:
        return [failure or "smoke: missing complete diff"], None
    if set(result.stdout.splitlines()) != set(paths):
        return [
            "smoke: complete PR diff must change only "
            "sandbox/text.py and tests/test_smoke_canary.py"
        ], None
    sources = []
    for path in paths:
        result, failure = _setup_call(
            runner, ["git", "show", f"{head}:{path}"], env, cwd=SANDBOX_CLONE
        )
        if failure or result is None:
            return [failure or f"smoke: missing {path}"], None
        sources.append(result.stdout)
    if not _canary_sources_valid(*sources):
        return ["smoke: missing executable unittest canary assertion"], None
    with tempfile.TemporaryDirectory(dir=out) as directory:
        root = Path(directory)
        for path, source in zip(paths, sources, strict=True):
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
        script = (
            "import unittest; from sandbox.text import smoke_canary; "
            "assert smoke_canary() is True; "
            "suite=unittest.defaultTestLoader.discover('tests'); "
            "assert suite.countTestCases()>0; "
            "result=unittest.TextTestRunner().run(suite); "
            "raise SystemExit(0 if result.wasSuccessful() else 1)"
        )
        _, failure = _setup_call(
            runner, [sys.executable, "-c", script], env, cwd=root, evidence=out / "smoke-canary"
        )
    return ([failure] if failure else []), results["base"]


def verify_smoke(
    test: e2e_catalog.CatalogTest,
    runtime: Mapping[str, str],
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> list[str]:
    rows, failure = _smoke_pull_requests(runtime, env, runner)
    if rows is None:
        return [str(failure)]
    failures = check_pull_requests(rows, test.expected_pull_request_state)
    if failures:
        return failures
    pr = rows[0]
    if not _smoke_pr_matches(pr, runtime):
        return ["smoke: PR head branch or repository does not match the sandbox"]
    head, base = pr.get("headRefOid"), pr.get("baseRefOid")
    if not isinstance(head, str) or not isinstance(base, str):
        return ["smoke: invalid PR commit OIDs"]
    if not all(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid) for oid in (head, base)):
        return ["smoke: invalid PR commit OIDs"]
    status, ref, failure = _ref_request(runtime, runtime["branch_name"], env, runner)
    if failure or status != 200 or ref is None or ref["object"]["sha"] != head:
        return [failure or "smoke: remote branch missing or does not match the PR head"]
    failures, comparison_base = _verify_smoke_commit(head, base, out, env, runner)
    latest, failure = _smoke_pull_requests(runtime, env, runner)
    if failure or latest is None or len(latest) != 1 or latest[0].get("headRefOid") != head:
        failures.append(failure or "smoke: PR head changed during verification")
    _atomic_json(
        out / "smoke-evidence.json",
        {
            "number": pr["number"],
            "branch_name": runtime["branch_name"],
            "head_commit": head,
            "comparison_base": comparison_base,
            "pull_request": pr,
            "pre_cleanup_ref": ref,
            "canary_verified": not failures,
        },
    )
    return failures


def _close_smoke_pull_requests(
    descriptor: Mapping[str, str], env: Mapping[str, str], runner: Runner
) -> list[str]:
    failures: list[str] = []
    rows, failure = _smoke_pull_requests(descriptor, env, runner)
    if failure:
        failures.append(failure)
    for pr in rows or []:
        if not _smoke_pr_matches(pr, descriptor):
            failures.append("cleanup: PR does not match assigned branch")
            continue
        if str(pr.get("state", "")).lower() == "open":
            time.sleep(1)
            _, failure = _setup_call(
                runner,
                ["gh", "pr", "close", str(pr["number"]), "--repo", descriptor["repository"]],
                env,
            )
            if failure:
                failures.append(failure)
    return failures


def _cleanup_smoke_resources(
    descriptor: Mapping[str, str], env: Mapping[str, str], runner: Runner
) -> tuple[list[str], dict[str, Any]]:
    failures: list[str] = []
    base_status, _, failure = _ref_request(descriptor, descriptor["base_branch"], env, runner)
    access_verified = base_status == 200 and failure is None
    if not access_verified:
        failures.append(failure or "cleanup: base ref read access could not be verified")
    failures += _close_smoke_pull_requests(descriptor, env, runner)
    status, ref, failure = _ref_request(descriptor, descriptor["branch_name"], env, runner)
    if failure:
        failures.append(failure)
    elif status == 200:
        time.sleep(1)
        _, _, failure = _ref_request(
            descriptor, descriptor["branch_name"], env, runner, method="DELETE"
        )
        if failure:
            failures.append(failure)
    elif status != 404 or not access_verified:
        failures.append("cleanup: assigned ref absence could not be verified")
    final_rows, failure = _smoke_pull_requests(descriptor, env, runner)
    if failure:
        failures.append(failure)
    elif final_rows is None or any(
        not _smoke_pr_matches(pr, descriptor)
        or str(pr.get("state", "")).lower() not in ("closed", "merged")
        for pr in final_rows
    ):
        failures.append("cleanup: assigned branch still has an open or invalid PR")
    final_status, _, failure = _ref_request(descriptor, descriptor["branch_name"], env, runner)
    if failure:
        failures.append(failure)
    elif final_status != 404 or not access_verified:
        failures.append("cleanup: assigned remote ref is not verified absent")
    return failures, {
        "branch_name": descriptor["branch_name"],
        "pre_cleanup_ref": ref,
        "final_pull_requests": final_rows,
        "final_ref_status": final_status,
        "access_verified": access_verified,
        "failures": failures,
    }


def _model_result(out: Path, test_name: str) -> tuple[dict[str, Any], list[str]]:
    path = out / "result.json"
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or payload.get("test") != test_name
            or not isinstance(payload.get("passed"), bool)
            or not isinstance(payload.get("failures"), list)
            or not all(isinstance(failure, str) for failure in payload["failures"])
            or payload.get("outcome") not in ("passed", "expected_failure", "failed")
            or not isinstance(payload.get("expected_findings"), list)
        ):
            raise ValueError("invalid model result fields")
        if payload["passed"] != (payload["outcome"] == "passed"):
            raise ValueError("model result passed/outcome disagree")
        if bool(payload["failures"]) != (payload["outcome"] == "failed"):
            raise ValueError("model result failures/outcome disagree")
        if payload["outcome"] == "expected_failure" and not payload["expected_findings"]:
            raise ValueError("model result has no matched expected finding")
        return payload, []
    except (OSError, ValueError) as exc:
        if path.is_file():
            shutil.copyfile(path, out / "result-before-cleanup.json")
        result = {
            "test": test_name,
            "passed": False,
            "outcome": "failed",
            "failures": [],
            "expected_findings": [],
        }
        return result, [f"cleanup: missing or malformed model result: {exc}"]


def cleanup_smoke_test(
    catalog: e2e_catalog.Catalog,
    test_name: str,
    runner: Runner,
    out: Path,
    env: Mapping[str, str],
) -> int:
    test = catalog.get(test_name)
    if test.recipe_fixture is None or not (out / "smoke-launched").exists():
        return 0
    out.mkdir(parents=True, exist_ok=True)
    result, failures = _model_result(out, test_name)
    evidence: dict[str, Any] = {}
    try:
        descriptor = _validate_smoke_descriptor(
            json.loads((out / "smoke-lifecycle.json").read_text(encoding="utf-8")),
            test,
            catalog.sandbox_repository,
        )
        token = env.get("E2E_SANDBOX_TOKEN", "")
        if not token:
            failures.append("cleanup: E2E_SANDBOX_TOKEN is not set")
        else:
            scrubbed = child_env(env)
            failure = authenticate_gh(token, scrubbed, runner)
            if failure:
                failures.append(failure)
            resource_failures, evidence = _cleanup_smoke_resources(descriptor, scrubbed, runner)
            failures += resource_failures
    except Exception as exc:
        failures.append(f"cleanup error: {type(exc).__name__}: {exc}")
        evidence["exception"] = traceback.format_exc()
    evidence["failures"] = list(failures)
    _atomic_json(out / "smoke-cleanup.json", evidence)
    result["failures"] += failures
    if result["failures"]:
        result.update(passed=False, outcome="failed")
    _atomic_json(out / "result.json", result)
    for failure in failures:
        print(f"e2e_harness: FAIL {failure}", file=sys.stderr)
    return 1 if result["outcome"] == "failed" else 0


def check_pull_requests(prs: Sequence[Mapping[str, Any]], expected_state: str | None) -> list[str]:
    if len(prs) != 1:
        return [f"expected exactly one new sandbox pull request, found {len(prs)}"]
    pr = prs[0]
    failures: list[str] = []
    state = str(pr.get("state", "")).lower()
    if state != expected_state:
        failures.append(f"pull request #{pr.get('number')} is {state}, expected {expected_state}")
    if int(pr.get("additions", 0)) + int(pr.get("deletions", 0)) <= 0:
        failures.append(f"pull request #{pr.get('number')} has an empty diff")
    return failures


def close_open_pull_requests(
    repository: str,
    baseline: int,
    listed: Sequence[Mapping[str, Any]] | None,
    env: Mapping[str, str],
    runner: Runner,
) -> list[str]:
    if listed is None:
        listed, failure = new_pull_requests(repository, baseline, env, runner)
        if listed is None:
            return [f"cleanup: {failure}"]
    failures: list[str] = []
    for pr in listed:
        if str(pr.get("state", "")).lower() != "open":
            continue
        argv = ["gh", "pr", "close", str(pr["number"]), "--repo", repository, "--delete-branch"]
        _, failure = _setup_call(runner, argv, env)
        if failure is not None:
            failures.append(f"cleanup: {failure}")
    return failures


def install_session_hooks(home: Path, trace: Path) -> None:
    path = home / ".claude" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    command = f"python3 /opt/e2e/e2e_sessions.py {shlex.quote(str(trace))}"
    hooks = settings.setdefault("hooks", {})
    for event in ("SessionStart", "SessionEnd", "SubagentStart", "SubagentStop"):
        hooks.setdefault(event, []).append(
            {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
        )
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def _json_call(
    argv: Sequence[str],
    env: Mapping[str, str],
    runner: Runner,
    *,
    evidence: Path | None = None,
    cwd: Path | None = None,
) -> Any:
    result, failure = _setup_call(runner, argv, env, evidence=evidence, cwd=cwd)
    if failure or result is None:
        raise ValueError(failure or "missing command result")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{' '.join(argv[:3])}: invalid JSON") from exc


def _api_pages(endpoint: str, env: Mapping[str, str], runner: Runner, evidence: Path) -> list[Any]:
    pages = _json_call(
        ["gh", "api", endpoint, "--paginate", "--slurp"], env, runner, evidence=evidence
    )
    if not isinstance(pages, list):
        raise ValueError("GitHub API: expected paginated JSON array")
    return pages


def prepare_seed(
    test: e2e_catalog.CatalogTest,
    repository: str,
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> tuple[dict[str, Any], dict[str, str], str]:
    url = env.get(test.issue_url_env or "", "")
    match = re.fullmatch(
        r"https://github\.com/" + re.escape(repository) + r"/issues/([1-9][0-9]*)", url
    )
    if not match:
        raise ValueError("seed: missing or noncanonical sandbox issue URL")
    config = _json_call(
        ["autoskillit", "config", "show"],
        env,
        runner,
        evidence=out / "effective-config",
        cwd=SANDBOX_CLONE,
    )
    label = config["github"]["in_progress_label"]
    seed = _json_call(
        ["gh", "issue", "view", url, "--json", "number,url,state,title,body,labels"],
        env,
        runner,
        evidence=out / "seed",
    )
    if (
        not isinstance(seed, dict)
        or seed.get("url") != url
        or type(seed.get("number")) is not int
        or seed["number"] != int(match[1])
        or seed.get("state") != "OPEN"
        or not isinstance(seed.get("title"), str)
        or not seed["title"].strip()
        or not isinstance(seed.get("body"), str)
        or not isinstance(seed.get("labels"), list)
        or any(
            not isinstance(row, dict) or not isinstance(row.get("name"), str)
            for row in seed["labels"]
        )
    ):
        raise ValueError("seed: expected the configured OPEN sandbox issue with valid metadata")
    if label in {row["name"] for row in seed["labels"]}:
        raise ValueError("seed: issue is already claimed")
    repo = _json_call(
        ["gh", "repo", "view", repository, "--json", "defaultBranchRef"], env, runner
    )
    default_branch = repo.get("defaultBranchRef") if isinstance(repo, dict) else None
    if (
        not isinstance(default_branch, dict)
        or default_branch.get("name") != "main"
        or config["branching"]["default_base_branch"] != "main"
    ):
        raise ValueError("seed: sandbox and effective configured base branch must both be main")
    ingredients = dict(test.ingredients)
    ingredients.update(issue_url=url, task=f"{seed['title']}\n\n{seed['body']}")
    for stage, argv in (
        ("baseline-install", ["task", "install-worktree"]),
        ("baseline-test", ["task", "test-check"]),
    ):
        _, failure = _call(
            runner,
            argv,
            what=stage,
            evidence=out / stage,
            cwd=SANDBOX_CLONE,
            env=env,
            timeout=SETUP_COMMAND_TIMEOUT_SEC,
        )
        if failure:
            raise ValueError(failure)
    return seed, ingredients, label


def _repo_matches(value: Any, repository: str) -> bool:
    return isinstance(value, dict) and (
        value.get("full_name") == repository
        or value.get("url") == f"https://api.github.com/repos/{repository}"
    )


def owned_pull_requests(
    rows: Sequence[Mapping[str, Any]],
    proof: Mapping[str, Any],
    repository: str,
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> list[dict[str, Any]]:
    owned = []
    for row in rows:
        pr = _json_call(
            ["gh", "api", f"repos/{repository}/pulls/{row['number']}"],
            env,
            runner,
            evidence=out / f"pr-{row['number']}",
        )
        if not isinstance(pr, dict):
            raise ValueError("PR discovery: expected an object")
        head = pr.get("head")
        if (
            isinstance(head, dict)
            and head.get("ref") in proof["branches"]
            and _repo_matches(head.get("repo"), repository)
        ):
            pr["_listed_head"] = row.get("headRefOid")
            owned.append(pr)
    return owned


def validate_sandbox_ci(
    repository: str,
    number: int,
    head: str,
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> None:
    workflows = _api_pages(
        f"repos/{repository}/actions/workflows?per_page=100", env, runner, out / "ci-workflows"
    )
    candidates = [
        workflow
        for page in workflows
        for workflow in page.get("workflows", [])
        if workflow.get("name") == "CI" and workflow.get("state") == "active"
    ]
    if len(candidates) != 1:
        raise ValueError("sandbox CI: expected one active CI workflow")
    for attempt in range(6):
        pages = _api_pages(
            f"repos/{repository}/actions/workflows/{candidates[0]['id']}/runs?event=pull_request&per_page=100",
            env,
            runner,
            out / "ci-runs",
        )
        runs = [
            run
            for page in pages
            for run in page.get("workflow_runs", [])
            if any(
                pr.get("number") == number
                and pr.get("head", {}).get("sha") == head
                and _repo_matches(pr.get("head", {}).get("repo"), repository)
                and _repo_matches(pr.get("base", {}).get("repo"), repository)
                for pr in run.get("pull_requests", [])
            )
        ]
        if runs:
            run = max(runs, key=lambda value: (value["id"], value.get("run_attempt", 1)))
            if run.get("status") == "completed":
                jobs = _api_pages(
                    f"repos/{repository}/actions/runs/{run['id']}/jobs?per_page=100",
                    env,
                    runner,
                    out / "ci-jobs",
                )
                tests = [
                    job
                    for page in jobs
                    for job in page.get("jobs", [])
                    if job.get("name") == "test"
                ]
                if (
                    run.get("conclusion") != "success"
                    or not tests
                    or any(
                        job.get("status") != "completed" or job.get("conclusion") != "success"
                        for job in tests
                    )
                ):
                    raise ValueError("sandbox CI: CI run and test job must succeed")
                return
        if attempt < 5:
            time.sleep(10)
    raise ValueError("sandbox CI: no completed CI run associated with the exact PR head")


def validate_implementation_pr(
    test: e2e_catalog.CatalogTest,
    prs: Sequence[Mapping[str, Any]],
    seed: Mapping[str, Any],
    repository: str,
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> list[str]:
    if len(prs) != 1:
        return [f"expected exactly one new owned sandbox pull request, found {len(prs)}"]
    pr = prs[0]
    number = pr["number"]
    head = pr.get("head", {})
    base = pr.get("base", {})
    oid = head.get("sha")
    if (
        pr.get("state") != "open"
        or pr.get("merged") is not False
        or pr.get("merged_at")
        or pr.get("auto_merge") is not None
        or base.get("ref") != "main"
        or head.get("ref") == "main"
        or not isinstance(oid, str)
        or not oid
        or not _repo_matches(base.get("repo"), repository)
        or not _repo_matches(head.get("repo"), repository)
    ):
        return [
            "implementation PR: expected an open, unmerged sandbox PR targeting main "
            "without auto-merge"
        ]
    if oid != pr.get("_listed_head"):
        return ["implementation PR: REST head differs from captured headRefOid"]
    if not re.search(re.escape(seed["url"]) + r"(?![A-Za-z0-9_/#?=-])", str(pr.get("body", ""))):
        return ["implementation PR: body does not reference the canonical seed URL"]
    if (
        type(pr.get("additions")) is not int
        or type(pr.get("deletions")) is not int
        or (pr["additions"] + pr["deletions"] <= 0)
    ):
        return ["implementation PR: empty or malformed diff"]
    pages = _api_pages(
        f"repos/{repository}/pulls/{number}/files?per_page=100", env, runner, out / "pr-files"
    )
    files = [row for page in pages for row in page]
    for prefix in test.required_changed_paths:
        if not any(
            row.get("status") != "removed"
            and isinstance(row.get("filename"), str)
            and row["filename"].startswith(prefix)
            and row.get("additions", 0) + row.get("deletions", 0) > 0
            for row in files
        ):
            return [f"implementation PR: missing non-removed changes under {prefix}"]
    failures = test_pr_head(test, number, oid, out, env, runner)
    if failures:
        return failures
    validate_sandbox_ci(repository, number, oid, out, env, runner)
    final = _json_call(
        ["gh", "api", f"repos/{repository}/pulls/{number}"], env, runner, evidence=out / "pr-final"
    )
    if (
        final.get("head", {}).get("sha") != oid
        or final.get("state") != "open"
        or final.get("merged") is not False
        or final.get("merged_at")
    ):
        return ["implementation PR: head or open/unmerged state changed during validation"]
    return []


def test_pr_head(
    test: e2e_catalog.CatalogTest,
    number: int,
    oid: str,
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> list[str]:
    commands = (
        ("pr-fetch", ["git", "fetch", "origin", f"refs/pull/{number}/head"]),
        ("pr-oid", ["git", "rev-parse", "FETCH_HEAD"]),
        ("pr-checkout", ["git", "checkout", "--detach", oid]),
        ("pr-install", ["task", "install-worktree"]),
        ("pr-test", list(test.test_command)),
    )
    for stage, argv in commands:
        result, failure = _call(
            runner,
            argv,
            what=stage,
            evidence=out / stage,
            cwd=SANDBOX_CLONE,
            env=env,
            timeout=SETUP_COMMAND_TIMEOUT_SEC,
        )
        if failure:
            return [failure]
        if stage == "pr-oid" and (result is None or result.stdout.strip() != oid):
            return ["implementation PR: fetched head differs from captured head"]
    return []


def discover_cleanup_prs(
    repository: str,
    baseline: int,
    proof: Mapping[str, Any],
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> tuple[list[dict[str, Any]], list[str]]:
    failures: list[str] = []
    owned: list[dict[str, Any]] = []
    for attempt in range(2):
        try:
            rows, failure = new_pull_requests(repository, baseline, env, runner, pipeline=True)
            if rows is None:
                raise ValueError(failure)
            owned = owned_pull_requests(rows, proof, repository, out, env, runner)
            if owned or attempt == 1:
                if rows and not proof["branches"]:
                    failures.append("cleanup: PR ownership could not be established")
                break
        except Exception as exc:
            if attempt == 1:
                failures.append(f"cleanup discovery: {exc}")
        if attempt == 0:
            time.sleep(1)
    return owned, failures


def delete_owned_branch(
    repository: str, branch: str, env: Mapping[str, str], runner: Runner
) -> None:
    if branch == "main" or not branch or branch.startswith("refs/"):
        raise ValueError("refusing to delete an unsafe/default branch")
    endpoint = f"repos/{repository}/git/refs/heads/{quote(branch, safe='')}"
    result, failure = _setup_call(runner, ["gh", "api", endpoint], env)
    if failure and result is not None and "404" in result.stderr:
        return
    if failure:
        raise ValueError(failure)
    time.sleep(1)
    _, failure = _setup_call(runner, ["gh", "api", "--method", "DELETE", endpoint], env)
    if failure:
        raise ValueError(failure)


def release_owned_claim(
    proof: Mapping[str, Any],
    seed: Mapping[str, Any],
    label: str,
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> list[str]:
    current = _json_call(
        ["gh", "issue", "view", seed["url"], "--json", "state,labels"],
        env,
        runner,
        evidence=out / "seed-final",
    )
    failures = [] if current.get("state") == "OPEN" else ["cleanup: seed issue is no longer OPEN"]
    if label not in {row["name"] for row in current["labels"]}:
        return failures
    if not proof["claimed"]:
        return [*failures, "cleanup: claim label is present without this run's claim proof"]
    time.sleep(1)
    _, failure = _setup_call(
        runner, ["gh", "issue", "edit", seed["url"], "--remove-label", label], env
    )
    if failure:
        failures.append(f"cleanup: {failure}")
    return failures


def cleanup_implementation(
    repository: str,
    baseline: int,
    proof: Mapping[str, Any],
    seed: Mapping[str, Any],
    label: str,
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> list[str]:
    owned, failures = discover_cleanup_prs(repository, baseline, proof, out, env, runner)
    for pr in owned:
        try:
            if pr.get("state") == "open":
                time.sleep(1)
                _, failure = _setup_call(
                    runner, ["gh", "pr", "close", str(pr["number"]), "--repo", repository], env
                )
                if failure:
                    failures.append(f"cleanup: {failure}")
        except Exception as exc:
            failures.append(f"cleanup PR: {exc}")
    for branch in proof["branches"]:
        try:
            delete_owned_branch(repository, branch, env, runner)
        except Exception as exc:
            failures.append(f"cleanup branch: {exc}")
    try:
        failures += release_owned_claim(proof, seed, label, out, env, runner)
    except Exception as exc:
        failures.append(f"cleanup claim: {exc}")
    return failures


def match_recipe_findings(
    failures: list[str],
    findings: Sequence[Mapping[str, str]],
    expected: Sequence[Mapping[str, str]],
) -> tuple[list[str], list[dict[str, str]]]:
    annotations = {(row["check"], row["message"]): row for row in expected}
    matched: dict[tuple[str, str], dict[str, str]] = {}
    for row in findings:
        identity = (row["check"], row["message"])
        if identity in annotations:
            matched[identity] = dict(annotations[identity])
        else:
            failures.append(f"{row['check']}: {row['message']}")
    for identity, annotation in annotations.items():
        if identity not in matched:
            failures.append(
                f"expected recipe finding absent {identity}; remove stale expected failure "
                f"for {annotation['issue']}"
            )
    return failures, list(matched.values())


def run_recipe(
    test: e2e_catalog.CatalogTest,
    repository: str,
    token: str,
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
    *,
    home: Path | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    failure = (
        configure_git(env, runner)
        or authenticate_gh(token, env, runner)
        or clone_sandbox(repository, env, runner)
    )
    if failure is not None:
        return [failure], []
    if test.recipe_fixture is not None:
        return run_smoke_recipe(test, repository, out, env, runner)
    seed: dict[str, Any] = {}
    ingredients = dict(test.ingredients)
    label = ""
    home = home or Path.home()
    trace = out / "session-events.jsonl"
    if test.pipeline or test.issue_url_env:
        try:
            seed, ingredients, label = prepare_seed(test, repository, out, env, runner)
            if test.pipeline:
                trace.write_text("", encoding="utf-8")
                install_session_hooks(home, trace)
        except Exception as exc:
            return [f"preflight: {exc}"], []
    baseline, failure = latest_pull_request_number(repository, env, runner)
    if baseline is None:
        return [str(failure)], []
    if not test.pipeline:
        return launch_regular_recipe(test, repository, baseline, ingredients, out, env, runner)
    return launch_recipe(
        test, repository, baseline, seed, ingredients, label, out, home, env, runner
    )


def launch_regular_recipe(
    test: e2e_catalog.CatalogTest,
    repository: str,
    baseline: int,
    ingredients: Mapping[str, str],
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> tuple[list[str], list[dict[str, str]]]:
    failures: list[str] = []
    matched: list[dict[str, str]] = []
    listed: list[dict[str, Any]] | None = None
    try:
        envelope, failures, _ = run_fleet(test, out, env, runner, ingredients=ingredients)
        failures, matched = match_recipe_failure(test, failures, envelope)
        if not matched:
            listed, failure = new_pull_requests(repository, baseline, env, runner)
            if listed is None:
                failures.append(str(failure))
            else:
                failures += check_pull_requests(listed, test.expected_pull_request_state)
    finally:
        failures += close_open_pull_requests(repository, baseline, listed, env, runner)
    return failures, matched


def save_ownership(
    trace: Path,
    envelope: dict[str, Any] | None,
    log_root: Path,
    seed: Mapping[str, Any],
    repository: str,
    out: Path,
    label: str,
) -> dict[str, Any]:
    proof = e2e_sessions.collect_ownership(
        trace, envelope, log_root, seed["url"], repository, claim_label=label
    )
    (out / "ownership.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    return proof


def finish_pipeline(
    test: e2e_catalog.CatalogTest,
    repository: str,
    baseline: int,
    seed: Mapping[str, Any],
    label: str,
    out: Path,
    home: Path,
    env: Mapping[str, str],
    runner: Runner,
    envelope: dict[str, Any] | None,
    proof: dict[str, Any] | None,
) -> list[str]:
    failures: list[str] = []
    trace = out / "session-events.jsonl"
    log_root = home / ".local" / "share" / "autoskillit" / "logs"
    try:
        summary = e2e_sessions.summarize(trace, envelope, log_root)
        if not summary["coverage"]["complete"]:
            failures.append("session measurement: incomplete lifecycle coverage")
        if summary["peak_sessions"] > test.peak_sessions:
            failures.append("session measurement: observed peak exceeds catalog capacity")
    except Exception as exc:
        failures.append(f"session measurement: {exc}")
    if proof is None:
        try:
            proof = save_ownership(trace, envelope, log_root, seed, repository, out, label)
            failures += [f"ownership: {value}" for value in proof["violations"]]
        except Exception as exc:
            failures.append(f"cleanup ownership: {exc}")
    proof = proof or {"branches": [], "claimed": False}
    failures += cleanup_implementation(repository, baseline, proof, seed, label, out, env, runner)
    return failures


def launch_recipe(
    test: e2e_catalog.CatalogTest,
    repository: str,
    baseline: int,
    seed: Mapping[str, Any],
    ingredients: Mapping[str, str],
    label: str,
    out: Path,
    home: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> tuple[list[str], list[dict[str, str]]]:
    failures: list[str] = []
    findings: list[dict[str, str]] = []
    envelope: dict[str, Any] | None = None
    proof: dict[str, Any] | None = None
    try:
        envelope, fleet_failures, findings = run_fleet(
            test, out, env, runner, ingredients=ingredients
        )
        failures += fleet_failures
        proof = save_ownership(
            out / "session-events.jsonl",
            envelope,
            home / ".local" / "share" / "autoskillit" / "logs",
            seed,
            repository,
            out,
            label,
        )
        failures += [f"ownership: {value}" for value in proof["violations"]]
        listed, failure = new_pull_requests(repository, baseline, env, runner, pipeline=True)
        if listed is None:
            failures.append(str(failure))
        else:
            owned = owned_pull_requests(listed, proof, repository, out, env, runner)
            if not fleet_failures and not findings:
                failures += validate_implementation_pr(
                    test, owned, seed, repository, out, env, runner
                )
    except Exception as exc:
        failures.append(f"harness error: {exc}")
    finally:
        failures += finish_pipeline(
            test, repository, baseline, seed, label, out, home, env, runner, envelope, proof
        )
    return match_recipe_findings(failures, findings, test.expected_failures)


def check_clean_install_doctor(
    stdout: str, expected_failures: Sequence[Mapping[str, str]]
) -> tuple[list[str], list[dict[str, str]]]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return [f"doctor: invalid JSON: {exc}"], []
    if not isinstance(payload, dict):
        return ["doctor: report must be an object"], []
    rows = payload.get("results")
    if not isinstance(rows, list) or not rows:
        return ["doctor: results must be a non-empty list"], []
    expected = {(row["severity"], row["check"], row["message"]): row for row in expected_failures}
    failures: list[str] = []
    matched: dict[tuple[str, str, str], dict[str, str]] = {}
    for index, row in enumerate(rows):
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("severity"), str)
            or row["severity"] not in ("ok", "info", "warning", "error")
            or not isinstance(row.get("check"), str)
            or not isinstance(row.get("message"), str)
        ):
            failures.append(f"doctor: invalid result at index {index}")
            continue
        severity, check, message = row["severity"], row["check"], row["message"]
        if severity in ("ok", "info") or (
            severity == "warning" and CLEAN_INSTALL_ALLOWED_WARNINGS.get((check, message))
        ):
            continue
        identity = (severity, check, message)
        if identity in expected:
            matched[identity] = dict(expected[identity])
        else:
            failures.append(f"doctor: unexpected {severity} {check}: {message}")
    for identity, row in expected.items():
        if identity not in matched:
            failures.append(
                f"doctor: expected diagnostic absent {identity}; remove stale expected failure "
                f"for fixed bug {row['issue']}"
            )
    return failures, list(matched.values())


def run_clean_install(
    test: e2e_catalog.CatalogTest, out: Path, env: Mapping[str, str], runner: Runner
) -> tuple[list[str], list[dict[str, str]]]:
    deadline = time.monotonic() + test.timeout_sec
    scrubbed = {
        name: value for name, value in child_env(env).items() if not name.startswith("OPENAI_")
    }
    scratch_root = out / ".autoskillit" / "temp" / "clean-install"
    scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(dir=scratch_root))
    commands = (
        ("install", ["autoskillit", "install"]),
        ("git-init", ["git", "init"]),
        ("init", ["autoskillit", "init", "--test-command", "git diff --check"]),
        ("doctor", ["autoskillit", "doctor", "--output-json"]),
    )
    try:
        for stage, argv in commands:
            if stage == "init":
                (scratch / ".pre-commit-config.yaml").write_text(
                    "repos:\n  - repo: https://github.com/gitleaks/gitleaks\n"
                    "    rev: v8.30.0\n    hooks:\n      - id: gitleaks\n",
                    encoding="utf-8",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return [f"clean-install: timeout budget exhausted before {stage}"], []
            result, failure = _call(
                runner,
                argv,
                what=stage,
                evidence=out / stage,
                cwd=scratch,
                env=scrubbed,
                timeout=remaining,
            )
            if stage == "doctor":
                shutil.copyfile(out / "doctor.stdout.txt", out / "doctor.json")
            if failure is not None:
                return [failure], []
        assert result is not None
        return check_clean_install_doctor(result.stdout, test.expected_failures)
    finally:
        shutil.rmtree(scratch)


def _run_test(
    test: e2e_catalog.CatalogTest,
    catalog: e2e_catalog.Catalog,
    *,
    out: Path,
    home: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> tuple[list[str], list[dict[str, str]]]:
    if test.kind == "clean-install":
        return run_clean_install(test, out, env, runner)
    required = SECRET_ENV if test.kind == "recipe" else ("MINIMAX_API_KEY",)
    missing = [f"{name} is not set" for name in required if not env.get(name)]
    if missing:
        return missing, []
    write_claude_settings(home, env["MINIMAX_API_KEY"])
    install_autoskillit_config(home)
    scrubbed = child_env(env)
    if test.kind == "canary":
        return run_canary(test, out, scrubbed, runner), []
    token = env["E2E_SANDBOX_TOKEN"]
    return run_recipe(test, catalog.sandbox_repository, token, out, scrubbed, runner, home=home)


def run_test(
    test: e2e_catalog.CatalogTest,
    catalog: e2e_catalog.Catalog,
    *,
    out: Path,
    home: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> list[str]:
    """Run *test* and always record ``out/result.json``; return the failures."""
    out.mkdir(parents=True, exist_ok=True)
    matched_findings: list[dict[str, str]] = []
    result: dict[str, Any] = {"test": test.name}
    try:
        failures, matched_findings = _run_test(
            test, catalog, out=out, home=home, env=env, runner=runner
        )
    except Exception as exc:
        failures = [f"harness error: {type(exc).__name__}: {exc}"]
        result["exception"] = traceback.format_exc()
    result.update(passed=not failures, failures=failures)
    if test.kind in ("clean-install", "recipe"):
        outcome = "failed" if failures else "expected_failure" if matched_findings else "passed"
        result.update(
            outcome=outcome, passed=outcome == "passed", expected_findings=matched_findings
        )
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return failures


def _redact(data: bytes, needles: Sequence[bytes]) -> bytes:
    for needle in needles:
        data = data.replace(needle, REDACTED)
    return data


def _copy_redacted(source: Path, target: Path, needles: Sequence[bytes]) -> None:
    target.write_bytes(_redact(source.read_bytes(), needles))
    target.chmod(0o644)


def _copy_tree_redacted(source: Path, target: Path, needles: Sequence[bytes]) -> None:
    for root, _dirs, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        target_dir = target / root_path.relative_to(source)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_dir.chmod(0o755)
        for name in files:
            path = root_path / name
            if not path.is_symlink() and path.is_file():
                _copy_redacted(path, target_dir / name, needles)


def _find_leak(dest: Path, needles: Sequence[bytes]) -> Path | None:
    for root, _dirs, files in os.walk(dest, followlinks=False):
        for name in files:
            path = Path(root) / name
            try:
                data = path.read_bytes()
            except (FileNotFoundError, NotADirectoryError):
                # A file that vanished after the walk cannot be uploaded, so it cannot leak.
                continue
            if any(needle in data for needle in needles):
                return path
    return None


def redact_tree(sources: Sequence[Path], dest: Path, secrets: Sequence[str]) -> None:
    """Copy *sources* into *dest* world-readable with every secret replaced; verify the copy."""
    needles = [secret.encode() for secret in secrets if secret]
    dest.mkdir(parents=True, exist_ok=True)
    for source in sources:
        if source.is_symlink() or not source.exists():
            continue
        target = dest / source.name
        if source.is_dir():
            _copy_tree_redacted(source, target, needles)
        else:
            _copy_redacted(source, target, needles)
    leak = _find_leak(dest, needles)
    if leak is not None:
        raise RuntimeError(f"a secret survived redaction in {leak}")


def _run(test_name: str, out: Path, catalog_path: Path) -> int:
    try:
        catalog = e2e_catalog.load_catalog(catalog_path)
        test = catalog.get(test_name)
    except e2e_catalog.CatalogError as exc:
        print(f"e2e_harness: {exc}", file=sys.stderr)
        return 2
    failures = run_test(
        test, catalog, out=out, home=Path.home(), env=os.environ, runner=run_command
    )
    for failure in failures:
        print(f"e2e_harness: FAIL {failure}", file=sys.stderr)
    outcome = "failed" if failures else "passed"
    if test.kind in ("clean-install", "recipe"):
        outcome = json.loads((out / "result.json").read_text(encoding="utf-8"))["outcome"]
    print(f"e2e_harness: {test.name} {outcome}")
    return 1 if failures else 0


def _redact_command(dest: Path, secret_env: Sequence[str], sources: Sequence[str]) -> int:
    secrets = [os.environ.get(name, "") for name in secret_env]
    try:
        redact_tree([Path(source) for source in sources], dest, secrets)
    except (OSError, RuntimeError) as exc:
        print(f"e2e_harness: redaction failed: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str]) -> int:
    """``run`` accepts passes and expected failures; ``redact`` requires a clean copy."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run one catalog test.")
    run.add_argument("--test", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--catalog", default=str(e2e_catalog.CATALOG_PATH))
    cleanup = commands.add_parser("cleanup", help="Verify exact smoke resource cleanup.")
    cleanup.add_argument("--test", required=True)
    cleanup.add_argument("--out", required=True)
    cleanup.add_argument("--catalog", default=str(e2e_catalog.CATALOG_PATH))
    redact = commands.add_parser("redact", help="Copy artifacts with every secret redacted.")
    redact.add_argument("--dest", required=True)
    redact.add_argument("--secret-env", action="append", required=True)
    redact.add_argument("sources", nargs="+")
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run(args.test, Path(args.out), Path(args.catalog))
    if args.command == "cleanup":
        catalog = e2e_catalog.load_catalog(Path(args.catalog))
        return cleanup_smoke_test(catalog, args.test, run_command, Path(args.out), os.environ)
    return _redact_command(Path(args.dest), args.secret_env, args.sources)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
