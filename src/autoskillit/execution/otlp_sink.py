"""Run-scoped loopback OTLP/HTTP-JSON diagnostic receiver."""

from __future__ import annotations

import json
import queue
import threading
import time
import zlib
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, cast

from autoskillit.core import (
    ArtifactLease,
    ArtifactLeaseContention,
    atomic_write,
    get_logger,
)
from autoskillit.execution.session_log import resolve_log_dir

logger = get_logger(__name__)

_MAX_ENCODED_REQUEST_BYTES = 20 * 1024 * 1024
_MAX_DECODED_REQUEST_BYTES = 20 * 1024 * 1024
_MAX_GENERATION_BYTES = 20 * 1024 * 1024
_QUEUE_CAPACITY = 128
_HANDLER_DRAIN_SECONDS = 1.0
_THREAD_JOIN_SECONDS = 2.0
_WRITER_POLL_SECONDS = 0.05
_SENTINEL = object()

_SIGNALS = {
    "/v1/metrics": "metrics",
    "/v1/logs": "logs",
    "/v1/traces": "traces",
}
_FORBIDDEN_KEYS = frozenset(
    {
        "enduser.id",
        "user.email",
        "organization.id",
        "user.account_id",
        "user.account_uuid",
        "user.id",
    }
)
_COUNTER_NAMES = (
    "received",
    "persisted",
    "dropped_queue_full",
    "dropped_shutdown",
    "dropped_lock_contention",
    "dropped_io",
    "rotations",
)


class _PayloadTooLarge(ValueError):
    pass


class _MalformedBody(ValueError):
    pass


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items() if key not in _FORBIDDEN_KEYS}
    if isinstance(value, list):
        return [
            _sanitize(item)
            for item in value
            if not (isinstance(item, dict) and item.get("key") in _FORBIDDEN_KEYS)
        ]
    return value


def _decode_gzip_bounded(body: bytes) -> bytes:
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    decoded = bytearray()
    try:
        for offset in range(0, len(body), 64 * 1024):
            chunk = body[offset : offset + 64 * 1024]
            while chunk:
                remaining = _MAX_DECODED_REQUEST_BYTES + 1 - len(decoded)
                decoded.extend(decoder.decompress(chunk, remaining))
                if len(decoded) > _MAX_DECODED_REQUEST_BYTES:
                    raise _PayloadTooLarge
                chunk = decoder.unconsumed_tail
        remaining = _MAX_DECODED_REQUEST_BYTES + 1 - len(decoded)
        decoded.extend(decoder.flush(remaining))
    except zlib.error as exc:
        raise _MalformedBody from exc
    if len(decoded) > _MAX_DECODED_REQUEST_BYTES:
        raise _PayloadTooLarge
    if not decoder.eof or decoder.unused_data:
        raise _MalformedBody
    return bytes(decoded)


def _build_env(base_url: str) -> dict[str, str]:
    env = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_METRICS_INCLUDE_SESSION_ID": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": base_url,
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"{base_url}/v1/traces",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": f"{base_url}/v1/metrics",
        "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": "http/json",
        "OTEL_METRICS_INCLUDE_ACCOUNT_UUID": "false",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": f"{base_url}/v1/logs",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json",
    }
    for prefix in (
        "OTEL_EXPORTER_OTLP",
        "OTEL_EXPORTER_OTLP_TRACES",
        "OTEL_EXPORTER_OTLP_METRICS",
        "OTEL_EXPORTER_OTLP_LOGS",
    ):
        env.update(
            {
                f"{prefix}_HEADERS": "",
                f"{prefix}_CERTIFICATE": "",
                f"{prefix}_COMPRESSION": "none",
                f"{prefix}_TIMEOUT": "",
            }
        )
    return env


class _OtlpHTTPServer(ThreadingHTTPServer):
    sink: LocalOtlpSink


