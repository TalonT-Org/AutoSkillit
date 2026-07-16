"""Pytest-free pure-function assertion helpers for Codex conformance tests.

Importable from both pytest and non-pytest contexts. Every function raises
AssertionError with a descriptive message on failure.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from autoskillit.core.types._type_enums import CodexEventType
from autoskillit.execution.process._process_jsonl import _marker_is_standalone


def assert_vocabulary_coverage(events: list[dict], expected_types: set[str]) -> None:
    observed = {e.get("type") for e in events}
    missing = expected_types - observed
    assert not missing, f"Missing event types in vocabulary: {sorted(missing)}"


def assert_no_unknown_event_types(events: list[dict]) -> None:
    unknown = []
    for i, e in enumerate(events):
        raw_type = e.get("type", "")
        if CodexEventType.from_ndjson(raw_type) == CodexEventType.UNKNOWN:
            unknown.append((i, raw_type))
    assert not unknown, f"Events with UNKNOWN type (index, raw): {unknown}"


def assert_session_start_present(events: list[dict]) -> None:
    assert events, "Event list is empty — cannot verify session start"
    first = events[0]
    valid_start_types = {
        CodexEventType.THREAD_STARTED.value,
        CodexEventType.SESSION_META.value,
    }
    first_type = first.get("type", "")
    assert first_type in valid_start_types, (
        f"First event type is {first_type!r}, expected one of {sorted(valid_start_types)}"
    )
    session_id = first.get("thread_id", "") or first.get("session_id", "")
    assert session_id, "First event has no non-empty session id field (thread_id or session_id)"


def assert_turn_completed_usage_nonzero(events: list[dict]) -> None:
    for e in events:
        if e.get("type") == CodexEventType.TURN_COMPLETED.value:
            usage = e.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            assert input_tokens > 0, f"turn.completed input_tokens is {input_tokens}, expected > 0"
            assert output_tokens > 0, (
                f"turn.completed output_tokens is {output_tokens}, expected > 0"
            )
            return
    raise AssertionError("No turn.completed event found in event list")


def assert_order_up_marker_standalone(events: list[dict], marker: str) -> None:
    for e in events:
        if e.get("type") != CodexEventType.ITEM_COMPLETED.value:
            continue
        item = e.get("item", {})
        for block in item.get("content", []):
            text = block.get("text", "")
            if marker in text and _marker_is_standalone(text, marker):
                return
    raise AssertionError(
        f"Marker {marker!r} not found as standalone line in any item.completed content block"
    )


def assert_hook_event_format(config_dict: dict) -> None:
    assert "hooks" in config_dict, "config_dict missing 'hooks' key"
    hooks = config_dict["hooks"]
    assert isinstance(hooks, dict), f"'hooks' value is {type(hooks).__name__}, expected dict"
    for event_type, hook_list in hooks.items():
        assert isinstance(event_type, str), f"Hook event key {event_type!r} is not a string"
        assert isinstance(hook_list, list), (
            f"hooks[{event_type!r}] is {type(hook_list).__name__}, expected list"
        )
        for entry in hook_list:
            assert isinstance(entry, dict), (
                f"Hook entry under {event_type!r} is {type(entry).__name__}, expected dict"
            )
            assert "hooks" in entry, f"Hook entry under {event_type!r} missing 'hooks' sub-list"
            for hook in entry["hooks"]:
                assert hook.get("type") == "command", (
                    f"Hook under {event_type!r} has type={hook.get('type')!r}, expected 'command'"
                )
                assert "trusted_hash" in hook, f"Hook under {event_type!r} missing 'trusted_hash'"


def assert_config_schema(config_dict: dict, version_str: str) -> None:
    expected_keys = {"model", "instructions"}
    present = set(config_dict.keys())
    missing = expected_keys - present
    assert not missing, (
        f"Config (version {version_str}) missing expected top-level keys: {sorted(missing)}"
    )


def assert_boundary_spill_behavior(spilled_by_size: dict[int, bool], threshold: int) -> None:
    """Assert the lossless-spill contract immediately around a source threshold."""
    expected = {threshold - 1: False, threshold: False, threshold + 1: True}
    observed = {size: spilled_by_size.get(size) for size in expected}
    assert observed == expected, (
        f"spill boundary mismatch at {threshold}: expected {expected}, observed {observed}"
    )


def assert_sentinels_present(text: str, sentinels: tuple[str, ...]) -> None:
    """Assert distinct workload sentinels survived a delivery or artifact path."""
    missing = [sentinel for sentinel in sentinels if sentinel not in text]
    assert not missing, f"missing sentinels: {missing}"


def assert_spill_artifact_integrity(
    artifact_path: str,
    expected_text: str,
    sentinels: tuple[str, ...],
) -> None:
    """Assert an atomically published spill is byte-complete and content-addressable."""
    path = Path(artifact_path)
    assert path.is_file(), f"spill artifact does not exist: {path}"
    artifact_bytes = path.read_bytes()
    expected_bytes = expected_text.encode("utf-8")
    assert artifact_bytes == expected_bytes, (
        f"spill artifact differs from source: {len(artifact_bytes)} != {len(expected_bytes)} bytes"
    )
    expected_sha256 = hashlib.sha256(expected_bytes).hexdigest()
    actual_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    assert actual_sha256 == expected_sha256, (
        f"spill sha256 mismatch: {actual_sha256} != {expected_sha256}"
    )
    assert_sentinels_present(artifact_bytes.decode("utf-8"), sentinels)


def assert_inline_within_byte_budget(
    inline_text: str,
    byte_budget: int,
    *,
    envelope_slack_bytes: int = 0,
) -> None:
    """Assert inline output stays within a transport ceiling plus explicit envelope slack."""
    inline_bytes = len(inline_text.encode("utf-8"))
    effective_budget = byte_budget + envelope_slack_bytes
    assert inline_bytes <= effective_budget, (
        f"inline output is {inline_bytes} bytes, over {byte_budget} + "
        f"{envelope_slack_bytes} envelope bytes"
    )


def assert_terminal_sentinel_preserved(
    delivered_text: str,
    terminal_sentinel: str,
    truncation_markers: tuple[str, ...],
) -> None:
    """Assert a terminal sentinel arrived and no known transport truncation marker did."""
    assert terminal_sentinel in delivered_text, (
        f"terminal sentinel missing from delivered text: {terminal_sentinel!r}"
    )
    observed_markers = [marker for marker in truncation_markers if marker in delivered_text]
    assert not observed_markers, f"transport truncation markers present: {observed_markers}"
