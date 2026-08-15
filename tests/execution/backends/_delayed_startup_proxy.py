"""Executable delayed-startup proxy for the Claude CLI conformance probe.

The owning test copies this helper into its ``tmp_path`` before exposing it to
Claude as an MCP server command. Keeping the proxy as ordinary Python makes its
message forwarding and trace instrumentation reviewable without weakening the
probe's filesystem isolation.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_MAX_TRACE_EVENTS = 256
_MAX_LIST_PAGES = 32


@dataclass(frozen=True, slots=True)
class PendingRequest:
    """Correlated discovery/call request awaiting one terminal child response."""

    method: str
    tool_name: str | None
    list_cursor: object | None
    monotonic_ns: int
    child_pid: int


def _argument_shape(params: object) -> dict[str, str]:
    if not isinstance(params, dict):
        return {}
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return {}
    return {str(key): type(value).__name__ for key, value in list(arguments.items())[:32]}


def response_measurements(texts: list[str]) -> dict[str, object]:
    """Measure one tool result without conflating content, serialization, or cost."""
    raw_content = "".join(texts)
    cost_usd: float | None = None
    for text in texts:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        for key in ("cost_usd", "total_cost_usd"):
            value = payload.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                cost_usd = float(value)
                break
        if cost_usd is not None:
            break
    utf8_bytes = len(raw_content.encode("utf-8"))
    return {
        "raw_chars": len(raw_content),
        "utf8_bytes": utf8_bytes,
        "client_serialized_chars": len(json.dumps(raw_content)),
        "estimated_tokens": utf8_bytes // 4,
        "raw_content_sha256": hashlib.sha256(raw_content.encode("utf-8")).hexdigest(),
        "cost_usd": cost_usd,
    }


def classify_attempt(
    events: list[dict[str, object]],
    *,
    server_pid: int,
    expected_identity: dict[str, object] | None = None,
) -> str:
    """Classify one proxy attempt from joined, bounded trace evidence."""
    attempt = [event for event in events if event.get("server_pid") == server_pid]
    if expected_identity is not None and any(
        event.get("plugin_identity") != expected_identity for event in attempt
    ):
        return "process_or_artifact_changed"
    if not any(event.get("event") == "tool_list_snapshot" for event in attempt):
        return "never_listed"
    if not any(
        event.get("event") == "client_message" and event.get("method") == "tools/call"
        for event in attempt
    ):
        return "listed_no_dispatch"
    outcomes = [
        event.get("outcome") for event in attempt if event.get("event") == "open_kitchen_result"
    ]
    if not outcomes:
        return "dispatched_no_response"
    outcome = outcomes[-1]
    return str(outcome) if outcome in {"protocol_error", "tool_error", "success"} else "unknown"


def _record(
    trace_path: Path,
    write_lock: threading.Lock,
    payload: dict[str, object],
    *,
    event_count: list[int],
    plugin_identity: dict[str, object],
) -> None:
    with write_lock:
        if event_count[0] >= _MAX_TRACE_EVENTS:
            raise RuntimeError(f"startup proxy trace exceeded {_MAX_TRACE_EVENTS} events")
        event_count[0] += 1
        payload["monotonic_ns"] = time.monotonic_ns()
        payload["server_pid"] = os.getpid()
        payload["plugin_identity"] = plugin_identity
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _pump_client_messages(
    child: subprocess.Popen[bytes],
    *,
    trace_path: Path,
    write_lock: threading.Lock,
    request_lock: threading.Lock,
    requests: dict[object, PendingRequest],
    event_count: list[int],
    plugin_identity: dict[str, object],
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
        if request_id is not None and method in {"tools/list", "tools/call"}:
            cursor = params.get("cursor") if isinstance(params, dict) else None
            with request_lock:
                requests[request_id] = PendingRequest(
                    method=method,
                    tool_name=tool_name if isinstance(tool_name, str) else None,
                    list_cursor=cursor,
                    monotonic_ns=time.monotonic_ns(),
                    child_pid=child.pid,
                )
        _record(
            trace_path,
            write_lock,
            {
                "event": "client_message",
                "method": method,
                "tool_name": tool_name,
                "request_id": request_id,
                "request_id_type": type(request_id).__name__,
                "argument_shape": _argument_shape(params),
                "child_pid": child.pid,
            },
            event_count=event_count,
            plugin_identity=plugin_identity,
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
    requests: dict[object, PendingRequest],
    event_count: list[int],
    list_page_count: list[int],
    plugin_identity: dict[str, object],
) -> None:
    assert child.stdout is not None
    for line in child.stdout:
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        request_id = payload.get("id")
        with request_lock:
            request = requests.pop(request_id, None)
        if request is not None and request.method == "tools/list":
            list_page_count[0] += 1
            if list_page_count[0] > _MAX_LIST_PAGES:
                raise RuntimeError(f"startup proxy tools/list exceeded {_MAX_LIST_PAGES} pages")
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
                    "request_id": request_id,
                    "request_id_type": type(request_id).__name__,
                    "request_cursor": request.list_cursor,
                    "next_cursor": result.get("nextCursor") if isinstance(result, dict) else None,
                    "child_pid": request.child_pid,
                },
                event_count=event_count,
                plugin_identity=plugin_identity,
            )
        elif request is not None and request.method == "tools/call":
            result = payload.get("result")
            content = result.get("content", []) if isinstance(result, dict) else []
            texts = [
                item.get("text")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            recipe_segment_sha256: str | None = None
            for text in texts:
                try:
                    tool_payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(tool_payload, dict) and "recipe_segment" in tool_payload:
                    recipe_segment_sha256 = hashlib.sha256(
                        json.dumps(
                            tool_payload["recipe_segment"],
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest()
                    break
            if "error" in payload:
                outcome = "protocol_error"
            elif isinstance(result, dict) and result.get("isError") is True:
                outcome = "tool_error"
            else:
                outcome = "success"
            response_monotonic_ns = time.monotonic_ns()
            if isinstance(request.tool_name, str) and request.tool_name.endswith("open_kitchen"):
                event_name = "open_kitchen_result"
            elif isinstance(request.tool_name, str) and request.tool_name.endswith("run_skill"):
                event_name = "run_skill_result"
            else:
                event_name = "tool_call_result"
            _record(
                trace_path,
                write_lock,
                {
                    "event": event_name,
                    "tool_name": request.tool_name,
                    "outcome": outcome,
                    "is_error": outcome != "success",
                    "has_jsonrpc_error": outcome == "protocol_error",
                    "request_id": request_id,
                    "request_id_type": type(request_id).__name__,
                    "child_pid": request.child_pid,
                    "response_line_sha256": hashlib.sha256(line).hexdigest(),
                    "result_text_sha256": hashlib.sha256(
                        "".join(texts).encode("utf-8")
                    ).hexdigest(),
                    "recipe_segment_sha256": recipe_segment_sha256,
                    "request_monotonic_ns": request.monotonic_ns,
                    "response_monotonic_ns": response_monotonic_ns,
                    "elapsed_ns": response_monotonic_ns - request.monotonic_ns,
                    **response_measurements(texts),
                },
                event_count=event_count,
                plugin_identity=plugin_identity,
            )
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()


def run_proxy(
    trace_path: Path,
    delay_ms: int,
    executable: str,
    plugin_identity: dict[str, object],
) -> int:
    write_lock = threading.Lock()
    request_lock = threading.Lock()
    requests: dict[object, PendingRequest] = {}
    event_count = [0]
    list_page_count = [0]

    _record(
        trace_path,
        write_lock,
        {"event": "server_delay_started"},
        event_count=event_count,
        plugin_identity=plugin_identity,
    )
    time.sleep(delay_ms / 1000)
    _record(
        trace_path,
        write_lock,
        {"event": "server_exec_started"},
        event_count=event_count,
        plugin_identity=plugin_identity,
    )

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
            "event_count": event_count,
            "plugin_identity": plugin_identity,
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
        event_count=event_count,
        list_page_count=list_page_count,
        plugin_identity=plugin_identity,
    )
    return child.wait()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        config_path = Path(args[0])
    else:
        config_path = Path(sys.argv[0]).with_suffix(".json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return run_proxy(
        Path(config["trace_path"]),
        int(config["delay_ms"]),
        str(config["executable"]),
        dict(config["plugin_identity"]),
    )


if __name__ == "__main__":
    raise SystemExit(main())