class _OtlpHandler(BaseHTTPRequestHandler):
    server: _OtlpHTTPServer

    def log_message(self, _format: str, *args: object) -> None:
        return None

    def do_POST(self) -> None:  # noqa: N802
        sink = self.server.sink
        sink._handler_enter()
        try:
            self._handle_post(sink)
        except Exception:
            logger.debug("local_otlp_sink_handler_failed", exc_info=True)
            self._send_status(500, "Internal OTLP receiver error")
        finally:
            sink._handler_exit()

    def do_GET(self) -> None:  # noqa: N802
        self._wrong_method()

    def do_PUT(self) -> None:  # noqa: N802
        self._wrong_method()

    def do_PATCH(self) -> None:  # noqa: N802
        self._wrong_method()

    def do_DELETE(self) -> None:  # noqa: N802
        self._wrong_method()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._wrong_method()

    def do_HEAD(self) -> None:  # noqa: N802
        self._wrong_method()

    def _wrong_method(self) -> None:
        self._send_status(405, "Only POST is supported", extra_headers={"Allow": "POST"})

    def _handle_post(self, sink: LocalOtlpSink) -> None:
        signal = _SIGNALS.get(self.path)
        if signal is None:
            self._send_status(404, "Unsupported OTLP signal path")
            return

        if self.headers.get_all("Transfer-Encoding"):
            self._send_status(400, "Transfer-Encoding is not supported")
            return
        lengths = self.headers.get_all("Content-Length")
        if lengths is None:
            self._send_status(411, "Content-Length is required")
            return
        if len(lengths) != 1:
            self._send_status(400, "Content-Length must be specified exactly once")
            return
        length_text = lengths[0].strip()
        if not length_text.isdigit():
            self._send_status(400, "Content-Length must be a non-negative integer")
            return
        content_length = int(length_text)
        if content_length > _MAX_ENCODED_REQUEST_BYTES:
            self._send_status(413, "Encoded OTLP request exceeds the size limit")
            return

        content_types = self.headers.get_all("Content-Type")
        if content_types is None or len(content_types) != 1:
            self._send_status(415, "Content-Type must be application/json")
            return
        if self.headers.get_content_type().lower() != "application/json":
            self._send_status(415, "Content-Type must be application/json")
            return
        params = self.headers.get_params(header="content-type", failobj=[])[1:]
        charset_values = [value for key, value in params if key.lower() == "charset"]
        if any(key.lower() != "charset" for key, _value in params) or len(charset_values) > 1:
            self._send_status(415, "Only a UTF-8 charset is supported")
            return
        if charset_values and charset_values[0].lower() not in {"utf-8", "utf8"}:
            self._send_status(415, "Only a UTF-8 charset is supported")
            return

        encodings = self.headers.get_all("Content-Encoding")
        if encodings is not None and len(encodings) != 1:
            self._send_status(415, "Unsupported Content-Encoding")
            return
        content_encoding = encodings[0].strip().lower() if encodings else "identity"
        if content_encoding not in {"identity", "gzip"}:
            self._send_status(415, "Unsupported Content-Encoding")
            return

        body = self.rfile.read(content_length)
        if len(body) != content_length:
            self._send_status(400, "Request body ended before Content-Length bytes")
            return
        try:
            if content_encoding == "gzip":
                body = _decode_gzip_bounded(body)
            elif len(body) > _MAX_DECODED_REQUEST_BYTES:
                raise _PayloadTooLarge
        except _PayloadTooLarge:
            self._send_status(413, "Decoded OTLP request exceeds the size limit")
            return
        except _MalformedBody:
            self._send_status(400, "Malformed gzip request body")
            return

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_status(400, "Malformed UTF-8 JSON request body")
            return

        line = (
            json.dumps(
                {"signal": signal, "payload": _sanitize(payload)},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        enqueue_status = sink._enqueue(line)
        if enqueue_status == "queue_full":
            self._send_status(503, "OTLP receiver queue is full")
            return
        if enqueue_status == "shutdown":
            self._send_status(503, "OTLP receiver is shutting down")
            return
        self._send_json(200, b"{}")

    def _send_status(
        self,
        status: int,
        message: str,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        body = json.dumps({"message": message, "details": []}, separators=(",", ":")).encode(
            "utf-8"
        )
        self._send_json(status, body, extra_headers=extra_headers)

    def _send_json(
        self,
        status: int,
        body: bytes,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except OSError:
            return


class LocalOtlpSink:
    """Own one bounded loopback receiver and its asynchronous JSONL writer."""

    _server_class: ClassVar[type[_OtlpHTTPServer]] = _OtlpHTTPServer

    def __init__(self) -> None:
        self.env: dict[str, str] = {}
        self._active_path: Path | None = None
        self._archive_path: Path | None = None
        self._lock_path: Path | None = None
        self._server: _OtlpHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._writer_thread: threading.Thread | None = None
        self._queue: queue.Queue[bytes | object] = queue.Queue(maxsize=_QUEUE_CAPACITY)
        self._condition = threading.Condition()
        self._closing = False
        self._closed = False
        self._enabled = False
        self._active_handlers = 0
        self._enqueue_allowed = False
        self._writer_stop = False
        self._sentinel_inserted = False
        self._counters = {name: 0 for name in _COUNTER_NAMES}

    @classmethod
    def start(cls, log_dir: str) -> LocalOtlpSink:
        """Start a sink or return a disabled, safely closable sink on failure."""
        sink = cls()
        try:
            log_root = resolve_log_dir(log_dir)
            log_root.mkdir(parents=True, exist_ok=True)
            locks_dir = log_root / ".locks"
            locks_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            locks_dir.chmod(0o700)

            sink._active_path = log_root / "otlp.jsonl"
            sink._archive_path = log_root / "otlp.jsonl.1"
            sink._lock_path = locks_dir / "otlp-sink.lock"
            server = cls._server_class(("127.0.0.1", 0), _OtlpHandler)
            server.sink = sink
            sink._server = server
            sink._writer_thread = threading.Thread(
                target=sink._writer_loop,
                name="autoskillit-otlp-writer",
                daemon=True,
            )
            sink._server_thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": _WRITER_POLL_SECONDS},
                name="autoskillit-otlp-server",
                daemon=True,
            )
            sink._writer_thread.start()
            sink._server_thread.start()
            host, port = cast(tuple[str, int], server.server_address[:2])
            sink.env = _build_env(f"http://{host}:{port}")
            with sink._condition:
                sink._enqueue_allowed = True
                sink._enabled = True
            return sink
        except Exception:
            logger.debug("local_otlp_sink_start_failed", exc_info=True)
            sink._disable_partial_start()
            return sink
        except BaseException:
            sink._disable_partial_start()
            raise

    def _disable_partial_start(self) -> None:
        self.env = {}
        with self._condition:
            self._enqueue_allowed = False
            self._writer_stop = True
            self._closing = True
            self._closed = True
            try:
                self._queue.put_nowait(_SENTINEL)
                self._sentinel_inserted = True
            except queue.Full:
                pass
            self._condition.notify_all()
        server = self._server
        if server is not None:
            try:
                if self._server_thread is not None and self._server_thread.is_alive():
                    server.shutdown()
            except Exception:
                logger.debug("local_otlp_sink_partial_shutdown_failed", exc_info=True)
            try:
                server.server_close()
            except Exception:
                logger.debug("local_otlp_sink_partial_server_close_failed", exc_info=True)
        for thread in (self._server_thread, self._writer_thread):
            if thread is not None and thread.is_alive():
                try:
                    thread.join(_THREAD_JOIN_SECONDS)
                except Exception:
                    logger.debug("local_otlp_sink_partial_thread_join_failed", exc_info=True)

    def _handler_enter(self) -> None:
        with self._condition:
            self._active_handlers += 1

    def _handler_exit(self) -> None:
        with self._condition:
            self._active_handlers -= 1
            self._condition.notify_all()

    def _enqueue(self, line: bytes) -> str:
        with self._condition:
            self._counters["received"] += 1
            if not self._enqueue_allowed:
                self._counters["dropped_shutdown"] += 1
                return "shutdown"
            try:
                self._queue.put_nowait(line)
            except queue.Full:
                self._counters["dropped_queue_full"] += 1
                return "queue_full"
            return "accepted"

    def _increment(self, counter: str) -> None:
        with self._condition:
            self._counters[counter] += 1

    def _writer_loop(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=_WRITER_POLL_SECONDS)
            except queue.Empty:
                with self._condition:
                    if self._writer_stop and self._queue.empty():
                        return
                continue
            if item is not _SENTINEL:
                assert isinstance(item, bytes)
                try:
                    self._persist_line(item)
                except Exception:
                    logger.debug("local_otlp_sink_persist_failed", exc_info=True)
                    self._increment("dropped_io")
            with self._condition:
                if self._writer_stop and self._queue.empty():
                    return

    def _persist_line(self, line: bytes) -> None:
        """Persist one complete record while holding the cross-process lease."""
        active_path = self._active_path
        archive_path = self._archive_path
        lock_path = self._lock_path
        if active_path is None or archive_path is None or lock_path is None:
            self._increment("dropped_io")
            return
        if len(line) > _MAX_GENERATION_BYTES:
            self._increment("dropped_io")
            return
        try:
            with ArtifactLease.acquire_exclusive(lock_path, timeout=0.0):
                try:
                    with active_path.open("rb") as active_file:
                        active_bytes = active_file.read(_MAX_GENERATION_BYTES + 1)
                except FileNotFoundError:
                    active_bytes = b""
                if len(active_bytes) > _MAX_GENERATION_BYTES:
                    raise OSError("active OTLP generation exceeds its size cap")
                if len(active_bytes) + len(line) > _MAX_GENERATION_BYTES:
                    atomic_write(archive_path, active_bytes)
                    archive_path.chmod(0o600)
                    atomic_write(active_path, line)
                    active_path.chmod(0o600)
                    self._increment("rotations")
                else:
                    atomic_write(active_path, active_bytes + line)
                    active_path.chmod(0o600)
                self._increment("persisted")
        except ArtifactLeaseContention:
            self._increment("dropped_lock_contention")
        except (OSError, RuntimeError, ValueError):
            self._increment("dropped_io")

    def close(self) -> None:
        """Stop accepting work, drain bounded in-flight work, and release the port."""
        try:
            self._close()
        except Exception:
            logger.debug("local_otlp_sink_close_failed", exc_info=True)

    def _close(self) -> None:
        with self._condition:
            if not self._enabled or self._closed:
                return
            if self._closing:
                self._condition.wait_for(lambda: self._closed, timeout=_THREAD_JOIN_SECONDS)
                return
            self._closing = True
            server = self._server
            server_thread = self._server_thread
            writer_thread = self._writer_thread

        if server is not None:
            if server_thread is not None and server_thread.is_alive():
                server.shutdown()
            server.server_close()

        deadline = time.monotonic() + _HANDLER_DRAIN_SECONDS
        with self._condition:
            while self._active_handlers and time.monotonic() < deadline:
                self._condition.wait(timeout=max(0.0, deadline - time.monotonic()))
            self._enqueue_allowed = False
            self._writer_stop = True
            if not self._sentinel_inserted:
                try:
                    self._queue.put_nowait(_SENTINEL)
                    self._sentinel_inserted = True
                except queue.Full:
                    pass
            self._condition.notify_all()

        if server_thread is not None and server_thread.is_alive():
            server_thread.join(_THREAD_JOIN_SECONDS)
        if writer_thread is not None and writer_thread.is_alive():
            writer_thread.join(_THREAD_JOIN_SECONDS)

        with self._condition:
            self._closed = True
            self._enabled = False
            counters = dict(self._counters)
            self._condition.notify_all()
        logger.debug("local_otlp_sink_closed", **counters)
