"""Integration tests for the loopback OTLP/HTTP diagnostic sink."""

from __future__ import annotations

import gzip
import json
import socket
import stat
import threading
import time
from collections.abc import Iterator
from http.client import HTTPConnection, HTTPResponse
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import structlog

pytestmark = [pytest.mark.layer("execution"), pytest.mark.large]


_SIGNALS = (
    ("metrics", "/v1/metrics"),
    ("logs", "/v1/logs"),
    ("traces", "/v1/traces"),
)
_FORBIDDEN = {
    "user.email": "private-email@example.test",
    "organization.id": "private-organization-id",
    "user.account_id": "private-account-id",
    "user.account_uuid": "private-account-uuid",
}


@pytest.fixture
def local_sink(tmp_path: Path) -> Iterator[Any]:
    from autoskillit.execution.otlp_sink import LocalOtlpSink

    sink = LocalOtlpSink.start(str(tmp_path))
    assert sink.env
    try:
        yield sink
    finally:
        sink.close()


def _connection(sink: Any) -> HTTPConnection:
    endpoint = urlsplit(sink.env["OTEL_EXPORTER_OTLP_ENDPOINT"])
    assert endpoint.hostname == "127.0.0.1"
    assert endpoint.port is not None
    return HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)


def _request(
    sink: Any,
    method: str,
    path: str,
    body: bytes,
    headers: dict[str, str] | None = None,
) -> tuple[int, str, dict[str, Any]]:
    connection = _connection(sink)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return (
            response.status,
            response.getheader("Content-Type", ""),
            json.loads(response.read()),
        )
    finally:
        connection.close()


def _raw_request(sink: Any, request: bytes) -> tuple[int, str, dict[str, Any]]:
    connection = _connection(sink)
    assert connection.host is not None
    assert connection.port is not None
    raw_socket = socket.create_connection((connection.host, connection.port), timeout=3)
    try:
        raw_socket.sendall(request)
        response = HTTPResponse(raw_socket)
        response.begin()
        return (
            response.status,
            response.getheader("Content-Type", ""),
            json.loads(response.read()),
        )
    finally:
        raw_socket.close()
        connection.close()


def _wait_for_records(log_path: Path, expected_count: int) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if log_path.exists():
            records = [json.loads(line) for line in log_path.read_text().splitlines()]
            if len(records) >= expected_count:
                return records
        time.sleep(0.01)
    return []


def _payload() -> dict[str, Any]:
    key_values = [
        {"key": key, "value": {"stringValue": value}} for key, value in _FORBIDDEN.items()
    ]
    return {
        "session": {"id": "session-join-key"},
        "direct": {
            **_FORBIDDEN,
            "nested": [{"organization.id": _FORBIDDEN["organization.id"]}],
        },
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": key_values
                    + [
                        {
                            "key": "array-container",
                            "value": {
                                "arrayValue": {
                                    "values": [
                                        {
                                            "kvlistValue": {
                                                "values": key_values,
                                            }
                                        }
                                    ]
                                }
                            },
                        },
                        {
                            "key": "kvlist-container",
                            "value": {
                                "kvlistValue": {
                                    "values": [
                                        {
                                            "key": "nested-array",
                                            "value": {
                                                "arrayValue": {
                                                    "values": [
                                                        {
                                                            "kvlistValue": {
                                                                "values": key_values,
                                                            }
                                                        }
                                                    ]
                                                }
                                            },
                                        }
                                    ]
                                }
                            },
                        },
                    ]
                }
            }
        ],
    }


@pytest.mark.parametrize(("signal", "path"), _SIGNALS)
def test_accepts_all_signals_and_redacts_nested_otlp_attributes(
    local_sink: Any, tmp_path: Path, signal: str, path: str
) -> None:
    payload = _payload()
    status, content_type, response = _request(
        local_sink,
        "POST",
        path,
        json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
    )

    assert status == 200
    assert content_type.split(";", 1)[0] == "application/json"
    assert response == {}
    records = _wait_for_records(tmp_path / "otlp.jsonl", 1)
    assert len(records) == 1
    assert set(records[0]) == {"signal", "payload"}
    assert records[0]["signal"] == signal
    serialized = json.dumps(records[0])
    assert records[0]["payload"]["session"]["id"] == "session-join-key"
    for key, value in _FORBIDDEN.items():
        assert key not in serialized
        assert value not in serialized


