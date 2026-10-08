"""Parent-side text spans delivered to verified native child invocations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any

from autoskillit._parent_assistant_turns import (
    _codex_assistant_payload,
    _resolve_codex_turn_id,
    _resolve_turn_id,
    is_parent_assistant_record,
)
from autoskillit.core import BackendCapabilities, TokenMeasure, get_logger
from autoskillit.execution.backends import get_backend

logger = get_logger(__name__)


def parent_transcript_records(text: str | None, backend: str) -> tuple[dict[str, Any], ...]:
    if text is None:
        return ()
    raw_records = _raw_records(text)
    return (
        _claude_records(raw_records) if backend == "claude_code" else _codex_records(raw_records)
    )


def _raw_records(text: str) -> tuple[dict[str, Any], ...]:
    records = []
    for line in text.splitlines(keepends=True):
        if not line.endswith("\n") or not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return tuple(records)


def _claude_records(raw_records: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    entries = []
    for record in raw_records:
        parent = is_parent_assistant_record(record)
        message = record.get("message")
        model = message.get("model") if parent and isinstance(message, dict) else None
        turn_id = _resolve_turn_id(record) if parent else ""
        entries.append(_record_entry(record, "claude_code", parent, turn_id, model))
    return tuple(entries)


def _codex_records(raw_records: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    entries = []
    active_turn_id = ""
    active_model: str | None = None
    context_seen = False
    for record in raw_records:
        payload = record.get("payload")
        if record.get("type") == "turn_context" and isinstance(payload, dict):
            context_seen = True
            active_model = _model_text(payload.get("model"))
            active_turn_id = _model_text(payload.get("turn_id")) or ""
            continue
        if not isinstance(payload, dict) or not _is_parent_codex_record(record, payload):
            continue
        turn_id = _resolve_codex_turn_id(record, active_turn_id)
        model = _codex_parent_model(
            record, payload, turn_id, active_turn_id, active_model, context_seen
        )
        entries.append(_record_entry(record, "codex", True, turn_id, model))
    return tuple(entries)


def _is_parent_codex_record(record: dict[str, Any], payload: dict[str, Any]) -> bool:
    if record.get("type") != "response_item":
        return False
    if payload.get("agent_id") or payload.get("agentId"):
        return False
    return _codex_assistant_payload(record) is not None or payload.get("type") in {
        "function_call_output",
        "custom_tool_call_output",
    }


def _codex_parent_model(
    record: Mapping[str, Any],
    payload: Mapping[str, Any],
    resolved_turn_id: str,
    context_turn_id: str,
    context_model: str | None,
    context_seen: bool,
) -> str | None:
    models = {
        value
        for value in (_model_text(record.get("model")), _model_text(payload.get("model")))
        if value is not None
    }
    if len(models) > 1:
        return None
    explicit_model = next(iter(models)) if models else None
    explicit_turn_id = (
        _resolve_turn_id(dict(record))
        or _model_text(record.get("turn_id"))
        or _model_text(payload.get("turn_id"))
    )
    if context_turn_id:
        if resolved_turn_id != context_turn_id:
            return None
        if context_model and explicit_model and context_model != explicit_model:
            return None
        return context_model or explicit_model
    if not explicit_turn_id or not explicit_model:
        return None
    if context_seen and context_model and context_model != explicit_model:
        return None
    return explicit_model


def _model_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _record_entry(
    record: dict[str, Any], backend: str, parent: bool, turn_id: str, model: object
) -> dict[str, Any]:
    timestamp = record.get("timestamp")
    return {
        "record": record,
        "parent_record": parent,
        "turn_id": turn_id or None,
        "model": model if isinstance(model, str) and model else None,
        "timestamp": timestamp if isinstance(timestamp, str) else None,
        "source_id": _source_id(record, backend),
    }


def _source_id(record: Mapping[str, Any], backend: str) -> str:
    serialized = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]
    record_id = record.get("uuid", record.get("id"))
    return (
        f"{backend}:{record_id if isinstance(record_id, str) and record_id else digest}:{digest}"
    )


def parent_context_spans(
    row: Mapping[str, Any],
    child_id: str,
    backend: str,
    parent_records: Sequence[dict[str, Any]],
    *,
    parent_complete: bool,
    parent_transcript_reason: str | None,
    tokenizer_version: str | None,
) -> list[dict[str, Any]]:
    invocations = _linked_parent_invocations(parent_records, child_id, backend)
    if not invocations:
        reason = parent_transcript_reason or (
            "parent_transcript_incomplete" if not parent_complete else "child_identity_unlinked"
        )
        return _unlinked_spans(row, tokenizer_version, reason)
    return [
        span
        for invocation in invocations
        for span in _invocation_spans(row, invocation, parent_complete, tokenizer_version)
    ]


def _unlinked_spans(
    row: Mapping[str, Any], tokenizer_version: str | None, reason: str
) -> list[dict[str, Any]]:
    return [
        _span(
            field,
            {},
            row,
            None,
            None,
            tokenizer_version,
            None,
            None,
            reason,
            None,
            reason,
            None,
            None,
        )
        for field in ("parent_prompt_tokens", "subagent_return_tokens")
    ]


def _linked_parent_invocations(
    records: Sequence[dict[str, Any]], child_id: str, backend: str
) -> list[dict[str, Any]]:
    capabilities = get_backend(backend.replace("_", "-")).capabilities
    if backend == "claude_code":
        return _claude_invocations(records, child_id, capabilities)
    return _codex_invocations(records, child_id, capabilities)


def _claude_invocations(
    records: Sequence[dict[str, Any]], child_id: str, capabilities: BackendCapabilities
) -> list[dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    results: dict[str, list[dict[str, Any]]] = {}
    seen_results: set[str] = set()
    for entry in records:
        if entry["parent_record"]:
            _claude_calls(entry, calls, child_id)
        if entry["record"].get("type") == "user":
            _claude_results(entry, results, seen_results)
    return _linked_invocations(calls, results, child_id, capabilities)


def _claude_calls(
    entry: Mapping[str, Any], calls: dict[str, dict[str, Any]], child_id: str
) -> None:
    record = entry["record"]
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return
    for index, block in enumerate(content):
        if not isinstance(block, Mapping) or block.get("type") != "tool_use":
            continue
        if str(block.get("name", "")).casefold() not in {"agent", "task"}:
            continue
        invocation_id = block.get("id")
        if not isinstance(invocation_id, str) or invocation_id in calls:
            continue
        call_input = block.get("input")
        prompt = call_input.get("prompt") if isinstance(call_input, Mapping) else None
        identities = _child_ids(call_input) | _child_ids(block)
        calls[invocation_id] = {
            "invocation_id": invocation_id,
            "turn_id": entry.get("turn_id"),
            "model": entry.get("model"),
            "prompt_text": prompt if isinstance(prompt, str) else None,
            "prompt_reason": None if isinstance(prompt, str) else "parent_prompt_text_missing",
            "prompt_source_id": f"{entry['source_id']}:tool-{index}",
            "prompt_timestamp": entry.get("timestamp"),
            "linked": identities == {child_id},
        }


def _claude_results(
    entry: Mapping[str, Any],
    results: dict[str, list[dict[str, Any]]],
    seen_results: set[str],
) -> None:
    record = entry["record"]
    message = record.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, list):
        return
    for index, block in enumerate(content):
        if not isinstance(block, Mapping) or block.get("type") != "tool_result":
            continue
        invocation_id = block.get("tool_use_id")
        if not isinstance(invocation_id, str):
            continue
        source_id = f"{entry['source_id']}:tool-result-{index}"
        if source_id in seen_results:
            continue
        seen_results.add(source_id)
        raw_text = block.get("content")
        result_text, reason = _claude_result_text(raw_text)
        results.setdefault(invocation_id, []).append(
            {
                "text": result_text,
                "reason": reason,
                "source_id": source_id,
                "timestamp": entry.get("timestamp"),
                "identities": _child_ids(block) | _child_ids(raw_text),
            }
        )


def _claude_result_text(value: object) -> tuple[str | None, str | None]:
    if isinstance(value, str):
        return value, None
    if isinstance(value, list):
        parts = [
            block["text"]
            for block in value
            if isinstance(block, Mapping)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        if parts:
            return "".join(parts), None
    return None, "parent_return_text_missing"


def _codex_invocations(
    records: Sequence[dict[str, Any]], child_id: str, capabilities: BackendCapabilities
) -> list[dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    results: dict[str, list[dict[str, Any]]] = {}
    seen_results: set[str] = set()
    for entry in records:
        if not entry["parent_record"]:
            continue
        payload = entry["record"].get("payload")
        if not isinstance(payload, Mapping):
            continue
        call_id = payload.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            continue
        payload_type = payload.get("type")
        if payload_type in {"function_call", "custom_tool_call"}:
            _codex_call(entry, payload, call_id, calls, child_id)
        elif payload_type in {"function_call_output", "custom_tool_call_output"}:
            _codex_result(entry, payload, call_id, results, seen_results)
    return _linked_invocations(calls, results, child_id, capabilities)


def _codex_call(
    entry: Mapping[str, Any],
    payload: Mapping[str, Any],
    call_id: str,
    calls: dict[str, dict[str, Any]],
    child_id: str,
) -> None:
    if call_id in calls:
        return
    arguments = payload.get("arguments", payload.get("args", payload.get("input")))
    prompt = _codex_prompt_text(arguments)
    identities = _child_ids(payload) | _child_ids(arguments)
    calls[call_id] = {
        "invocation_id": call_id,
        "turn_id": entry.get("turn_id"),
        "model": entry.get("model"),
        "prompt_text": prompt,
        "prompt_reason": None if prompt is not None else "parent_prompt_text_missing",
        "prompt_source_id": entry["source_id"],
        "prompt_timestamp": entry.get("timestamp"),
        "linked": identities == {child_id},
    }


def _codex_result(
    entry: Mapping[str, Any],
    payload: Mapping[str, Any],
    call_id: str,
    results: dict[str, list[dict[str, Any]]],
    seen_results: set[str],
) -> None:
    source_id = entry["source_id"]
    if source_id in seen_results:
        return
    seen_results.add(source_id)
    text, reason = _codex_result_text(payload.get("output"))
    results.setdefault(call_id, []).append(
        {
            "text": text,
            "reason": reason,
            "source_id": source_id,
            "timestamp": entry.get("timestamp"),
            "identities": _child_ids(payload) | _child_ids(payload.get("output")),
            "turn_id": entry.get("turn_id"),
        }
    )


def _codex_result_text(output: object) -> tuple[str | None, str | None]:
    if isinstance(output, Mapping):
        structured = output
    elif isinstance(output, str):
        try:
            decoded = json.loads(output)
        except json.JSONDecodeError:
            return output, None
        if not isinstance(decoded, Mapping):
            return output, None
        structured = decoded
    else:
        return None, "parent_return_text_missing"

    identity_status_fields = {"child_id", "agent_id", "agentId", "agent_thread_id", "status"}
    text_fields = {"text", "response_text"}
    if set(structured) <= identity_status_fields | text_fields:
        texts = [structured[key] for key in text_fields if isinstance(structured.get(key), str)]
        if texts:
            if len(set(texts)) != 1:
                return None, "parent_return_text_ambiguous"
            return texts[0], None
        if set(structured) & identity_status_fields:
            return None, "parent_return_acknowledgement_only"
    if isinstance(output, str):
        return output, None
    return None, "parent_return_text_missing"


def _linked_invocations(
    calls: Mapping[str, dict[str, Any]],
    results: Mapping[str, list[dict[str, Any]]],
    child_id: str,
    capabilities: BackendCapabilities,
) -> list[dict[str, Any]]:
    linked = []
    for invocation_id, call in calls.items():
        candidates = results.get(invocation_id, [])
        if not _has_child_link(call, candidates, child_id):
            continue
        accepted = _linked_results(candidates, call, child_id, capabilities)
        call["results"] = accepted
        if candidates and not accepted:
            call["return_reason"] = "parent_return_identity_mismatch"
        linked.append(call)
    return linked


def _has_child_link(
    call: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]], child_id: str
) -> bool:
    return bool(call.get("linked")) or any(
        result.get("identities") == {child_id} for result in candidates
    )


def _linked_results(
    candidates: Sequence[dict[str, Any]],
    call: Mapping[str, Any],
    child_id: str,
    capabilities: BackendCapabilities,
) -> list[dict[str, Any]]:
    return [
        result
        for result in candidates
        if (not result["identities"] or result["identities"] == {child_id})
        and _same_invocation_turn(result, call, capabilities)
    ]


def _same_invocation_turn(
    result: Mapping[str, Any], call: Mapping[str, Any], capabilities: BackendCapabilities
) -> bool:
    result_turn = result.get("turn_id")
    call_turn = call.get("turn_id")
    return (
        capabilities.supports_claude_format_stdout
        or not result_turn
        or not call_turn
        or result_turn == call_turn
    )


def _child_ids(value: object) -> set[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return set()
    if not isinstance(value, Mapping):
        return set()
    return {
        item
        for key in ("child_id", "agent_id", "agentId", "agent_thread_id")
        if isinstance((item := value.get(key)), str) and item
    }


def _codex_prompt_text(arguments: object) -> str | None:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    if not isinstance(arguments, Mapping):
        return None
    texts = [
        arguments[key] for key in ("message", "prompt") if isinstance(arguments.get(key), str)
    ]
    return texts[0] if texts and len(set(texts)) == 1 else None


def _invocation_spans(
    row: Mapping[str, Any],
    invocation: Mapping[str, Any],
    parent_complete: bool,
    tokenizer_version: str | None,
) -> list[dict[str, Any]]:
    provider = row.get("provider_used")
    provider = provider if isinstance(provider, str) and provider else None
    model = invocation.get("model")
    encoding, encoding_name, encoding_reason = _parent_encoding(provider, model)
    prompt_span = _span(
        "parent_prompt_tokens",
        invocation,
        row,
        provider,
        model,
        tokenizer_version,
        encoding,
        encoding_name,
        encoding_reason,
        invocation.get("prompt_text"),
        invocation.get("prompt_reason"),
        invocation.get("prompt_source_id"),
        invocation.get("prompt_timestamp"),
    )
    results = invocation.get("results")
    if not isinstance(results, list) or not results:
        return [
            prompt_span,
            _missing_return_span(
                row,
                invocation,
                provider,
                model,
                tokenizer_version,
                encoding,
                encoding_name,
                encoding_reason,
                parent_complete,
            ),
        ]
    return [prompt_span] + [
        _span(
            "subagent_return_tokens",
            invocation,
            row,
            provider,
            model,
            tokenizer_version,
            encoding,
            encoding_name,
            encoding_reason,
            result.get("text"),
            result.get("reason"),
            result.get("source_id"),
            result.get("timestamp"),
        )
        for result in results
    ]


def _missing_return_span(
    row: Mapping[str, Any],
    invocation: Mapping[str, Any],
    provider: str | None,
    model: object,
    tokenizer_version: str | None,
    encoding: Any | None,
    encoding_name: str | None,
    encoding_reason: str | None,
    parent_complete: bool,
) -> dict[str, Any]:
    reason = invocation.get("return_reason") or (
        "parent_transcript_incomplete" if not parent_complete else "parent_return_missing"
    )
    return _span(
        "subagent_return_tokens",
        invocation,
        row,
        provider,
        model,
        tokenizer_version,
        encoding,
        encoding_name,
        encoding_reason,
        None,
        reason,
        None,
        None,
    )


def _parent_encoding(provider: object, model: object) -> tuple[Any | None, str | None, str | None]:
    if not isinstance(provider, str) or provider.casefold() != "openai":
        return None, None, "unsupported_provider"
    if not isinstance(model, str) or not model:
        return None, None, "parent_model_missing"
    try:
        import tiktoken

        encoding = tiktoken.encoding_for_model(model)
    except KeyError as exc:
        logger.warning(
            "parent_context_model_unsupported",
            provider=provider,
            model=model,
            error=type(exc).__name__,
        )
        return None, None, "unsupported_model"
    except Exception as exc:
        logger.warning(
            "parent_context_tokenizer_unavailable",
            provider=provider,
            model=model,
            error=type(exc).__name__,
            exc_info=True,
        )
        return None, None, "tokenizer_initialization_failed"
    name = getattr(encoding, "name", None)
    if not isinstance(name, str) or not name:
        logger.warning("parent_context_encoding_name_missing", provider=provider, model=model)
        return None, None, "tokenizer_initialization_failed"
    return encoding, name, None


def _span(
    field: str,
    invocation: Mapping[str, Any],
    row: Mapping[str, Any],
    provider: str | None,
    model: object,
    tokenizer_version: str | None,
    encoding: Any | None,
    encoding_name: str | None,
    encoding_reason: str | None,
    text: object,
    missing_reason: object,
    source_id: object,
    timestamp: object,
) -> dict[str, Any]:
    reason = missing_reason if isinstance(missing_reason, str) else None
    measure = TokenMeasure.unavailable()
    if reason is None:
        if not isinstance(text, str):
            reason = "parent_text_missing"
        elif encoding_reason:
            reason = encoding_reason
        elif encoding is None:
            reason = "tokenizer_initialization_failed"
        else:
            try:
                measure = TokenMeasure.observed(len(encoding.encode_ordinary(text)))
            except Exception as exc:
                logger.warning(
                    "parent_context_text_encoding_failed",
                    provider=provider,
                    model=model,
                    error=type(exc).__name__,
                    exc_info=True,
                )
                reason = "tokenizer_encoding_failed"
    invocation_id = invocation.get("invocation_id")
    turn_id = invocation.get("turn_id")
    return {
        "field": field,
        "measure": measure.to_dict(),
        "invocation_id": invocation_id if isinstance(invocation_id, str) else None,
        "turn_id": turn_id if isinstance(turn_id, str) else None,
        "source_id": source_id if isinstance(source_id, str) else None,
        "timestamp": timestamp if isinstance(timestamp, str) else None,
        "harness": row.get("backend") if isinstance(row.get("backend"), str) else None,
        "provider": provider,
        "model": model if isinstance(model, str) else None,
        "tokenizer_version": tokenizer_version,
        "encoding": encoding_name,
        "reason": reason,
    }


def tokenizer_version() -> str | None:
    try:
        return package_version("tiktoken")
    except PackageNotFoundError:
        return None
