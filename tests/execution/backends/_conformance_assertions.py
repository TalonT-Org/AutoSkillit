"""Pytest-free pure-function assertion helpers for Codex conformance tests.

Importable from both pytest and non-pytest contexts. Every function raises
AssertionError with a descriptive message on failure.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from autoskillit.core import SkillSemanticAdaptationResult, SkillSemanticPlan
from autoskillit.core.types._type_enums import CodexEventType
from autoskillit.execution.process._process_jsonl import _marker_is_standalone
from autoskillit.hooks._capture._snapshot import CaptureFinalManifest
from autoskillit.hooks._capture_artifacts import (
    CaptureSetupError,
    open_capture_lifecycle,
)
from autoskillit.hooks._capture_contract import (
    CaptureContractError,
    CaptureV2Fields,
    parse_capture_v2,
)
from autoskillit.hooks._capture_lifecycle import CaptureLifecycleError


@dataclass(frozen=True, slots=True)
class ShellCaptureAuthorityAssertion:
    fields: CaptureV2Fields
    manifest: CaptureFinalManifest
    capture_bytes: bytes


def assert_shell_capture_marker_authority(
    completed_output: str,
    physical_project: Path,
    expected_capture_id: str,
    *,
    sentinels: tuple[bytes, ...] = (),
) -> ShellCaptureAuthorityAssertion:
    candidates = [
        line.encode("utf-8")
        for line in completed_output.splitlines()
        if line.startswith("[AutoSkillit shell capture v2:")
    ]
    assert len(candidates) == 1, (
        f"expected exactly one shell-capture V2 marker in completed output, got {len(candidates)}"
    )
    try:
        fields = parse_capture_v2(candidates[0])
    except CaptureContractError as exc:
        raise AssertionError(f"shell-capture V2 marker is invalid: {exc}") from exc
    assert fields.capture_id == expected_capture_id
    assert fields.reference_status == "published"
    assert fields.reference is not None

    chunks: list[bytes] = []
    try:
        with open_capture_lifecycle(str(physical_project), create=False) as lifecycle:
            with lifecycle.open_verified_capture(fields.reference) as reader:
                manifest = cast(CaptureFinalManifest, reader.manifest)
                offset = 0
                while offset < manifest.total_bytes:
                    chunk = reader.read(
                        offset,
                        min(64 * 1024, manifest.total_bytes - offset),
                    )
                    if not chunk:
                        raise AssertionError("verified capture reader returned early EOF")
                    chunks.append(chunk)
                    offset += len(chunk)
    except (CaptureSetupError, CaptureLifecycleError, OSError) as exc:
        raise AssertionError(f"shell-capture reference did not resolve: {exc}") from exc

    capture_bytes = b"".join(chunks)
    assert manifest.capture_id == fields.capture_id
    assert manifest.finalized_at_revision == fields.finalized_at_revision
    assert manifest.total_bytes == fields.total_bytes == len(capture_bytes)
    assert manifest.sha256 == fields.sha256 == hashlib.sha256(capture_bytes).hexdigest()
    assert manifest.command_outcome.kind.value == fields.command_outcome_kind
    assert manifest.command_outcome.value == fields.command_outcome_value
    assert manifest.command_outcome.shell_returncode == fields.shell_returncode
    missing = [sentinel for sentinel in sentinels if sentinel not in capture_bytes]
    assert not missing, f"verified shell-capture bytes lack sentinels: {missing}"
    return ShellCaptureAuthorityAssertion(fields, manifest, capture_bytes)


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


def assert_generated_child_delivery(
    parent_events: list[dict],
    child_events: list[dict],
    *,
    parent_id: str,
    agent_role: str,
    output_discipline_digest: str,
    backend: str = "codex",
    semantic_plan: SkillSemanticPlan | None = None,
    semantic_adaptation: SkillSemanticAdaptationResult | None = None,
    runtime_cardinalities: Mapping[str, int] | None = None,
    child_terminal_sentinel: str | None = None,
    sibling_result_sentinel: str | None = None,
    parent_terminal_sentinel: str | None = None,
) -> None:
    """Assert one semantic child-delivery plan over normalized Claude/Codex traces.

    This is the sole oracle for both deterministic adapter traces and the installed
    Codex native-subagent probe.  Raw backend events are normalized locally so the
    semantic assertions remain backend-neutral.
    """

    @dataclass(slots=True)
    class _ObservedCall:
        name: str
        call_id: str
        arguments: dict[str, object]
        call_index: int
        result: str = ""
        result_index: int | None = None

    def _mapping(value: object) -> dict[str, object]:
        if isinstance(value, Mapping):
            return {str(key): item for key, item in value.items()}
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return {}
            if isinstance(decoded, dict):
                return {str(key): item for key, item in decoded.items()}
        return {}

    def _text(value: object) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, sort_keys=True, default=str)

    def _blocks(event: Mapping[str, object]) -> list[dict[str, object]]:
        message = event.get("message")
        if not isinstance(message, Mapping):
            payload = event.get("payload")
            if isinstance(payload, Mapping) and isinstance(payload.get("message"), Mapping):
                message = payload["message"]
            elif isinstance(payload, Mapping):
                message = payload
        if not isinstance(message, Mapping):
            return []
        content = message.get("content", ())
        if not isinstance(content, list):
            return []
        return [dict(block) for block in content if isinstance(block, Mapping)]

    observed: list[_ObservedCall] = []
    by_id: dict[str, _ObservedCall] = {}
    for index, event in enumerate(parent_events):
        payload = event.get("payload", {})
        if (
            event.get("type") == "response_item"
            and isinstance(payload, Mapping)
            and payload.get("type") == "function_call"
        ):
            call = _ObservedCall(
                name=str(payload.get("name", "")),
                call_id=str(payload.get("call_id", "")),
                arguments=_mapping(payload.get("arguments", {})),
                call_index=index,
            )
            observed.append(call)
            by_id[call.call_id] = call
        elif (
            event.get("type") == "response_item"
            and isinstance(payload, Mapping)
            and payload.get("type") == "function_call_output"
        ):
            call = by_id.get(str(payload.get("call_id", "")))
            if call is not None:
                call.result = _text(payload.get("output", ""))
                call.result_index = index
        for block in _blocks(event):
            if block.get("type") == "tool_use":
                call = _ObservedCall(
                    name=str(block.get("name", "")),
                    call_id=str(block.get("id", "")),
                    arguments=_mapping(block.get("input", {})),
                    call_index=index,
                )
                observed.append(call)
                by_id[call.call_id] = call
            elif block.get("type") == "tool_result":
                call = by_id.get(str(block.get("tool_use_id", "")))
                if call is not None:
                    call.result = _text(block.get("content", ""))
                    call.result_index = index

    assert (semantic_plan is None) == (semantic_adaptation is None), (
        "semantic plan and adaptation must be supplied together"
    )
    if semantic_plan is not None and semantic_adaptation is not None:
        assert semantic_adaptation.unsupported_operation is None
        semantic_adaptation.validate_for(semantic_plan, backend=backend)
        expected_roles_list: list[str] = []
        for spawn in semantic_plan.child_spawns:
            if spawn.for_each is not None:
                assert runtime_cardinalities is not None
                assert spawn.for_each in runtime_cardinalities
                cardinality = runtime_cardinalities[spawn.for_each]
            else:
                assert spawn.count is not None
                cardinality = spawn.count
            expected_roles_list.extend(
                semantic_adaptation.logical_role_mapping[spawn.role] for _ in range(cardinality)
            )
        expected_roles = tuple(expected_roles_list)
    else:
        expected_roles = (agent_role,)

    spawn_names = {"spawn_agent"} if backend == "codex" else {"Agent"}
    spawn_calls = [call for call in observed if call.name in spawn_names]
    assert len(spawn_calls) == len(expected_roles), (
        f"expected {len(expected_roles)} native child calls, got {len(spawn_calls)}"
    )
    role_key = "agent_type" if backend == "codex" else "subagent_type"
    actual_roles = tuple(str(call.arguments.get(role_key, "")) for call in spawn_calls)
    assert sorted(actual_roles) == sorted(expected_roles), (
        f"native role mapping mismatch: expected {expected_roles}, got {actual_roles}"
    )

    policies_by_native_role: dict[str, tuple[str | None, str | None, str, str | None]] = {}
    if semantic_plan is not None and semantic_adaptation is not None:
        for policy in semantic_plan.child_model_policies:
            native_role = semantic_adaptation.logical_role_mapping[policy.role]
            model, effort = semantic_adaptation.model_effort_policy[native_role]
            policies_by_native_role[native_role] = (
                policy.model_class,
                policy.reasoning_effort,
                model,
                effort,
            )
    for call, native_role in zip(spawn_calls, actual_roles, strict=True):
        policy = policies_by_native_role.get(native_role)
        if policy is None:
            assert "model" not in call.arguments
            assert "reasoning_effort" not in call.arguments
            continue
        model_class, required_effort, physical_model, physical_effort = policy
        if model_class is not None:
            assert call.arguments.get("model") in {model_class, physical_model}, (
                f"child {native_role!r} did not receive its canonical model policy"
            )
        else:
            assert "model" not in call.arguments
        if required_effort is not None:
            if backend == "claude":
                assert "reasoning_effort" not in call.arguments
                assert any(
                    required_effort in fragment
                    for fragment in semantic_adaptation.instruction_fragments
                ), f"child {native_role!r} omitted its reasoning policy instruction"
            else:
                assert call.arguments.get("reasoning_effort") in {
                    required_effort,
                    physical_effort,
                }, f"child {native_role!r} did not receive its required reasoning effort"
        else:
            assert "reasoning_effort" not in call.arguments

    if semantic_plan is not None and semantic_plan.concurrency is not None:
        if semantic_plan.concurrency.required and len(spawn_calls) > 1:
            if backend == "claude":
                assert len({call.call_index for call in spawn_calls}) == 1, (
                    "Claude parallel Agent calls were not issued in one assistant message"
                )
            else:
                wait_indices = [call.call_index for call in observed if call.name == "wait_agent"]
                assert wait_indices
                assert max(call.call_index for call in spawn_calls) < min(wait_indices), (
                    "Codex awaited a child before all parallel children were spawned"
                )

    child_ids: list[str] = []
    child_results: list[tuple[str, int]] = []
    if backend == "codex":
        child_handles: list[str] = []
        for call in spawn_calls:
            authored_task_name = call.arguments.get("task_name")
            assert isinstance(authored_task_name, str) and authored_task_name, (
                "spawn_agent omitted task_name"
            )
            canonical_task_name = _mapping(call.result).get("task_name")
            assert isinstance(canonical_task_name, str) and canonical_task_name, (
                f"spawn_agent returned no canonical task_name: {call.result[:500]!r}"
            )
            assert canonical_task_name == authored_task_name or canonical_task_name.endswith(
                f"/{authored_task_name}"
            )
            child_handles.append(canonical_task_name)

        wait_calls = [call for call in observed if call.name == "wait_agent"]
        successful_waits = [
            call
            for call in wait_calls
            if call.result_index is not None and _mapping(call.result).get("timed_out") is False
        ]
        assert successful_waits, "wait_agent never returned successfully"
        assert max(call.call_index for call in spawn_calls) < min(
            call.call_index for call in successful_waits
        ), "Codex awaited a child before all children were spawned"

        completed_notifications: dict[str, list[tuple[str, int]]] = {}
        notification_start = "<subagent_notification>"
        notification_end = "</subagent_notification>"
        for index, event in enumerate(parent_events):
            payload = event.get("payload", {})
            if (
                event.get("type") != "response_item"
                or not isinstance(payload, Mapping)
                or payload.get("type") != "message"
                or payload.get("role") != "user"
            ):
                continue
            for block in _blocks(event):
                block_text = str(block.get("text", ""))
                if notification_start not in block_text or notification_end not in block_text:
                    continue
                encoded = block_text.split(notification_start, 1)[1].split(notification_end, 1)[0]
                notification = _mapping(encoded.strip())
                agent_path = notification.get("agent_path")
                status = notification.get("status")
                if not isinstance(agent_path, str) or not isinstance(status, Mapping):
                    continue
                completed = status.get("completed")
                if isinstance(completed, str) and completed:
                    completed_notifications.setdefault(agent_path, []).append((completed, index))

        for child_handle in child_handles:
            delivered = completed_notifications.get(child_handle, [])
            assert len(delivered) == 1, (
                f"expected one completed notification for {child_handle}, got {len(delivered)}"
            )
            child_results.append(delivered[0])
    else:
        for call in spawn_calls:
            assert call.result and call.result_index is not None, (
                "Claude Agent call did not deliver an independent terminal result"
            )
            child_ids.append(call.call_id)
            child_results.append((call.result, cast(int, call.result_index)))

    if child_terminal_sentinel is not None:
        assert all(child_terminal_sentinel in result for result, _ in child_results), (
            "a child terminal result omitted the expected sentinel"
        )
    if semantic_plan is not None and semantic_plan.evidence is not None:
        if semantic_plan.evidence.independent:
            assert len(child_results) == len(expected_roles)
            assert all(result.strip() for result, _ in child_results)

    if semantic_plan is not None and semantic_adaptation is not None:
        for target in semantic_adaptation.sibling_skill_targets.values():
            matching = [
                call
                for call in observed
                if call.name in {"Skill", "invoke_skill", "run_skill"}
                and target in _text(call.arguments)
            ]
            assert matching, f"sibling skill {target!r} was not invoked"
            assert any(call.result and call.result_index is not None for call in matching), (
                f"sibling skill {target!r} produced no result"
            )
            if sibling_result_sentinel is not None:
                assert any(sibling_result_sentinel in call.result for call in matching), (
                    f"sibling skill {target!r} omitted its terminal sentinel"
                )

    def _assistant_text(event: Mapping[str, object]) -> str:
        payload = event.get("payload", {})
        if (
            event.get("type") == "response_item"
            and isinstance(payload, Mapping)
            and payload.get("type") == "message"
            and payload.get("role") == "assistant"
        ):
            return _text(payload.get("content", ""))
        if event.get("type") == "result":
            return _text(event.get("result", ""))
        return "\n".join(
            _text(block.get("text", "")) for block in _blocks(event) if block.get("type") == "text"
        )

    if parent_terminal_sentinel is not None:
        terminal_indices = [
            index
            for index, event in enumerate(parent_events)
            if parent_terminal_sentinel in _assistant_text(event)
        ]
        assert terminal_indices, "parent terminal success was not delivered"
        assert child_results
        assert min(terminal_indices) > max(index for _, index in child_results), (
            "parent reported success before every child terminal result was delivered"
        )

    if backend != "codex":
        return
    child_session_metas = [
        event.get("payload", {}) for event in child_events if event.get("type") == "session_meta"
    ]
    linked_children = [
        meta
        for meta in child_session_metas
        if (meta.get("forked_from_id") or meta.get("parent_thread_id")) == parent_id
    ]
    assert len(linked_children) == len(child_handles), (
        f"expected {len(child_handles)} children linked to {parent_id}, got {len(linked_children)}"
    )
    linked_by_id = {str(child.get("id", "")): child for child in linked_children}
    linked_by_path = {str(child.get("agent_path", "")): child for child in linked_children}
    for child_handle in child_handles:
        child = linked_by_path.get(child_handle)
        assert child is not None, f"no linked child session matched {child_handle}"
        child_id = str(child.get("id", ""))
        assert child_id
        child_ids.append(child_id)
    assert len(set(child_ids)) == len(child_ids)
    for child_id, native_role in zip(child_ids, actual_roles, strict=True):
        child = linked_by_id[child_id]
        assert child_id != parent_id
        assert (child.get("forked_from_id") or child.get("parent_thread_id")) == parent_id
        assert child.get("agent_role") == native_role
        base_instructions = child.get("base_instructions", {})
        assert isinstance(base_instructions, dict)
        base_text = str(base_instructions.get("text", ""))
        developer_blocks = []
        for event in child_events:
            payload = event.get("payload", {})
            if (
                event.get("type") != "response_item"
                or payload.get("type") != "message"
                or payload.get("role") != "developer"
            ):
                continue
            content = payload.get("content", [])
            if isinstance(content, str):
                developer_blocks.append(content)
            elif isinstance(content, list):
                developer_blocks.extend(
                    str(block.get("text", "")) for block in content if isinstance(block, dict)
                )
        developer_text = "\n".join(developer_blocks)
        assert (
            output_discipline_digest in base_text or output_discipline_digest in developer_text
        ), "generated child instructions omitted output discipline digest"