def test_accepts_bounded_gzip_json_and_sanitizes_it(local_sink: Any, tmp_path: Path) -> None:
    body = gzip.compress(json.dumps(_payload()).encode())
    status, content_type, response = _request(
        local_sink,
        "POST",
        "/v1/traces",
        body,
        {"Content-Type": "application/json; charset=utf-8", "Content-Encoding": "gzip"},
    )

    assert (status, content_type.split(";", 1)[0], response) == (200, "application/json", {})
    records = _wait_for_records(tmp_path / "otlp.jsonl", 1)
    assert len(records) == 1
    assert _FORBIDDEN["user.email"] not in json.dumps(records[0])


@pytest.mark.parametrize(
    ("request", "expected_status"),
    (
        (
            b"POST /v1/metrics HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Type: application/json\r\nContent-Length: 1\r\n\r\n{",
            400,
        ),
        (
            b"POST /v1/metrics HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
            400,
        ),
        (
            b"POST /v1/metrics HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Type: application/json\r\nContent-Length: nope\r\n\r\n",
            400,
        ),
        (
            b"POST /v1/metrics HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 1\r\nContent-Length: 1\r\n\r\n{}",
            400,
        ),
        (
            b"POST /v1/metrics HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Type: application/json\r\n\r\n{}",
            411,
        ),
        (
            b"PUT /v1/metrics HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n",
            405,
        ),
        (
            b"POST /wrong HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Type: application/json\r\nContent-Length: 2\r\n\r\n{}",
            404,
        ),
        (
            b"POST /v1/metrics HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n\r\n{}",
            415,
        ),
        (
            b"POST /v1/metrics HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Type: application/json; charset=latin-1\r\nContent-Length: 2\r\n\r\n{}",
            415,
        ),
    ),
)
def test_protocol_errors_return_status_without_persisting(
    local_sink: Any, tmp_path: Path, request: bytes, expected_status: int
) -> None:
    status, content_type, response = _raw_request(local_sink, request)

    assert status == expected_status
    assert content_type.split(";", 1)[0] == "application/json"
    assert response["message"]
    assert response["details"] == []
    assert not (tmp_path / "otlp.jsonl").exists()

    status, _content_type, response = _request(
        local_sink,
        "POST",
        "/v1/logs",
        b"{}",
        {"Content-Type": "application/json"},
    )
    assert (status, response) == (200, {})


