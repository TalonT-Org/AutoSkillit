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

from autoskillit.execution.otlp_sink import _SIGNALS as _OTLP_SIGNALS
from tests.execution.conftest import _flush

pytestmark = [pytest.mark.layer("execution"), pytest.mark.large]


_SIGNALS = tuple((signal, path) for path, signal in _OTLP_SIGNALS.items())
_FORBIDDEN = {
    "enduser.id": "private-enduser-id",
    "user.email": "private-email@example.test",
    "organization.id": "private-organization-id",
    "user.account_id": "private-account-id",
    "user.account_uuid": "private-account-uuid",
    "user.id": "private-user-id",
}
_FIXTURE_DIR = Path(__file__).with_name("fixtures")


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


def _native_fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURE_DIR / name).read_text())


def _native_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]


def _attribute(record: dict[str, Any], key: str) -> dict[str, Any]:
    matches = [item for item in record["attributes"] if item["key"] == key]
    assert len(matches) == 1
    return matches[0]


def _record_for(payload: dict[str, Any], event_name: str) -> dict[str, Any]:
    matches = [
        record
        for record in _native_records(payload)
        if _attribute(record, "event.name")["value"]["stringValue"] == event_name
    ]
    assert len(matches) == 1
    return matches[0]


def _set_string(record: dict[str, Any], key: str, value: str) -> None:
    _attribute(record, key)["value"] = {"stringValue": value}


def _remove_attribute(record: dict[str, Any], key: str) -> None:
    record["attributes"] = [item for item in record["attributes"] if item["key"] != key]


def _post_native_logs(sink: Any, payload: dict[str, Any]) -> None:
    status, _, response = _request(
        sink,
        "POST",
        "/v1/logs",
        json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
    )
    assert (status, response) == (200, {})


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


