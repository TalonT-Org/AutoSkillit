"""Deterministic tests for the specialized explorer conformance attestation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.execution.backends._explorer_conformance import (
    EXPLORER_ATTESTATION_SCHEMA_VERSION,
    EXPLORER_MODEL,
    EXPLORER_PARENT_MODEL,
    EXPLORER_PROBE_CONTRACT,
    EXPLORER_PROBE_ROLE,
    EXPLORER_PROBE_TASK_NAME,
    EXPLORER_REASONING_EFFORT,
    EXPLORER_SANDBOX_MODE,
    EXPLORER_TOOL_SURFACE_DIGEST,
    ExplorerConformanceAttestation,
    explorer_probe_definition_digest,
    new_observed_at,
    project_codex_luna_catalog,
    publish_explorer_attestation,
    read_explorer_attestation,
    validate_codex_luna_catalog,
    validate_explorer_attestation,
    validate_explorer_release_readiness,
    validate_published_explorer_release_readiness,
)
from autoskillit.execution.backends._probe_cache import PROBE_POLICY_IDENTITY
from tests.execution.backends._explorer_conformance_assertions import (
    assert_generated_codex_child_delivery,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]
_DEFINITION_DIGEST = explorer_probe_definition_digest()


def _catalog() -> bytes:
    return json.dumps(
        {
            "models": [
                {
                    "slug": EXPLORER_MODEL,
                    "apply_patch_tool_type": "freeform",
                    "tool_mode": "code_mode_only",
                    "supported_reasoning_levels": [
                        {"effort": "high"},
                        {"effort": EXPLORER_REASONING_EFFORT},
                    ],
                }
            ]
        }
    ).encode()


def _attestation(catalog_digest: str) -> ExplorerConformanceAttestation:
    return ExplorerConformanceAttestation(
        schema_version=EXPLORER_ATTESTATION_SCHEMA_VERSION,
        cli_version="codex-cli 1.2.3",
        model_catalog_digest=catalog_digest,
        probe_policy_identity=PROBE_POLICY_IDENTITY,
        probe_contract=EXPLORER_PROBE_CONTRACT,
        cache_miss=True,
        role=EXPLORER_PROBE_ROLE,
        agent_path=EXPLORER_PROBE_TASK_NAME,
        parent_thread_id="parent",
        child_thread_id="child",
        parent_model=EXPLORER_PARENT_MODEL,
        model=EXPLORER_MODEL,
        reasoning_effort=EXPLORER_REASONING_EFFORT,
        parent_sandbox_mode=EXPLORER_SANDBOX_MODE,
        sandbox_mode=EXPLORER_SANDBOX_MODE,
        approval_policy="never",
        network_policy="restricted",
        native_target_execution_isolation="enforced",
        native_credential_isolation="enforced",
        native_lsp_status="unsupported",
        native_tree_sitter_status="supported",
        tool_surface_digest=EXPLORER_TOOL_SURFACE_DIGEST,
        definition_digest=_DEFINITION_DIGEST,
        observed_at=new_observed_at(),
    )


def _expected(digest: str) -> dict[str, str]:
    return {
        "expected_cli_version": "codex-cli 1.2.3",
        "expected_model_catalog_digest": digest,
        "expected_probe_policy_identity": PROBE_POLICY_IDENTITY,
        "expected_definition_digest": _DEFINITION_DIGEST,
        "expected_role": EXPLORER_PROBE_ROLE,
        "expected_agent_path": EXPLORER_PROBE_TASK_NAME,
        "expected_parent_thread_id": "parent",
        "expected_child_thread_id": "child",
        "expected_native_target_execution_isolation": "enforced",
        "expected_native_credential_isolation": "enforced",
        "expected_native_lsp_status": "unsupported",
        "expected_native_tree_sitter_status": "supported",
    }


def _validate(attestation: ExplorerConformanceAttestation, digest: str) -> None:
    validate_explorer_attestation(attestation, **_expected(digest))


def test_luna_catalog_requires_exact_model_and_max_effort() -> None:
    digest = validate_codex_luna_catalog(_catalog())
    assert digest.startswith("sha256:")
    without_max = _catalog().replace(b'"max"', b'"medium"')
    with pytest.raises(ValueError, match="max reasoning"):
        validate_codex_luna_catalog(without_max)


def test_luna_catalog_projection_changes_only_execution_surface() -> None:
    raw = json.dumps(
        {
            "models": [
                {
                    "slug": "gpt-5.6-sol",
                    "tool_mode": "code_mode",
                    "sentinel": {"unchanged": True},
                },
                json.loads(_catalog())["models"][0],
            ],
            "metadata": {"catalog": "bundled"},
        }
    ).encode()

    projection = project_codex_luna_catalog(raw)
    projected = json.loads(projection.canonical_projected_bytes)

    assert projected["models"][0] == {
        "slug": "gpt-5.6-sol",
        "tool_mode": "code_mode",
        "sentinel": {"unchanged": True},
    }
    assert projected["models"][1]["tool_mode"] == "direct"
    assert projected["models"][1]["apply_patch_tool_type"] is None
    assert projected["metadata"] == {"catalog": "bundled"}
    assert projection.bundled_sha256.startswith("sha256:")
    assert projection.projected_sha256.startswith("sha256:")
    assert projection.bundled_sha256 != projection.projected_sha256
    assert project_codex_luna_catalog(raw) == projection


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tool_mode", "direct", "bundled tool_mode"),
        ("apply_patch_tool_type", None, "bundled apply_patch_tool_type"),
    ],
)
def test_luna_catalog_projection_rejects_unexpected_bundled_surface(
    field: str, value: object, message: str
) -> None:
    parsed = json.loads(_catalog())
    parsed["models"][0][field] = value
    with pytest.raises(ValueError, match=message):
        project_codex_luna_catalog(json.dumps(parsed).encode())


def test_luna_catalog_projection_rejects_duplicate_luna() -> None:
    parsed = json.loads(_catalog())
    parsed["models"].append(dict(parsed["models"][0]))
    with pytest.raises(ValueError, match="exactly one"):
        project_codex_luna_catalog(json.dumps(parsed).encode())


@pytest.mark.parametrize(
    ("observed_at", "message"),
    [
        ("2000-01-01T00:00:00+00:00", "stale"),
        ("2999-01-01T00:00:00+00:00", "future"),
    ],
)
def test_attestation_rejects_replayed_or_future_evidence(observed_at: str, message: str) -> None:
    digest = validate_codex_luna_catalog(_catalog())
    with pytest.raises(ValueError, match=message):
        _validate(replace(_attestation(digest), observed_at=observed_at), digest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cache_miss", False),
        ("schema_version", 1),
        ("model", "gpt-5.6-sol"),
        ("reasoning_effort", "high"),
        ("parent_sandbox_mode", "workspace-write"),
        ("sandbox_mode", "workspace-write"),
        ("network_policy", "unrestricted"),
        ("native_target_execution_isolation", "failed-open"),
        ("native_credential_isolation", "failed-open"),
        ("native_lsp_status", "supported"),
        ("native_tree_sitter_status", "unsupported"),
        ("tool_surface_digest", "sha256:stale"),
        ("definition_digest", "sha256:stale"),
    ],
)
def test_attestation_rejects_stale_or_weakened_evidence(field: str, value: object) -> None:
    digest = validate_codex_luna_catalog(_catalog())
    with pytest.raises(ValueError, match=field):
        _validate(replace(_attestation(digest), **{field: value}), digest)


@pytest.mark.parametrize(
    ("field", "value", "expected_field"),
    [
        ("role", "wrong-role", "expected_role"),
        ("agent_path", "agents/wrong.toml", "expected_agent_path"),
        ("parent_thread_id", "wrong-parent", "expected_parent_thread_id"),
        ("child_thread_id", "wrong-child", "expected_child_thread_id"),
        (
            "native_target_execution_isolation",
            "failed-open",
            "expected_native_target_execution_isolation",
        ),
        (
            "native_credential_isolation",
            "failed-open",
            "expected_native_credential_isolation",
        ),
        ("native_lsp_status", "supported", "expected_native_lsp_status"),
        ("native_tree_sitter_status", "unsupported", "expected_native_tree_sitter_status"),
    ],
)
def test_attestation_requires_exact_authoritative_observations(
    field: str, value: str, expected_field: str
) -> None:
    digest = validate_codex_luna_catalog(_catalog())
    with pytest.raises(ValueError, match=field):
        validate_explorer_attestation(
            replace(_attestation(digest), **{field: value}),
            **_expected(digest),
        )


@pytest.mark.parametrize(
    ("field", "invalid_value", "expected_field", "message"),
    [
        (
            "native_target_execution_isolation",
            "unsupported",
            "expected_native_target_execution_isolation",
            "enforced or failed-open",
        ),
        (
            "native_credential_isolation",
            "unsupported",
            "expected_native_credential_isolation",
            "enforced or failed-open",
        ),
        (
            "native_lsp_status",
            "enforced",
            "expected_native_lsp_status",
            "supported or unsupported",
        ),
        (
            "native_tree_sitter_status",
            "enforced",
            "expected_native_tree_sitter_status",
            "supported or unsupported",
        ),
    ],
)
def test_attestation_rejects_invalid_native_observation_literals(
    field: str, invalid_value: str, expected_field: str, message: str
) -> None:
    digest = validate_codex_luna_catalog(_catalog())
    expected = _expected(digest)
    expected[expected_field] = invalid_value
    with pytest.raises(ValueError, match=message):
        validate_explorer_attestation(
            replace(_attestation(digest), **{field: invalid_value}), **expected
        )


@pytest.mark.parametrize(
    ("execution", "credential"),
    [("failed-open", "enforced"), ("enforced", "failed-open")],
)
def test_release_readiness_rejects_failed_open_required_boundaries(
    execution: str, credential: str
) -> None:
    digest = validate_codex_luna_catalog(_catalog())
    with pytest.raises(ValueError, match="release readiness"):
        validate_explorer_release_readiness(
            replace(
                _attestation(digest),
                native_target_execution_isolation=execution,
                native_credential_isolation=credential,
            )
        )


@pytest.mark.parametrize(
    ("lsp", "tree_sitter"),
    [("supported", "supported"), ("unsupported", "unsupported")],
)
def test_release_readiness_allows_optional_native_observations(lsp: str, tree_sitter: str) -> None:
    digest = validate_codex_luna_catalog(_catalog())
    validate_explorer_release_readiness(
        replace(_attestation(digest), native_lsp_status=lsp, native_tree_sitter_status=tree_sitter)
    )


@pytest.mark.parametrize("field", ["native_lsp_status", "native_tree_sitter_status"])
def test_release_readiness_rejects_invalid_optional_observations(field: str) -> None:
    digest = validate_codex_luna_catalog(_catalog())
    with pytest.raises(ValueError, match="supported or unsupported"):
        validate_explorer_release_readiness(replace(_attestation(digest), **{field: "unknown"}))


def test_publish_is_atomic_unique_and_round_trips(tmp_path: Path) -> None:
    digest = validate_codex_luna_catalog(_catalog())
    attestation = _attestation(digest)
    output = publish_explorer_attestation(
        tmp_path,
        attestation,
        **_expected(digest),
    )
    assert output.name == "codex-explorer-conformance-v6.json"
    assert (tmp_path / f"{output.name}.sha256").is_file()
    assert read_explorer_attestation(output) == attestation
    assert validate_published_explorer_release_readiness(output) == attestation
    with pytest.raises(FileExistsError):
        publish_explorer_attestation(
            tmp_path,
            attestation,
            **_expected(digest),
        )


def test_publication_rejects_a_tampered_expected_authority(tmp_path: Path) -> None:
    digest = validate_codex_luna_catalog(_catalog())
    attestation = _attestation(digest)
    expected = _expected(digest)
    expected["expected_child_thread_id"] = "wrong-child"
    with pytest.raises(ValueError, match="child_thread_id"):
        publish_explorer_attestation(tmp_path, attestation, **expected)
    assert not (tmp_path / "codex-explorer-conformance-v6.json").exists()


def test_read_requires_the_exact_v6_schema(tmp_path: Path) -> None:
    digest = validate_codex_luna_catalog(_catalog())
    attestation = _attestation(digest)
    raw = json.loads(
        publish_explorer_attestation(
            tmp_path,
            attestation,
            **_expected(digest),
        ).read_text(encoding="utf-8")
    )
    missing_field = dict(raw)
    missing_field.pop("parent_sandbox_mode")
    extra_field = {**raw, "legacy_parent_sandbox": EXPLORER_SANDBOX_MODE}
    invalid_path = tmp_path / "invalid.json"
    for invalid in (missing_field, extra_field):
        invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
        with pytest.raises(ValueError, match="fields do not match the schema"):
            read_explorer_attestation(invalid_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cli_version", "codex-cli substituted"),
        ("model_catalog_digest", "sha256:" + ("c" * 64)),
        ("probe_policy_identity", "policy-substituted"),
        ("definition_digest", "sha256:" + ("e" * 64)),
        ("role", "substituted-role"),
        ("agent_path", "substituted-path"),
        ("parent_thread_id", "substituted-parent"),
        ("child_thread_id", "substituted-child"),
    ],
)
def test_published_release_readiness_rejects_payload_tampering(
    tmp_path: Path, field: str, value: str
) -> None:
    digest = validate_codex_luna_catalog(_catalog())
    output = publish_explorer_attestation(tmp_path, _attestation(digest), **_expected(digest))
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload[field] = value
    output.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="sidecar digest mismatch"):
        validate_published_explorer_release_readiness(output)


def _rollout_events() -> tuple[list[dict], list[dict]]:
    parent_events = [
        {
            "type": "turn_context",
            "payload": {
                "model": EXPLORER_PARENT_MODEL,
                "sandbox_policy": {"type": EXPLORER_SANDBOX_MODE},
                "approval_policy": "never",
                "permission_profile": {"network": "restricted"},
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "spawn_agent",
                "call_id": "spawn",
                "arguments": json.dumps(
                    {
                        "task_name": "capability_probe",
                        "agent_type": "semantic-code-navigator",
                        "fork_turns": "none",
                    }
                ),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "spawn",
                "output": json.dumps({"task_name": "/root/capability_probe"}),
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "sub_agent_activity",
                "event_id": "spawn",
                "agent_thread_id": "child",
                "agent_path": "/root/capability_probe",
                "kind": "started",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "wait_agent",
                "call_id": "wait",
                "arguments": json.dumps({"timeout_ms": 3_600_000}),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "wait",
                "output": json.dumps({"message": "Wait completed.", "timed_out": False}),
            },
        },
    ]
    child_events = [
        {
            "type": "session_meta",
            "payload": {
                "id": "child",
                "cli_version": "1.2.3",
                "base_instructions": {"text": "out sha256:def"},
                "source": {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": "parent",
                            "agent_role": "semantic-code-navigator",
                            "agent_path": "/root/capability_probe",
                        }
                    }
                },
            },
        },
        {
            "type": "turn_context",
            "payload": {
                "model": EXPLORER_MODEL,
                "effort": EXPLORER_REASONING_EFFORT,
                "sandbox_policy": {"type": EXPLORER_SANDBOX_MODE},
                "approval_policy": "never",
                "permission_profile": {"network": "restricted"},
            },
        },
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ]
    return parent_events, child_events


def test_rollout_identity_uses_authoritative_record_owners() -> None:
    parent_events, child_events = _rollout_events()
    evidence = assert_generated_codex_child_delivery(
        parent_events,
        child_events,
        parent_id="parent",
        agent_role="semantic-code-navigator",
        output_discipline_digest="out",
        expected_parent_model=EXPLORER_PARENT_MODEL,
        expected_model=EXPLORER_MODEL,
        expected_reasoning_effort=EXPLORER_REASONING_EFFORT,
        expected_parent_sandbox_mode=EXPLORER_SANDBOX_MODE,
        expected_sandbox_mode=EXPLORER_SANDBOX_MODE,
        expected_definition_digest="sha256:def",
    )
    assert evidence.child_id == "child"
    assert evidence.cli_version == "1.2.3"
    assert evidence.parent_model == EXPLORER_PARENT_MODEL
    assert evidence.parent_sandbox_mode == EXPLORER_SANDBOX_MODE


def test_rollout_identity_requires_authoritative_parent_turn_context() -> None:
    parent_events, child_events = _rollout_events()
    parent_events = [event for event in parent_events if event["type"] != "turn_context"]
    with pytest.raises(AssertionError, match="parent rollout omitted turn_context"):
        assert_generated_codex_child_delivery(
            parent_events,
            child_events,
            parent_id="parent",
            agent_role="semantic-code-navigator",
            output_discipline_digest="out",
            expected_model=EXPLORER_MODEL,
            expected_reasoning_effort=EXPLORER_REASONING_EFFORT,
            expected_parent_sandbox_mode=EXPLORER_SANDBOX_MODE,
            expected_sandbox_mode=EXPLORER_SANDBOX_MODE,
            expected_definition_digest="sha256:def",
        )


def test_rollout_identity_rejects_requested_settings_without_turn_context() -> None:
    parent_events, child_events = _rollout_events()
    child_events = [event for event in child_events if event["type"] != "turn_context"]
    with pytest.raises(AssertionError, match="turn_context"):
        assert_generated_codex_child_delivery(
            parent_events,
            child_events,
            parent_id="parent",
            agent_role="semantic-code-navigator",
            output_discipline_digest="out",
            expected_model=EXPLORER_MODEL,
            expected_reasoning_effort=EXPLORER_REASONING_EFFORT,
            expected_parent_sandbox_mode=EXPLORER_SANDBOX_MODE,
            expected_sandbox_mode=EXPLORER_SANDBOX_MODE,
            expected_definition_digest="sha256:def",
        )