def test_request_size_limits_return_status_without_partial_record(
    local_sink: Any, tmp_path: Path
) -> None:
    encoded_too_large = (
        b"POST /v1/metrics HTTP/1.1\r\nHost: localhost\r\n"
        b"Content-Type: application/json\r\nContent-Length: 20971521\r\n\r\n"
    )
    status, content_type, response = _raw_request(local_sink, encoded_too_large)
    assert status == 413
    assert content_type.split(";", 1)[0] == "application/json"
    assert response["message"]
    assert response["details"] == []
    assert not (tmp_path / "otlp.jsonl").exists()

    gzip_bomb = gzip.compress(b"x" * (20 * 1024 * 1024 + 1))
    status, content_type, response = _request(
        local_sink,
        "POST",
        "/v1/metrics",
        gzip_bomb,
        {"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert status == 413
    assert content_type.split(";", 1)[0] == "application/json"
    assert response["message"]
    assert response["details"] == []
    assert not (tmp_path / "otlp.jsonl").exists()


def test_close_is_idempotent_and_releases_listener_port(local_sink: Any) -> None:
    endpoint = urlsplit(local_sink.env["OTEL_EXPORTER_OTLP_ENDPOINT"])
    assert endpoint.hostname == "127.0.0.1"
    assert endpoint.port is not None

    local_sink.close()
    local_sink.close()

    rebound = socket.socket()
    try:
        rebound.bind((endpoint.hostname, endpoint.port))
    finally:
        rebound.close()


def test_persisted_jsonl_is_user_only_and_contains_complete_records(
    local_sink: Any, tmp_path: Path
) -> None:
    for _signal, path in _SIGNALS:
        status, _content_type, response = _request(
            local_sink,
            "POST",
            path,
            b"{}",
            {"Content-Type": "application/json"},
        )
        assert (status, response) == (200, {})

    log_path = tmp_path / "otlp.jsonl"
    records = _wait_for_records(log_path, len(_SIGNALS))
    assert len(records) == len(_SIGNALS)
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600


def test_tiny_generation_cap_rotates_complete_jsonl_records(tmp_path: Path, monkeypatch) -> None:
    import autoskillit.execution.otlp_sink as otlp_sink
    from autoskillit.execution.otlp_sink import LocalOtlpSink

    monkeypatch.setattr(otlp_sink, "_MAX_GENERATION_BYTES", 30)
    sink = LocalOtlpSink.start(str(tmp_path))
    first = b'{"record":"first"}\n'
    second = b'{"record":"second"}\n'
    try:
        sink._persist_line(first)
        sink._persist_line(second)

        active = tmp_path / "otlp.jsonl"
        archive = tmp_path / "otlp.jsonl.1"
        assert active.read_bytes() == second
        assert archive.read_bytes() == first
        for path in (active, archive):
            assert len(path.read_bytes()) <= 30
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert [json.loads(line) for line in path.read_text().splitlines()]
    finally:
        sink.close()


def test_interrupted_tiny_generation_rotation_recovers_on_restart(
    tmp_path: Path, monkeypatch
) -> None:
    import autoskillit.execution.otlp_sink as otlp_sink
    from autoskillit.execution.otlp_sink import LocalOtlpSink

    monkeypatch.setattr(otlp_sink, "_MAX_GENERATION_BYTES", 30)
    first = b'{"record":"first"}\n'
    second = b'{"record":"second"}\n'
    third = b'{"record":"third"}\n'
    sink = LocalOtlpSink.start(str(tmp_path))
    original_atomic_write = otlp_sink.atomic_write

    def fail_active_replace(path: Path, content: bytes, **kwargs: object) -> None:
        if path == tmp_path / "otlp.jsonl" and content == second:
            raise OSError("interrupted active-generation replace")
        original_atomic_write(path, content, **kwargs)

    try:
        sink._persist_line(first)
        monkeypatch.setattr(otlp_sink, "atomic_write", fail_active_replace)
        sink._persist_line(second)
        assert (tmp_path / "otlp.jsonl").read_bytes() == first
        assert (tmp_path / "otlp.jsonl.1").read_bytes() == first
    finally:
        sink.close()

    monkeypatch.setattr(otlp_sink, "atomic_write", original_atomic_write)
    recovered_sink = LocalOtlpSink.start(str(tmp_path))
    try:
        recovered_sink._persist_line(third)
        assert (tmp_path / "otlp.jsonl.1").read_bytes() == first
        assert (tmp_path / "otlp.jsonl").read_bytes() == third
    finally:
        recovered_sink.close()


@pytest.mark.parametrize(
    ("failure", "counter"),
    (("lease", "dropped_lock_contention"), ("write", "dropped_io")),
)
def test_persistence_failures_drop_records_without_stopping_http_server(
    local_sink: Any, monkeypatch, failure: str, counter: str
) -> None:
    import autoskillit.execution.otlp_sink as otlp_sink
    from autoskillit.core import ArtifactLeaseContention

    if failure == "lease":
        observed_blocking: list[bool] = []

        def contend(_path: Path, *, blocking: bool) -> None:
            observed_blocking.append(blocking)
            raise ArtifactLeaseContention("test lease contention")

        monkeypatch.setattr(otlp_sink.ArtifactLease, "acquire_exclusive", contend)
    else:

        def fail_write(_path: Path, _content: bytes, **_kwargs: object) -> None:
            raise OSError("test write failure")

        monkeypatch.setattr(otlp_sink, "atomic_write", fail_write)

    local_sink._persist_line(b'{"record":"dropped"}\n')
    status, _content_type, response = _request(
        local_sink,
        "POST",
        "/v1/logs",
        b"{}",
        {"Content-Type": "application/json"},
    )

    assert (status, response) == (200, {})
    assert local_sink._counters[counter] >= 1
    if failure == "lease":
        assert observed_blocking
        assert set(observed_blocking) == {False}


def test_close_logs_one_aggregate_summary_without_payload_data(tmp_path: Path) -> None:
    from autoskillit.execution.otlp_sink import LocalOtlpSink

    sink = LocalOtlpSink.start(str(tmp_path))
    secret = "raw-payload-value-that-must-not-be-logged"
    try:
        with structlog.testing.capture_logs() as caplog:
            assert sink._enqueue(f'{{"payload":"{secret}"}}\n'.encode()) == "accepted"
            assert _wait_for_records(tmp_path / "otlp.jsonl", 1)
            sink.close()
            sink.close()
    finally:
        sink.close()

    summaries = [entry for entry in caplog if entry.get("event") == "local_otlp_sink_closed"]
    assert len(summaries) == 1
    assert all(name in summaries[0] for name in sink._counters)
    assert summaries[0]["received"] == 1
    assert secret not in json.dumps(summaries[0])


def test_startup_failure_returns_disabled_sink_and_partial_start_releases_port(
    tmp_path: Path, monkeypatch
) -> None:
    import autoskillit.execution.otlp_sink as otlp_sink
    from autoskillit.execution.otlp_sink import LocalOtlpSink, _OtlpHTTPServer

    class FailingServer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise OSError("test bind failure")

    monkeypatch.setattr(LocalOtlpSink, "_server_class", FailingServer)
    disabled = LocalOtlpSink.start(str(tmp_path / "bind-failure"))
    assert disabled.env == {}
    disabled.close()

    created_servers: list[_OtlpHTTPServer] = []

    class RecordingServer(_OtlpHTTPServer):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            created_servers.append(self)

    start_calls = 0
    original_start = threading.Thread.start

    def fail_second_thread_start(thread: threading.Thread) -> None:
        nonlocal start_calls
        start_calls += 1
        if start_calls == 2:
            raise RuntimeError("test server-thread start failure")
        original_start(thread)

    monkeypatch.setattr(LocalOtlpSink, "_server_class", RecordingServer)
    monkeypatch.setattr(otlp_sink.threading.Thread, "start", fail_second_thread_start)
    partial = LocalOtlpSink.start(str(tmp_path / "partial-start"))
    assert partial.env == {}
    assert created_servers
    assert partial._writer_thread is not None
    assert not partial._writer_thread.is_alive()

    host, port = created_servers[0].server_address[:2]
    rebound = socket.socket()
    try:
        rebound.bind((host, port))
    finally:
        rebound.close()
    partial.close()


def test_close_rejects_handler_that_reaches_enqueue_after_shutdown_gate(
    tmp_path: Path, monkeypatch
) -> None:
    import autoskillit.execution.otlp_sink as otlp_sink
    from autoskillit.execution.otlp_sink import LocalOtlpSink

    monkeypatch.setattr(otlp_sink, "_HANDLER_DRAIN_SECONDS", 0.01)
    monkeypatch.setattr(otlp_sink, "_THREAD_JOIN_SECONDS", 0.5)
    sink = LocalOtlpSink.start(str(tmp_path))
    entered_enqueue = threading.Event()
    release_enqueue = threading.Event()
    close_finished = threading.Event()
    response: list[tuple[int, str, dict[str, Any]]] = []
    original_enqueue = sink._enqueue

    def delayed_enqueue(line: bytes) -> str:
        entered_enqueue.set()
        assert release_enqueue.wait(2)
        return original_enqueue(line)

    def post_request() -> None:
        response.append(
            _request(
                sink,
                "POST",
                "/v1/logs",
                b"{}",
                {"Content-Type": "application/json"},
            )
        )

    def close_sink() -> None:
        sink.close()
        close_finished.set()

    request_thread = threading.Thread(target=post_request)
    close_thread = threading.Thread(target=close_sink)
    monkeypatch.setattr(sink, "_enqueue", delayed_enqueue)
    assert sink._server is not None
    monkeypatch.setattr(
        sink._server,
        "shutdown",
        lambda: setattr(sink._server, "_BaseServer__shutdown_request", True),
    )
    try:
        request_thread.start()
        assert entered_enqueue.wait(1)
        close_thread.start()
        assert close_finished.wait(1)
        with sink._condition:
            assert not sink._enqueue_allowed
        release_enqueue.set()
        request_thread.join(2)
        close_thread.join(2)
        assert not request_thread.is_alive()
        assert not close_thread.is_alive()
        assert response[0][0] == 503
    finally:
        release_enqueue.set()
        sink.close()
        request_thread.join(2)
        close_thread.join(2)


def test_close_terminates_when_the_bounded_queue_is_full(tmp_path: Path, monkeypatch) -> None:
    import autoskillit.execution.otlp_sink as otlp_sink
    from autoskillit.execution.otlp_sink import LocalOtlpSink

    monkeypatch.setattr(otlp_sink, "_QUEUE_CAPACITY", 1)
    writer_entered = threading.Event()
    release_writer = threading.Event()

    def pause_writer(_sink: LocalOtlpSink, _line: bytes) -> None:
        writer_entered.set()
        assert release_writer.wait(2)

    monkeypatch.setattr(LocalOtlpSink, "_persist_line", pause_writer)
    sink = LocalOtlpSink.start(str(tmp_path))
    close_thread = threading.Thread(target=sink.close)
    try:
        assert sink._enqueue(b"first\n") == "accepted"
        assert writer_entered.wait(1)
        assert sink._enqueue(b"second\n") == "accepted"
        assert sink._enqueue(b"third\n") == "queue_full"
        close_thread.start()
        release_writer.set()
        close_thread.join(3)
        assert not close_thread.is_alive()
        assert sink._writer_thread is not None
        assert not sink._writer_thread.is_alive()
    finally:
        release_writer.set()
        sink.close()
        close_thread.join(3)
