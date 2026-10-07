"""Focused tests for native parent-visible child text spans."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autoskillit.execution.evidence import _native_child_projection as projection
from autoskillit.execution.evidence import _native_parent_context as parent_context

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _jsonl(*records: dict[str, Any]) -> str:
    return "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records)


def _encoding(name: str, *, merge_ab: bool = False) -> Any:
    import tiktoken

    ranks = {bytes((value,)): value for value in range(256)}
    if merge_ab:
        ranks[b"ab"] = 256
    return tiktoken.Encoding(
        name=name,
        pat_str=r"[\s\S]+",
        mergeable_ranks=ranks,
        special_tokens={"<|endoftext|>": 257 if merge_ab else 256},
    )


def _spans(
    transcript: str,
    *,
    child_id: str,
    backend: str = "claude_code",
    provider: str = "openai",
) -> list[dict[str, Any]]:
    row = {"backend": backend.replace("_", "-"), "provider_used": provider}
    return parent_context.parent_context_spans(
        row,
        child_id,
        backend,
        parent_context.parent_transcript_records(transcript, backend),
        parent_complete=projection._complete_jsonl(transcript),
        parent_transcript_reason=None,
        tokenizer_version="0.14.0",
    )


def _claude_call(
    *,
    child_id: str | None,
    tool_id: str,
    prompt: str,
    model: str = "gpt-known",
) -> dict[str, Any]:
    inputs: dict[str, str] = {"prompt": prompt}
    if child_id is not None:
        inputs["agent_id"] = child_id
    return {
        "type": "assistant",
        "uuid": f"record-{tool_id}",
        "timestamp": "2026-10-07T10:00:00Z",
        "requestId": f"turn-{tool_id}",
        "message": {
            "id": f"message-{tool_id}",
            "model": model,
            "content": [{"type": "tool_use", "id": tool_id, "name": "Task", "input": inputs}],
        },
    }


def _claude_result(*, tool_id: str, record_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": record_id,
        "timestamp": f"2026-10-07T10:00:0{record_id[-1]}Z",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": [{"type": "text", "text": text}],
                }
            ]
        },
    }


def test_claude_spans_count_exact_linked_text_deduplicate_replay_and_keep_deliveries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tiktoken

    encoding = _encoding("local-byte-merge", merge_ab=True)
    monkeypatch.setattr(tiktoken, "encoding_for_model", lambda _model: encoding)
    prompt = "<|endoftext|>ab"
    call = _claude_call(child_id="child-1", tool_id="tool-1", prompt=prompt)
    transcript = _jsonl(
        call,
        call,
        _claude_result(tool_id="tool-1", record_id="result-1", text="ab"),
        _claude_result(tool_id="tool-1", record_id="result-2", text="<|endoftext|>"),
    )

    spans = _spans(transcript, child_id="child-1")

    assert [span["field"] for span in spans] == [
        "parent_prompt_tokens",
        "subagent_return_tokens",
        "subagent_return_tokens",
    ]
    assert spans[0]["measure"] == {
        "state": "measured",
        "value": len(encoding.encode_ordinary(prompt)),
    }
    assert spans[1]["measure"] == {"state": "measured", "value": 1}
    assert spans[2]["measure"]["value"] == len(encoding.encode_ordinary("<|endoftext|>"))
    assert spans[0]["invocation_id"] == "tool-1"
    assert spans[0]["turn_id"] == "turn-tool-1"
    assert spans[0]["model"] == "gpt-known"
    assert spans[0]["encoding"] == "local-byte-merge"
    assert spans[0]["source_id"]
    assert len({span["source_id"] for span in spans[1:]}) == 2
    assert "<|endoftext|>ab" not in json.dumps(spans)


@pytest.mark.parametrize(
    ("context", "call_metadata", "expected_model"),
    [
        ({"turn_id": "context-turn", "model": "gpt-context"}, {"turn_id": "call-turn"}, None),
        (
            {"turn_id": "call-turn", "model": "gpt-context"},
            {"turn_id": "call-turn", "model": "gpt-call"},
            None,
        ),
        (None, {"turn_id": "call-turn", "model": "gpt-explicit"}, "gpt-explicit"),
        (None, {"model": "gpt-without-turn"}, None),
    ],
)
def test_codex_model_requires_matching_context_or_explicit_call_turn(
    monkeypatch: pytest.MonkeyPatch,
    context: dict[str, str] | None,
    call_metadata: dict[str, str],
    expected_model: str | None,
) -> None:
    import tiktoken

    encoding = _encoding("local-model-check")
    monkeypatch.setattr(tiktoken, "encoding_for_model", lambda _model: encoding)
    call_payload = {
        "type": "function_call",
        "call_id": "call-1",
        "arguments": json.dumps({"agent_thread_id": "child-1", "message": "ab"}),
        **call_metadata,
    }
    records = []
    if context is not None:
        records.append({"type": "turn_context", "payload": context})
    records.extend(
        [
            {
                "type": "response_item",
                "timestamp": "2026-10-07T10:00:00Z",
                "payload": call_payload,
            },
            {
                "type": "response_item",
                "timestamp": "2026-10-07T10:00:01Z",
                "payload": {"type": "function_call_output", "call_id": "call-1", "output": "ab"},
            },
        ]
    )

    spans = _spans(_jsonl(*records), child_id="child-1", backend="codex")

    assert spans[0]["model"] == expected_model
    if expected_model is None:
        assert spans[0]["measure"] == {"state": "unavailable", "value": None}
        assert spans[0]["reason"] == "parent_model_missing"
    else:
        assert spans[0]["measure"]["state"] == "measured"
        assert spans[0]["encoding"] == "local-model-check"


def test_codex_uses_each_invocation_turn_model_and_pairs_call_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tiktoken

    encodings = {
        "gpt-model-a": _encoding("local-a", merge_ab=True),
        "gpt-model-b": _encoding("local-b"),
    }
    monkeypatch.setattr(tiktoken, "encoding_for_model", encodings.__getitem__)
    records = []
    for turn, model, child_id, call_id in (
        ("turn-a", "gpt-model-a", "child-a", "call-a"),
        ("turn-b", "gpt-model-b", "child-b", "call-b"),
    ):
        arguments = json.dumps(
            {"agent_thread_id": child_id, "message": "ab"},
            separators=(",", ":"),
        )
        records.extend(
            [
                {"type": "turn_context", "payload": {"turn_id": turn, "model": model}},
                {
                    "type": "response_item",
                    "timestamp": "2026-10-07T10:00:00Z",
                    "payload": {
                        "type": "function_call",
                        "name": "spawn_agent",
                        "call_id": call_id,
                        "arguments": arguments,
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-10-07T10:00:01Z",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": "ab",
                    },
                },
            ]
        )
    transcript = _jsonl(*records)

    first = _spans(transcript, child_id="child-a", backend="codex")
    second = _spans(transcript, child_id="child-b", backend="codex")

    assert first[0]["model"] == "gpt-model-a"
    assert first[0]["encoding"] == "local-a"
    assert first[0]["measure"]["value"] == len(encodings["gpt-model-a"].encode_ordinary("ab"))
    assert first[1]["measure"]["value"] == 1
    assert second[0]["model"] == "gpt-model-b"
    assert second[0]["encoding"] == "local-b"
    assert second[1]["measure"]["value"] == 2
    assert first[0]["turn_id"] == "turn-a"
    assert second[0]["turn_id"] == "turn-b"


def test_codex_call_without_message_or_prompt_does_not_count_other_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tiktoken

    encoding = _encoding("local-no-prompt")
    monkeypatch.setattr(tiktoken, "encoding_for_model", lambda _model: encoding)
    transcript = _jsonl(
        {"type": "turn_context", "payload": {"turn_id": "turn-1", "model": "gpt-known"}},
        {
            "type": "response_item",
            "timestamp": "2026-10-07T10:00:00Z",
            "payload": {
                "type": "function_call",
                "call_id": "call-1",
                "arguments": json.dumps(
                    {"agent_thread_id": "child-1", "description": "not the prompt"}
                ),
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-10-07T10:00:01Z",
            "payload": {"type": "function_call_output", "call_id": "call-1", "output": "ab"},
        },
    )

    spans = _spans(transcript, child_id="child-1", backend="codex")

    assert spans[0]["measure"] == {"state": "unavailable", "value": None}
    assert spans[0]["reason"] == "parent_prompt_text_missing"
    assert spans[1]["measure"] == {"state": "measured", "value": 2}


@pytest.mark.parametrize(
    ("output", "expected_state", "expected_value", "expected_reason"),
    [
        (
            json.dumps({"agent_id": "child-1", "status": "completed"}),
            "unavailable",
            None,
            "parent_return_acknowledgement_only",
        ),
        (
            json.dumps({"agent_id": "child-1"}),
            "unavailable",
            None,
            "parent_return_acknowledgement_only",
        ),
        (json.dumps({"agent_id": "child-1", "text": "ab"}), "measured", 2, None),
        (json.dumps({"agent_id": "child-1", "response_text": "ab"}), "measured", 2, None),
    ],
)
def test_codex_acknowledgement_is_not_return_text(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    expected_state: str,
    expected_value: int | None,
    expected_reason: str | None,
) -> None:
    import tiktoken

    encoding = _encoding("local-acknowledgement")
    monkeypatch.setattr(tiktoken, "encoding_for_model", lambda _model: encoding)
    transcript = _jsonl(
        {"type": "turn_context", "payload": {"turn_id": "turn-1", "model": "gpt-known"}},
        {
            "type": "response_item",
            "timestamp": "2026-10-07T10:00:00Z",
            "payload": {
                "type": "function_call",
                "call_id": "call-1",
                "arguments": json.dumps({"agent_thread_id": "child-1", "message": "ab"}),
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-10-07T10:00:01Z",
            "payload": {"type": "function_call_output", "call_id": "call-1", "output": output},
        },
    )

    spans = _spans(transcript, child_id="child-1", backend="codex")
    returned = spans[1]

    assert returned["measure"]["state"] == expected_state
    assert returned["measure"]["value"] == expected_value
    assert returned["reason"] == expected_reason


def test_codex_json_child_text_without_ack_shape_remains_return_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tiktoken

    encoding = _encoding("local-json-child-text")
    monkeypatch.setattr(tiktoken, "encoding_for_model", lambda _model: encoding)
    output = json.dumps({"answer": "child-authored", "count": 1})
    transcript = _jsonl(
        {"type": "turn_context", "payload": {"turn_id": "turn-1", "model": "gpt-known"}},
        {
            "type": "response_item",
            "timestamp": "2026-10-07T10:00:00Z",
            "payload": {
                "type": "function_call",
                "call_id": "call-1",
                "arguments": json.dumps({"agent_thread_id": "child-1", "message": "ab"}),
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-10-07T10:00:01Z",
            "payload": {"type": "function_call_output", "call_id": "call-1", "output": output},
        },
    )

    spans = _spans(transcript, child_id="child-1", backend="codex")

    assert spans[1]["measure"] == {
        "state": "measured",
        "value": len(encoding.encode_ordinary(output)),
    }
    assert spans[1]["reason"] is None


@pytest.mark.parametrize(
    ("provider", "expected_state", "expected_reason"),
    [("openai", "measured_zero", None), ("anthropic", "unavailable", "unsupported_provider")],
)
def test_empty_prompt_is_zero_and_missing_return_or_unsupported_provider_stays_explicit(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    expected_state: str,
    expected_reason: str | None,
) -> None:
    import tiktoken

    encoding = _encoding("local-empty")
    monkeypatch.setattr(tiktoken, "encoding_for_model", lambda _model: encoding)
    spans = _spans(
        _jsonl(_claude_call(child_id="child-1", tool_id="tool-1", prompt="")),
        child_id="child-1",
        provider=provider,
    )

    assert spans[0]["measure"]["state"] == expected_state
    assert spans[0]["reason"] == expected_reason
    assert spans[1]["measure"] == {"state": "unavailable", "value": None}
    assert spans[1]["reason"] == "parent_return_missing"


def test_child_id_is_not_inferred_from_claude_tool_use_id() -> None:
    transcript = _jsonl(
        _claude_call(child_id=None, tool_id="child-1", prompt="do work"),
        _claude_result(tool_id="child-1", record_id="result-1", text="done"),
    )

    spans = _spans(transcript, child_id="child-1")

    assert [span["measure"] for span in spans] == [
        {"state": "unavailable", "value": None},
        {"state": "unavailable", "value": None},
    ]
    assert {span["reason"] for span in spans} == {"child_identity_unlinked"}
    assert all(span["invocation_id"] is None for span in spans)


def test_native_child_dependency_tracks_parent_transcript_and_tokenizer_version(
    tmp_path: Path,
) -> None:
    row: dict[str, Any] = {"timestamp": "2026-10-07T10:00:00Z"}
    transcript_path = tmp_path / "parent.jsonl"
    transcript_path.write_text("first parent content\n", encoding="utf-8")
    original_parent_fingerprint = projection._transcript_fingerprint(
        transcript_path, transcript_path.read_text(encoding="utf-8")
    )
    child: dict[str, Any] = {
        "_parent_transcript_fingerprint": original_parent_fingerprint,
        "_tokenizer_version": "0.14.0",
    }
    original = projection._child_dependency(row, child)

    transcript_path.write_text("replacement parent content\n", encoding="utf-8")
    child["_parent_transcript_fingerprint"] = projection._transcript_fingerprint(
        transcript_path, transcript_path.read_text(encoding="utf-8")
    )
    changed_transcript = projection._child_dependency(row, child)
    child["_parent_transcript_fingerprint"] = original_parent_fingerprint
    child["_tokenizer_version"] = "0.14.1"
    changed_tokenizer = projection._child_dependency(row, child)

    assert original != changed_transcript
    assert original != changed_tokenizer
