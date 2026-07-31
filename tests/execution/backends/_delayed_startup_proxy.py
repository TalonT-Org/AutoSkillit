"""Executable delayed-startup proxy for the Claude CLI conformance probe.

The owning test copies this helper into its ``tmp_path`` before exposing it to
Claude as an MCP server command. Keeping the proxy as ordinary Python makes its
message forwarding and trace instrumentation reviewable without weakening the
probe's filesystem isolation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def _record(
    trace_path: Path,
    write_lock: threading.Lock,
    payload: dict[str, object],
) -> None:
    payload["monotonic_ns"] = time.monotonic_ns()
    payload["server_pid"] = os.getpid()
    with write_lock:
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _pump_client_messages(
    child: subprocess.Popen[bytes],
    *,
    trace_path: Path,
    write_lock: threading.Lock,
    request_lock: threading.Lock,
    requests: dict[str, tuple[object, object]],
) -> None:
    assert child.stdin is not None
    for line in sys.stdin.buffer:
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        method = payload.get("method")
        params = payload.get("params")
        tool_name = params.get("name") if isinstance(params, dict) else None
        request_id = payload.get("id")
        if request_id is not None:
            with request_lock:
                requests[str(request_id)] = (method, tool_name)
        _record(
            trace_path,
            write_lock,
            {
                "event": "client_message",
                "method": method,
                "tool_name": tool_name,
            },
        )
        child.stdin.write(line)
        child.stdin.flush()
    child.stdin.close()


def _pump_child_stderr(child: subprocess.Popen[bytes]) -> None:
    assert child.stderr is not None
    for chunk in iter(lambda: child.stderr.read(8192), b""):
        sys.stderr.buffer.write(chunk)
        sys.stderr.buffer.flush()


def _proxy_child_stdout(
    child: subprocess.Popen[bytes],
    *,
    trace_path: Path,
    write_lock: threading.Lock,
    request_lock: threading.Lock,
    requests: dict[str, tuple[object, object]],
) -> None:
    assert child.stdout is not None
    for line in child.stdout:
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        request_id = payload.get("id")
        with request_lock:
            request = requests.get(str(request_id))
        if request is not None and request[0] == "tools/list":
            result = payload.get("result")
            tools = result.get("tools", []) if isinstance(result, dict) else []
            names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
            schema_bytes = len(json.dumps(tools, sort_keys=True).encode("utf-8"))
            _record(
                trace_path,
                write_lock,
                {
                    "event": "tool_list_snapshot",
                    "tool_names": names,
                    "schema_bytes": schema_bytes,
                },
            )
        elif (
            request is not None
            and request[0] == "tools/call"
            and isinstance(request[1], str)
            and request[1].endswith("open_kitchen")
        ):
            result = payload.get("result")
            is_error = result.get("isError") if isinstance(result, dict) else True
            _record(
                trace_path,
                write_lock,
                {
                    "event": "open_kitchen_result",
                    "is_error": bool(is_error),
                    "has_jsonrpc_error": "error" in payload,
                },
            )
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()


def run_proxy(trace_path: Path, delay_ms: int, executable: str) -> int:
    write_lock = threading.Lock()
    request_lock = threading.Lock()
    requests: dict[str, tuple[object, object]] = {}

    _record(trace_path, write_lock, {"event": "server_delay_started"})
    time.sleep(delay_ms / 1000)
    _record(trace_path, write_lock, {"event": "server_exec_started"})

    child = subprocess.Popen(
        [executable],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    threading.Thread(
        target=_pump_client_messages,
        kwargs={
            "child": child,
            "trace_path": trace_path,
            "write_lock": write_lock,
            "request_lock": request_lock,
            "requests": requests,
        },
        daemon=True,
    ).start()
    threading.Thread(
        target=_pump_child_stderr,
        args=(child,),
        daemon=True,
    ).start()
    _proxy_child_stdout(
        child,
        trace_path=trace_path,
        write_lock=write_lock,
        request_lock=request_lock,
        requests=requests,
    )
    return child.wait()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    return run_proxy(Path(args[0]), int(args[1]), args[2])


if __name__ == "__main__":
    raise SystemExit(main())