def test_sink_env_enables_native_claude_logs_and_metrics(local_sink: Any) -> None:
    base_url = local_sink.env["OTEL_EXPORTER_OTLP_ENDPOINT"]
    assert local_sink.env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert local_sink.env["OTEL_LOGS_EXPORTER"] == "otlp"
    assert local_sink.env["OTEL_METRICS_EXPORTER"] == "otlp"
    assert local_sink.env["OTEL_METRICS_INCLUDE_SESSION_ID"] == "true"
    assert local_sink.env["OTEL_METRICS_INCLUDE_ACCOUNT_UUID"] == "false"
    assert local_sink.env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/json"
    assert local_sink.env["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] == f"{base_url}/v1/logs"
    assert local_sink.env["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"] == (f"{base_url}/v1/metrics")
    assert local_sink.env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == (f"{base_url}/v1/traces")
    assert "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA" not in local_sink.env
    assert "ENABLE_ENHANCED_TELEMETRY_BETA" not in local_sink.env
    assert "OTEL_TRACES_EXPORTER" not in local_sink.env
    assert "OTEL_LOG_USER_PROMPTS" not in local_sink.env
    assert "OTEL_LOG_ASSISTANT_RESPONSES" not in local_sink.env
    assert "OTEL_LOG_RAW_API_BODIES" not in local_sink.env


@pytest.mark.parametrize(
    ("backend", "native_id_key", "scope_name", "event_name", "channel_b_capable"),
    (
        (
            "claude-code",
            "session.id",
            "com.anthropic.claude_code.events",
            "api_request",
            True,
        ),
        ("codex", "conversation.id", "codex_otel", "codex.sse_event", False),
    ),
)
def test_native_log_id_joins_authoritative_session_record(
    local_sink: Any,
    tmp_path: Path,
    backend: str,
    native_id_key: str,
    scope_name: str,
    event_name: str,
    channel_b_capable: bool,
) -> None:
    """Verify emitted native log IDs are the direct sessions.jsonl join key."""
    native_id = f"{backend}-native-session"
    _flush(
        tmp_path,
        backend=backend,
        session_id=native_id,
        channel_b_capable=channel_b_capable,
    )
    log_record = {
        "timeUnixNano": "0" if backend == "codex" else "1787770000000000000",
        "observedTimeUnixNano": "1787770000000000001",
        "attributes": [
            {"key": "event.name", "value": {"stringValue": event_name}},
            {"key": native_id_key, "value": {"stringValue": native_id}},
            {
                "key": "user.email",
                "value": {"stringValue": "private-email@example.test"},
            },
        ],
    }
    payload = {
        "resourceLogs": [
            {
                "resource": {"attributes": []},
                "scopeLogs": [
                    {
                        "scope": {"name": scope_name},
                        "logRecords": [log_record],
                    }
                ],
            }
        ]
    }

    status, _, response = _request(
        local_sink,
        "POST",
        "/v1/logs",
        json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
    )

    assert (status, response) == (200, {})
    records = _wait_for_records(tmp_path / "otlp.jsonl", 1)
    assert len(records) == 1
    persisted_scope = records[0]["payload"]["resourceLogs"][0]["scopeLogs"][0]
    persisted_record = persisted_scope["logRecords"][0]
    persisted_attributes = {
        attribute["key"]: attribute["value"]["stringValue"]
        for attribute in persisted_record["attributes"]
    }
    session_row = json.loads((tmp_path / "sessions.jsonl").read_text().splitlines()[0])

    assert persisted_scope["scope"]["name"] == scope_name
    assert persisted_attributes["event.name"] == event_name
    assert persisted_attributes[native_id_key] == session_row["session_id"] == native_id
    assert "user.email" not in persisted_attributes
    assert "private-email@example.test" not in json.dumps(records[0])
    if backend == "codex":
        assert persisted_attributes["event.name"].startswith("codex.")
        assert persisted_record["timeUnixNano"] == "0"
        assert persisted_record["observedTimeUnixNano"] == "1787770000000000001"
    else:
        assert "claude_code" in persisted_scope["scope"]["name"]


@pytest.mark.parametrize(
    ("fixture_name", "configured_alias", "native_id_key", "session_id", "resolved_model"),
    (
        (
            "claude_native_model_evidence.json",
            "sonnet",
            "session.id",
            "claude-session-sentinel",
            "claude-sonnet-5",
        ),
        (
            "claude_native_model_evidence.json",
            "opus[1m]",
            "session.id",
            "claude-session-sentinel",
            "claude-opus-5[1m]",
        ),
        (
            "codex_native_model_evidence.json",
            "opus",
            "conversation.id",
            "codex-conversation-sentinel",
            "gpt-5.6-sol",
        ),
    ),
)
def test_native_fixture_projects_verbatim_parent_and_fixture_proven_outcome(
    local_sink: Any,
    tmp_path: Path,
    fixture_name: str,
    configured_alias: str,
    native_id_key: str,
    session_id: str,
    resolved_model: str,
) -> None:
    payload = _native_fixture(fixture_name)
    parent_event = (
        "api_request" if fixture_name.startswith("claude") else "codex.conversation_starts"
    )
    parent_record = (
        _native_records(payload)[0]
        if parent_event == "api_request"
        else _record_for(payload, parent_event)
    )
    _set_string(parent_record, "model", resolved_model)
    if fixture_name.startswith("codex"):
        _set_string(parent_record, "slug", resolved_model)

    _post_native_logs(local_sink, payload)

    expected_outcomes: tuple[dict[str, Any], ...] = ()
    if fixture_name.startswith("claude"):
        expected_outcomes = (
            {
                "model": "claude-child-model-sentinel",
                "final_model": "claude-child-final-model-sentinel",
                "model_swapped": True,
            },
        )
    assert configured_alias != resolved_model
    assert local_sink.model_evidence_for(session_id) == (
        resolved_model,
        expected_outcomes,
    )
    assert local_sink.model_evidence_for("unrelated-session") == ("", ())
    persisted = _wait_for_records(tmp_path / "otlp.jsonl", 1)
    assert len(persisted) == 1
    persisted_records = _native_records(persisted[0]["payload"])
    assert _attribute(persisted_records[0], native_id_key)["value"]["stringValue"] == (session_id)


@pytest.mark.parametrize(
    ("fixture_name", "selected_key", "other_key", "selected_id"),
    (
        (
            "claude_native_model_evidence.json",
            "session.id",
            "conversation.id",
            "claude-session-sentinel",
        ),
        (
            "codex_native_model_evidence.json",
            "conversation.id",
            "session.id",
            "codex-conversation-sentinel",
        ),
    ),
)
def test_native_fixture_backend_key_wins_when_both_native_ids_are_present(
    local_sink: Any,
    fixture_name: str,
    selected_key: str,
    other_key: str,
    selected_id: str,
) -> None:
    payload = _native_fixture(fixture_name)
    for record in _native_records(payload):
        record["attributes"].append(
            {"key": other_key, "value": {"stringValue": "other-native-id"}}
        )

    _post_native_logs(local_sink, payload)

    parent, outcomes = local_sink.model_evidence_for(selected_id)
    assert parent.endswith("model-sentinel")
    if fixture_name.startswith("claude"):
        assert outcomes
    else:
        assert outcomes == ()
    assert local_sink.model_evidence_for("other-native-id") == ("", ())
    assert selected_key != other_key


@pytest.mark.parametrize(
    "malformation",
    (
        "missing_session",
        "empty_discriminator",
        "missing_discriminator",
        "duplicate_discriminator",
        "duplicate_session",
        "duplicate_model",
        "child_discriminator_on_parent",
        "unrecognized_event",
        "unrecognized_scope",
    ),
)
def test_malformed_or_ambiguous_claude_parent_is_raw_only(
    local_sink: Any, tmp_path: Path, malformation: str
) -> None:
    payload = _native_fixture("claude_native_model_evidence.json")
    record = _native_records(payload)[0]
    _native_records(payload)[:] = [record]
    if malformation == "missing_session":
        _remove_attribute(record, "session.id")
    elif malformation == "empty_discriminator":
        _set_string(record, "query_source", "")
    elif malformation == "missing_discriminator":
        _remove_attribute(record, "query_source")
    elif malformation == "duplicate_discriminator":
        record["attributes"].append({"key": "query_source", "value": {"stringValue": "sdk"}})
    elif malformation == "duplicate_session":
        record["attributes"].append(
            {"key": "session.id", "value": {"stringValue": "conflicting-session"}}
        )
    elif malformation == "duplicate_model":
        record["attributes"].append(
            {"key": "model", "value": {"stringValue": "conflicting-model"}}
        )
    elif malformation == "child_discriminator_on_parent":
        record["attributes"].append(
            {"key": "agent.name", "value": {"stringValue": "general-purpose"}}
        )
    elif malformation == "unrecognized_event":
        _set_string(record, "event.name", "assistant_response")
    else:
        payload["resourceLogs"][0]["scopeLogs"][0]["scope"]["name"] = "other.scope"

    _post_native_logs(local_sink, payload)

    assert local_sink.model_evidence_for("claude-session-sentinel") == ("", ())
    assert len(_wait_for_records(tmp_path / "otlp.jsonl", 1)) == 1


@pytest.mark.parametrize(
    "malformation",
    (
        "missing_session",
        "empty_discriminator",
        "duplicate_discriminator",
        "duplicate_session",
        "duplicate_model",
        "unrecognized_event",
        "unrecognized_scope",
    ),
)
def test_malformed_or_ambiguous_codex_parent_is_raw_only(
    local_sink: Any, tmp_path: Path, malformation: str
) -> None:
    payload = _native_fixture("codex_native_model_evidence.json")
    record = _record_for(payload, "codex.conversation_starts")
    if malformation == "missing_session":
        _remove_attribute(record, "conversation.id")
    elif malformation == "empty_discriminator":
        _set_string(record, "originator", "")
    elif malformation == "duplicate_discriminator":
        record["attributes"].append({"key": "originator", "value": {"stringValue": "codex_exec"}})
    elif malformation == "duplicate_session":
        record["attributes"].append(
            {"key": "conversation.id", "value": {"stringValue": "conflict"}}
        )
    elif malformation == "duplicate_model":
        record["attributes"].append({"key": "model", "value": {"stringValue": "conflict"}})
    elif malformation == "unrecognized_event":
        _set_string(record, "event.name", "codex.sse_event")
    else:
        payload["resourceLogs"][0]["scopeLogs"][0]["scope"]["name"] = "codex_otel"

    _post_native_logs(local_sink, payload)

    assert local_sink.model_evidence_for("codex-conversation-sentinel") == ("", ())
    assert len(_wait_for_records(tmp_path / "otlp.jsonl", 1)) == 1


@pytest.mark.parametrize(
    "malformation",
    (
        "missing_child_key",
        "missing_model",
        "missing_final_model",
        "missing_swapped",
        "duplicate_child_key",
        "duplicate_model",
        "duplicate_final_model",
        "duplicate_swapped",
        "wrong_swapped_type",
        "unrecognized_event",
    ),
)
def test_incomplete_or_ambiguous_completion_is_raw_only(
    local_sink: Any, tmp_path: Path, malformation: str
) -> None:
    payload = _native_fixture("claude_native_model_evidence.json")
    record = _record_for(payload, "subagent_completed")
    _native_records(payload)[:] = [record]
    field = {
        "missing_child_key": "agent_type",
        "missing_model": "model",
        "missing_final_model": "final_model",
        "missing_swapped": "model_swapped",
    }.get(malformation)
    if field is not None:
        _remove_attribute(record, field)
    elif malformation.startswith("duplicate_"):
        duplicate_key = {
            "duplicate_child_key": "agent_type",
            "duplicate_model": "model",
            "duplicate_final_model": "final_model",
            "duplicate_swapped": "model_swapped",
        }[malformation]
        record["attributes"].append(json.loads(json.dumps(_attribute(record, duplicate_key))))
    elif malformation == "wrong_swapped_type":
        _attribute(record, "model_swapped")["value"] = {"stringValue": "true"}
    else:
        _set_string(record, "event.name", "tool_result")

    _post_native_logs(local_sink, payload)

    assert local_sink.model_evidence_for("claude-session-sentinel") == ("", ())
    assert len(_wait_for_records(tmp_path / "otlp.jsonl", 1)) == 1


@pytest.mark.parametrize("path", ("/v1/metrics", "/v1/traces"))
def test_non_log_signals_remain_raw_without_model_projection(
    local_sink: Any, tmp_path: Path, path: str
) -> None:
    payload = _native_fixture("claude_native_model_evidence.json")
    status, _, response = _request(
        local_sink,
        "POST",
        path,
        json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
    )

    assert (status, response) == (200, {})
    assert local_sink.model_evidence_for("claude-session-sentinel") == ("", ())
    assert len(_wait_for_records(tmp_path / "otlp.jsonl", 1)) == 1


def test_model_evidence_lookup_states_and_post_close_snapshot(tmp_path: Path) -> None:
    from autoskillit.execution.otlp_sink import LocalOtlpSink

    assert LocalOtlpSink().model_evidence_for("never-started") == ("", ())

    outcomes_only = LocalOtlpSink.start(str(tmp_path / "outcomes-only"))
    outcomes_payload = _native_fixture("claude_native_model_evidence.json")
    completion = _record_for(outcomes_payload, "subagent_completed")
    _native_records(outcomes_payload)[:] = [completion]
    try:
        _post_native_logs(outcomes_only, outcomes_payload)
        assert outcomes_only.model_evidence_for("claude-session-sentinel") == (
            "",
            (
                {
                    "model": "claude-child-model-sentinel",
                    "final_model": "claude-child-final-model-sentinel",
                    "model_swapped": True,
                },
            ),
        )
    finally:
        outcomes_only.close()

    parent_only = LocalOtlpSink.start(str(tmp_path / "parent-only"))
    parent_payload = _native_fixture("codex_native_model_evidence.json")
    try:
        _post_native_logs(parent_only, parent_payload)
        assert parent_only.model_evidence_for("codex-conversation-sentinel") == (
            "codex-parent-model-sentinel",
            (),
        )
        parent_only.close()
        assert parent_only.model_evidence_for("codex-conversation-sentinel") == (
            "codex-parent-model-sentinel",
            (),
        )
    finally:
        parent_only.close()


def test_projection_capacity_retains_earliest_sessions_without_raw_drops(
    tmp_path: Path, monkeypatch
) -> None:
    import autoskillit.execution.otlp_sink as otlp_sink
    from autoskillit.execution.otlp_sink import LocalOtlpSink

    monkeypatch.setattr(otlp_sink, "_MODEL_EVIDENCE_SESSION_CAPACITY", 2)
    sink = LocalOtlpSink.start(str(tmp_path))
    try:
        for index in range(3):
            payload = _native_fixture("codex_native_model_evidence.json")
            record = _record_for(payload, "codex.conversation_starts")
            _set_string(record, "conversation.id", f"session-{index}")
            _set_string(record, "model", f"model-{index}")
            _set_string(record, "slug", f"model-{index}")
            _post_native_logs(sink, payload)
        later_parent = _native_fixture("codex_native_model_evidence.json")
        later_record = _record_for(later_parent, "codex.conversation_starts")
        _set_string(later_record, "conversation.id", "session-0")
        _set_string(later_record, "model", "later-model")
        _set_string(later_record, "slug", "later-model")
        _post_native_logs(sink, later_parent)

        assert sink.model_evidence_for("session-0") == ("model-0", ())
        assert sink.model_evidence_for("session-1") == ("model-1", ())
        assert sink.model_evidence_for("session-2") == ("", ())
        assert len(_wait_for_records(tmp_path / "otlp.jsonl", 4)) == 4
    finally:
        sink.close()


def test_outcome_capacity_retains_earliest_accepted_completions(
    tmp_path: Path, monkeypatch
) -> None:
    import autoskillit.execution.otlp_sink as otlp_sink
    from autoskillit.execution.otlp_sink import LocalOtlpSink

    monkeypatch.setattr(otlp_sink, "_MODEL_EVIDENCE_OUTCOME_CAPACITY", 2)
    sink = LocalOtlpSink.start(str(tmp_path))
    try:
        for index in range(3):
            payload = _native_fixture("claude_native_model_evidence.json")
            completion = _record_for(payload, "subagent_completed")
            _native_records(payload)[:] = [completion]
            _set_string(completion, "model", f"child-{index}")
            _set_string(completion, "final_model", f"final-{index}")
            _post_native_logs(sink, payload)

        assert sink.model_evidence_for("claude-session-sentinel") == (
            "",
            (
                {"model": "child-0", "final_model": "final-0", "model_swapped": True},
                {"model": "child-1", "final_model": "final-1", "model_swapped": True},
            ),
        )
        assert len(_wait_for_records(tmp_path / "otlp.jsonl", 3)) == 3
    finally:
        sink.close()


def test_queue_full_and_shutdown_rejections_do_not_project_observations(
    tmp_path: Path, monkeypatch
) -> None:
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
    try:
        assert sink._enqueue(b"first\n") == "accepted"
        assert writer_entered.wait(1)
        assert (
            sink._enqueue(b"second\n", (("accepted-session", "accepted-model", None),))
            == "accepted"
        )
        assert (
            sink._enqueue(b"third\n", (("queue-full-session", "not-retained", None),))
            == "queue_full"
        )
        assert sink.model_evidence_for("accepted-session") == ("accepted-model", ())
        assert sink.model_evidence_for("queue-full-session") == ("", ())
    finally:
        release_writer.set()
        sink.close()
    assert (
        sink._enqueue(b"after-close\n", (("shutdown-session", "not-retained", None),))
        == "shutdown"
    )
    assert sink.model_evidence_for("shutdown-session") == ("", ())


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
    ("raw_request", "expected_status"),
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
    local_sink: Any, tmp_path: Path, raw_request: bytes, expected_status: int
) -> None:
    status, content_type, response = _raw_request(local_sink, raw_request)

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
        observed_timeouts: list[float] = []

        def contend(_path: Path, *, timeout: float) -> None:
            observed_timeouts.append(timeout)
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
        assert observed_timeouts
        assert set(observed_timeouts) == {0.0}


