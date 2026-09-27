#!/usr/bin/env python3
"""Compare the pinned Codex host with and without the shell-capture hook."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from autoskillit.execution.backends import _serialize_toml  # noqa: E402
from autoskillit.execution.backends._codex_hooks import generate_codex_hooks_config  # noqa: E402
from autoskillit.hooks._capture_contract import (  # noqa: E402
    NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
    PROTECTED_CAPTURE_ENV_VARS,
)
from tests.hooks._shell_conformance_matrix import (  # noqa: E402
    CONFORMANCE_CASES,
    CONFORMANCE_INVARIANTS,
    ConformanceBaseline,
    ConformanceCaseDef,
    ConformanceDriver,
    ConformanceExpectation,
    ConformanceMode,
)

_MAX_LOG_BYTES = 2_000_000
_CODEX_TIMEOUT_SECONDS = 300


def _prepare_home(home: Path, *, auth_source: Path) -> None:
    home.mkdir(parents=True)
    auth_file = auth_source / "auth.json"
    if auth_file.is_file():
        shutil.copy2(auth_file, home / "auth.json")


def _write_hook_config(home: Path) -> None:
    generated = generate_codex_hooks_config(plugin_dir=PROJECT_ROOT / "src" / "autoskillit")
    entries = []
    for entry in generated.get("PreToolUse", []):
        commands = [
            hook
            for hook in entry["hooks"]
            if shlex.split(hook["command"])[-1] == "shell_capture_hook"
        ]
        if commands:
            entries.append({**entry, "hooks": commands})
    if len(entries) != 1 or len(entries[0]["hooks"]) != 1:
        raise RuntimeError("generated shell-capture config was not a single PreToolUse entry")
    hooks = {"PreToolUse": entries}
    (home / "config.toml").write_text(_serialize_toml({"hooks": hooks}), encoding="utf-8")


def _kill_host(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _request_session_end(
    process: subprocess.Popen[bytes], leader_file: Path, signum: signal.Signals
) -> bool:
    try:
        leader_pid = int(leader_file.read_text().strip())
        if leader_pid <= 1 or _proc_gone_or_zombie(leader_pid):
            return False
        process.send_signal(signum)
    except (OSError, ValueError):
        return False
    return True


def _collect_codex_logs(
    process: subprocess.Popen[bytes],
    stdout_path: Path,
    stderr_path: Path,
    session_end_file: Path | None,
    session_end_signal: signal.Signals,
) -> tuple[bool, bool, bool]:
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    for stream in (process.stdout, process.stderr):
        selector.register(stream.fileno(), selectors.EVENT_READ)

    started = time.monotonic()
    output_bytes = 0
    exceeded_limit = False
    interrupted = False
    timed_out = False
    with stdout_path.open("wb") as stdout_log, stderr_path.open("wb") as stderr_log:
        destinations = {process.stdout.fileno(): stdout_log, process.stderr.fileno(): stderr_log}
        while selector.get_map():
            if not interrupted and session_end_file is not None:
                interrupted = _request_session_end(process, session_end_file, session_end_signal)
            if time.monotonic() - started > _CODEX_TIMEOUT_SECONDS and not timed_out:
                timed_out = True
                _kill_host(process)
            for key, _mask in selector.select(timeout=0.1):
                descriptor = key.fd
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    selector.unregister(descriptor)
                    continue
                if output_bytes + len(chunk) > _MAX_LOG_BYTES:
                    exceeded_limit = True
                    _kill_host(process)
                    continue
                destinations[descriptor].write(chunk)
                output_bytes += len(chunk)
    selector.close()
    process.stdout.close()
    process.stderr.close()
    return timed_out, exceeded_limit, interrupted


def _run_codex(
    *,
    home: Path,
    project: Path,
    prompt: str,
    stdout_path: Path,
    stderr_path: Path,
    hook_trust_bypass: bool,
    session_end_file: Path | None = None,
    session_end_signal: signal.Signals = signal.SIGKILL,
) -> tuple[int, bool]:
    args = ["codex", "exec", "--json", "--dangerously-bypass-approvals-and-sandbox"]
    if hook_trust_bypass:
        args.append("--dangerously-bypass-hook-trust")
    args.append(prompt)
    environment = {
        name: value for name, value in os.environ.items() if name not in PROTECTED_CAPTURE_ENV_VARS
    }
    environment[NATIVE_SHELL_CAPTURE_MODE_ENV_VAR] = "capture"
    environment["AUTOSKILLIT_AGENT_BACKEND"] = "codex"
    environment["CODEX_HOME"] = str(home)
    process = subprocess.Popen(
        args,
        cwd=project,
        env=environment,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    try:
        timed_out, exceeded_limit, interrupted = _collect_codex_logs(
            process, stdout_path, stderr_path, session_end_file, session_end_signal
        )
    except BaseException:
        _kill_host(process)
        process.wait(timeout=5)
        raise
    try:
        return_code = process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _kill_host(process)
        return_code = process.wait(timeout=5)
    if timed_out or exceeded_limit:
        raise RuntimeError(
            f"codex exec exceeded its time/output bound: timed_out={timed_out}, "
            f"log_limit={exceeded_limit}; output: {stdout_path}; stderr: {stderr_path}"
        )
    if session_end_file is not None and not interrupted:
        raise RuntimeError(f"command was not live at session end; evidence: {stdout_path}")
    return return_code, interrupted


def _command_result(
    stdout_path: Path, command: str, *, interrupted: bool
) -> tuple[bytes, int | None]:
    lines = stdout_path.read_bytes().splitlines()
    events: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            if interrupted and index == len(lines) - 1:
                break
            raise RuntimeError(f"invalid Codex JSON event in {stdout_path}:{index + 1}") from exc
        if isinstance(event, dict):
            events.append(event)

    started = [
        event["item"]
        for event in events
        if event.get("type") == "item.started"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "command_execution"
        and _matches_command(str(event["item"].get("command", "")), command)
    ]
    if len(started) != 1:
        raise RuntimeError(
            "Codex did not report exactly one execution of the requested command; "
            f"output: {stdout_path}"
        )
    item_id = started[0].get("id")
    if interrupted:
        return b"", None

    completed = [
        event["item"]
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "command_execution"
        and event["item"].get("id") == item_id
    ]
    if len(completed) != 1:
        raise RuntimeError(
            f"Codex response is missing the requested command result; output: {stdout_path}"
        )
    item = completed[0]
    output = item.get("aggregated_output", item.get("stdout"))
    exit_code = item.get("exit_code")
    if not isinstance(output, str) or not isinstance(exit_code, int):
        raise RuntimeError(
            f"Codex command result lacks output or exit status; output: {stdout_path}"
        )
    return output.encode("utf-8"), exit_code


def _proc_gone_or_zombie(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return True
    fields = stat[stat.rfind(")") + 1 :].split()
    return bool(fields) and fields[0] == "Z"


def _host_case_observation(project: Path, leader_pid: int) -> bool:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not _proc_gone_or_zombie(leader_pid):
        time.sleep(0.02)
    settled = _proc_gone_or_zombie(leader_pid)
    time.sleep(1.2)
    no_marker = not (project / "marker").exists()
    try:
        os.killpg(leader_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if not settled or not no_marker:
        raise RuntimeError(
            f"host-lifetime failed in {project}: leader_settled={settled}, "
            f"marker_absent={no_marker}"
        )
    return settled and no_marker


def _matches_command(reported: str, command: str) -> bool:
    if reported == command:
        return True
    try:
        argv = shlex.split(reported)
    except ValueError:
        return False
    return len(argv) >= 3 and argv[-2] in {"-c", "-lc"} and argv[-1] == command


def _prompt(command: str) -> str:
    return (
        "Run exactly one Bash command, once, in the current directory. "
        "Perform no additional actions or commands and do not change the command text. "
        "Return no prose.\n\n" + command
    )


def _summary(output: bytes, exit_code: int | None, *, settled: bool | None = None) -> str:
    if settled is not None:
        return f"settled={settled}"
    preview = repr(output[:80]) + ("..." if len(output) > 80 else "")
    return f"exit={exit_code},output={preview}"


def _observe_case(
    case: ConformanceCaseDef, home: Path, project: Path, *, hooked: bool
) -> tuple[bytes, int | None, bool | None]:
    (project / ".autoskillit" / "temp" / "shell_capture").mkdir(parents=True)
    expectation = case.expect[ConformanceMode.CAPTURE]
    session_end = expectation is ConformanceExpectation.USER_GROUP_SETTLED
    output_path = project.parent / f"{project.name}.jsonl"
    stderr_path = project.parent / f"{project.name}.stderr"
    return_code, interrupted = _run_codex(
        home=home,
        project=project,
        prompt=_prompt(case.command),
        stdout_path=output_path,
        stderr_path=stderr_path,
        hook_trust_bypass=hooked,
        session_end_file=project / "leader.pid" if session_end else None,
        session_end_signal=(
            signal.SIGTERM if case.driver is ConformanceDriver.HOST_SIGTERM else signal.SIGKILL
        ),
    )
    if return_code != 0 and not interrupted:
        raise RuntimeError(
            f"codex exec failed ({return_code}); output: {output_path}; stderr: {stderr_path}"
        )
    output, command_exit = _command_result(output_path, case.command, interrupted=interrupted)
    marker_ok: bool | None = None
    if expectation is ConformanceExpectation.USER_GROUP_SETTLED:
        pid = int((project / "leader.pid").read_text().strip())
        marker_ok = _host_case_observation(project, pid)
    elif expectation is ConformanceExpectation.MARKER_SETTLED:
        time.sleep(1.0)
        marker_ok = not (project / "marker").exists()
    return output, command_exit, marker_ok


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    if shutil.which("codex") is None:
        raise RuntimeError("codex CLI is required; run this tool inside the verification image")
    if not Path("/proc/self/stat").is_file():
        raise RuntimeError("host-lifetime baseline requires /proc")

    temp_root = PROJECT_ROOT / ".autoskillit" / "temp" / "codex_shell_baseline"
    temp_root.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix="run-", dir=temp_root))
    source_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    native_home = run_root / "codex-native"
    hooked_home = run_root / "codex-hooked"
    _prepare_home(native_home, auth_source=source_home)
    _prepare_home(hooked_home, auth_source=source_home)
    _write_hook_config(hooked_home)

    cases = [
        case
        for case in CONFORMANCE_CASES
        if CONFORMANCE_INVARIANTS[case.invariant].baseline is ConformanceBaseline.NATIVE_CODEX
        or case.invariant == "initial-trap-state"
    ]
    print("case | native | hooked | capture expectation")
    failures = 0
    for case in cases:
        expectation = case.expect[ConformanceMode.CAPTURE]
        case_root = run_root / case.id
        observations: dict[str, tuple[bytes, int | None, bool | None]] = {}
        for label, codex_home, hooked in (
            ("native", native_home, False),
            ("hooked", hooked_home, True),
        ):
            project = case_root / f"{label}-project"
            observations[label] = _observe_case(case, codex_home, project, hooked=hooked)

        native = observations["native"]
        hooked_result = observations["hooked"]
        if expectation is ConformanceExpectation.RAW_OUTPUT_AND_STATUS:
            agreed = native[:2] == hooked_result[:2] and native[1] == 0
            if case.invariant == "initial-trap-state":
                agreed = agreed and native[0] == b"end\n"
        elif expectation is ConformanceExpectation.MARKER_SETTLED:
            agreed = (
                native[:2] == hooked_result[:2]
                and native[1] == 0
                and bool(native[2])
                and bool(hooked_result[2])
            )
        elif expectation is ConformanceExpectation.USER_GROUP_SETTLED:
            agreed = bool(native[2]) and bool(hooked_result[2])
        else:
            raise RuntimeError(f"unsupported live Codex expectation: {expectation.value}")

        native_text = _summary(native[0], native[1], settled=native[2])
        hooked_text = _summary(hooked_result[0], hooked_result[1], settled=hooked_result[2])
        print(f"{case.id} | {native_text} | {hooked_text} | {expectation.value}")
        if not agreed:
            failures += 1

    print(f"logs: {run_root}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, StopIteration) as exc:
        print(f"codex shell baseline failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
