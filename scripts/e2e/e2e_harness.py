#!/usr/bin/env python3
"""Run one catalog E2E test inside the AutoSkillit user image, and redact its artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import e2e_catalog

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
    repository: str, limit: int, fields: str, env: Mapping[str, str], runner: Runner
) -> tuple[list[dict[str, Any]] | None, str | None]:
    argv = ["gh", "pr", "list", "--repo", repository, "--state", "all"]
    argv += ["--limit", str(limit), "--json", fields]
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
    repository: str, baseline: int, env: Mapping[str, str], runner: Runner
) -> tuple[list[dict[str, Any]] | None, str | None]:
    rows, failure = _list_pull_requests(
        repository, 20, "number,state,additions,deletions", env, runner
    )
    if rows is None:
        return None, failure
    return [row for row in rows if int(row["number"]) > baseline], None


def fleet_run_argv(test: e2e_catalog.CatalogTest) -> list[str]:
    ingredients = [arg for key, value in test.ingredients for arg in ("-i", f"{key}={value}")]
    return [
        "autoskillit",
        "fleet",
        "run",
        str(test.recipe),
        *ingredients,
        "--disable-quota-guard",
        "--timeout-sec",
        str(test.timeout_sec),
    ]


def last_line(stdout: str) -> str:
    lines = [line for line in stdout.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def check_envelope(returncode: int, stdout: str) -> list[str]:
    failures = [] if returncode == 0 else [f"fleet run: exit {returncode}"]
    line = last_line(stdout)
    try:
        envelope = json.loads(line)
    except json.JSONDecodeError:
        return [*failures, "fleet run: the last stdout line is not a JSON envelope"]
    if not isinstance(envelope, dict):
        failures.append("fleet run: the envelope is not a JSON object")
    elif envelope.get("success") is not True:
        failures.append("fleet run: the envelope does not report success")
    return failures


def run_fleet(
    test: e2e_catalog.CatalogTest, out: Path, env: Mapping[str, str], runner: Runner
) -> list[str]:
    result, failure = _call(
        runner,
        fleet_run_argv(test),
        what="fleet run",
        cwd=SANDBOX_CLONE,
        env=env,
        timeout=test.timeout_sec + e2e_catalog.HARNESS_GRACE_SEC,
    )
    if result is None:
        return [str(failure)]
    (out / "fleet-run.stdout.log").write_text(result.stdout, encoding="utf-8")
    (out / "fleet-run.stderr.log").write_text(result.stderr, encoding="utf-8")
    (out / "envelope.json").write_text(last_line(result.stdout) + "\n", encoding="utf-8")
    return check_envelope(result.returncode, result.stdout)


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


def run_recipe(
    test: e2e_catalog.CatalogTest,
    repository: str,
    token: str,
    out: Path,
    env: Mapping[str, str],
    runner: Runner,
) -> list[str]:
    failure = (
        configure_git(env, runner)
        or authenticate_gh(token, env, runner)
        or clone_sandbox(repository, env, runner)
    )
    if failure is not None:
        return [failure]
    baseline, failure = latest_pull_request_number(repository, env, runner)
    if baseline is None:
        return [str(failure)]
    failures: list[str] = []
    listed: list[dict[str, Any]] | None = None
    try:
        failures += run_fleet(test, out, env, runner)
        listed, failure = new_pull_requests(repository, baseline, env, runner)
        if listed is None:
            failures.append(str(failure))
        else:
            failures += check_pull_requests(listed, test.expected_pull_request_state)
    finally:
        failures += close_open_pull_requests(repository, baseline, listed, env, runner)
    return failures


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
    return run_recipe(test, catalog.sandbox_repository, token, out, scrubbed, runner), []


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
    try:
        failures, matched_findings = _run_test(
            test, catalog, out=out, home=home, env=env, runner=runner
        )
    except Exception as exc:
        failures = [f"harness error: {exc}"]
    result = {"test": test.name, "passed": not failures, "failures": failures}
    if test.kind == "clean-install":
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
    if test.kind == "clean-install":
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
    redact = commands.add_parser("redact", help="Copy artifacts with every secret redacted.")
    redact.add_argument("--dest", required=True)
    redact.add_argument("--secret-env", action="append", required=True)
    redact.add_argument("sources", nargs="+")
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run(args.test, Path(args.out), Path(args.catalog))
    return _redact_command(Path(args.dest), args.secret_env, args.sources)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