def test_writer_loop_contains_unexpected_persistence_failure(monkeypatch) -> None:
    from autoskillit.execution.otlp_sink import _SENTINEL, LocalOtlpSink

    sink = LocalOtlpSink()
    persisted: list[bytes] = []

    def persist(line: bytes) -> None:
        if not persisted:
            persisted.append(line)
            raise TypeError("unexpected persistence failure")
        persisted.append(line)

    monkeypatch.setattr(sink, "_persist_line", persist)
    sink._queue.put_nowait(b'{"record":1}\n')
    sink._queue.put_nowait(b'{"record":2}\n')
    sink._queue.put_nowait(_SENTINEL)
    sink._writer_stop = True

    sink._writer_loop()

    assert persisted == [b'{"record":1}\n', b'{"record":2}\n']
    assert sink._counters["dropped_io"] == 1


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


@pytest.mark.parametrize("exception_type", (KeyboardInterrupt, SystemExit))
def test_close_propagates_process_interrupt(
    monkeypatch, exception_type: type[BaseException]
) -> None:
    from autoskillit.execution.otlp_sink import LocalOtlpSink

    sink = LocalOtlpSink()

    def interrupt() -> None:
        raise exception_type

    monkeypatch.setattr(sink, "_close", interrupt)

    with pytest.raises(exception_type):
        sink.close()


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
    assert disabled.model_evidence_for("failed-start") == ("", ())
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
    assert partial.model_evidence_for("partial-start") == ("", ())
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


@pytest.mark.parametrize("exception_type", (KeyboardInterrupt, SystemExit))
def test_start_propagates_process_interrupt_after_partial_cleanup(
    tmp_path: Path, monkeypatch, exception_type: type[BaseException]
) -> None:
    from autoskillit.execution.otlp_sink import LocalOtlpSink

    class InterruptingServer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise exception_type

    cleanup_calls: list[LocalOtlpSink] = []
    original_cleanup = LocalOtlpSink._disable_partial_start

    def record_cleanup(sink: LocalOtlpSink) -> None:
        cleanup_calls.append(sink)
        original_cleanup(sink)

    monkeypatch.setattr(LocalOtlpSink, "_server_class", InterruptingServer)
    monkeypatch.setattr(LocalOtlpSink, "_disable_partial_start", record_cleanup)

    with pytest.raises(exception_type):
        LocalOtlpSink.start(str(tmp_path))

    assert len(cleanup_calls) == 1
    assert cleanup_calls[0].env == {}


def test_partial_start_cleanup_propagates_process_interrupt() -> None:
    from autoskillit.execution.otlp_sink import LocalOtlpSink

    class InterruptingServer:
        def shutdown(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            raise AssertionError("server_close must not mask the interrupt")

    class AliveThread:
        @staticmethod
        def is_alive() -> bool:
            return True

    sink = LocalOtlpSink()
    sink._server = InterruptingServer()  # type: ignore[assignment]
    sink._server_thread = AliveThread()  # type: ignore[assignment]

    with pytest.raises(KeyboardInterrupt):
        sink._disable_partial_start()


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
