"""Ratchet the accepted context-admission contract decision."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import CONTEXT_ADMISSION_COVERAGE

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION = REPO_ROOT / "docs/decisions/0007-context-admission.md"
DECISION_INDEX = REPO_ROOT / "docs/decisions/README.md"

pytestmark = pytest.mark.small

CODEX_VERSION = "0.145.0"
CODEX_REVISION = "25af12f7e61572b0bc18ddb1008be543b91519b0"
REQUIRED_PINNED_CODEX_PATHS = (
    "codex-rs/core/src/session/context_window.rs",
    "codex-rs/core/src/context_manager/history.rs",
    "codex-rs/features/src/lib.rs",
    "codex-rs/core/src/tools/spec_plan.rs",
    "codex-rs/core/src/tools/handlers/get_context_remaining.rs",
    "codex-rs/app-server-protocol/src/protocol/v2/thread.rs",
    "codex-rs/app-server/README.md",
    "codex-rs/protocol/src/protocol.rs",
    "codex-rs/utils/string/src/truncate.rs",
    "codex-rs/hooks/schema/generated",
)
CURRENT_HOOK_COVERAGE_URL = (
    "https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md#hooks"
)
EXPECTED_DEPENDENCY_EDGES = {
    "#4333 C1 -> #4334 C2",
    "#4333 C1 + #4334 C2 + #4335 C3 -> #4336 C4",
    "#4333 C1 + #4334 C2 + #4335 C3 -> #4337 C5",
    "#4333 C1 + #4334 C2 -> #4338 C8",
    "#4319/#4320/#4321/#4322/#4325/#4326/#4327 -> #4335 C3 artifact authority",
    "#4334 C2 + #4336 C4 + #4337 C5 + #4324 + #4338 C8 -> #4339 C6",
    "#4334 C2 + #4335 C3 + #4271 + #4338 C8 -> #4340 C7",
}

REQUIRED_HEADINGS = (
    "Context",
    "Decision",
    "Admission boundary and authority",
    "Protocol version 1",
    "State, witnesses, and atomic batches",
    "Accounting and identity invariants",
    "Protected reserve and epoch isolation",
    "Producer coverage matrix",
    "Authority unavailable and byte ceilings",
    "Upstream authority request",
    "Privacy and observability",
    "Capability decision for Codex 0.145.0",
    "Protocol evolution",
    "Downstream dependency graph",
    "Non-goals",
    "Traceability",
)

TRACEABILITY_TERMS = {
    "INV-1": "model-visible admission boundary",
    "INV-2": "stable identities",
    "INV-3": "atomic reserve/commit/release protocol",
    "INV-4": "version-pinned coverage matrix",
    "INV-5": "token_budget/get_context_remaining",
    "INV-6": "upstream Codex contract",
    "INV-7": "privacy-safe observability",
    "OUT-1": "versioned admission protocol and state machine",
    "OUT-2": "producer/control-point coverage matrix",
    "OUT-3": "accounting and identity invariants",
    "OUT-4": "failure and reconciliation semantics",
    "OUT-5": "authoritative token accounting",
    "OUT-6": "upstream Codex request",
    "OUT-7": "implementation dependency graph",
    "NG-1": "no enforcement or numeric budget defaults",
    "NG-2": "retain existing raw per-producer ceilings",
    "NG-3": "bytes are not an exact token proxy",
    "NG-4": "digest is not an access capability or deduplication identity",
    "AC-1": "every model-visible producer",
    "AC-2": "idempotent reservation keys and compaction/window reset rules",
    "AC-3": "outstanding concurrent calls and protected reserve",
    "AC-4": "Codex claims cite tested version and primary sources",
    "AC-5": "C2-C8 use the shared accounting contract",
}

ALLOWED_TRACEABILITY_TARGETS = (
    "CONTEXT_ADMISSION_PROTOCOL_VERSION",
    "CONTEXT_ADMISSION_COVERAGE",
    "reduce_context_admission",
    "replay_context_admission",
    "test_context_admission_contract.py",
    "test_context_admission_coverage.py",
    "test_context_admission_reducer.py",
    "test_context_admission_state_machine.py",
    "test_context_admission_decision.py",
    *REQUIRED_HEADINGS,
)


@pytest.fixture(scope="module")
def decision_text() -> str:
    assert DECISION.exists(), "ADR-0007 must exist"
    return DECISION.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    lines = text.splitlines()
    start = -1
    level = 0
    for index, line in enumerate(lines):
        match = re.fullmatch(r"(#{2,6}) (.+)", line)
        if match is not None and match.group(2) == heading:
            start = index + 1
            level = len(match.group(1))
            break
    assert start >= 0, f"missing ADR heading: {heading}"
    end = len(lines)
    for index in range(start, len(lines)):
        match = re.match(r"(#{2,6}) ", lines[index])
        if match is not None and len(match.group(1)) <= level:
            end = index
            break
    return "\n".join(lines[start:end])


def _traceability_rows(text: str) -> dict[str, tuple[str, str]]:
    rows: dict[str, tuple[str, str]] = {}
    for row_id, requirement, target in re.findall(
        r"^\|\s*((?:INV|OUT|NG|AC)-\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$",
        _section(text, "Traceability"),
        flags=re.MULTILINE,
    ):
        assert row_id not in rows, f"duplicate traceability row: {row_id}"
        rows[row_id] = (requirement.strip(), target.strip())
    return rows


def test_context_admission_decision_is_indexed(decision_text: str) -> None:
    assert "**Status:** Accepted" in decision_text
    assert "#4333" in decision_text
    assert "C1" in decision_text
    assert "0007-context-admission.md" in DECISION_INDEX.read_text(encoding="utf-8")


def test_decision_has_the_normative_contract_sections(decision_text: str) -> None:
    for heading in REQUIRED_HEADINGS:
        assert re.search(
            rf"^##+ {re.escape(heading)}\s*$",
            decision_text,
            flags=re.MULTILINE,
        ), f"missing ADR heading: {heading}"


def test_decision_references_the_executable_protocol(decision_text: str) -> None:
    for required in [
        "CONTEXT_ADMISSION_PROTOCOL_VERSION",
        "reduce_context_admission",
        "replay_context_admission",
        "CONTEXT_ADMISSION_COVERAGE",
    ]:
        assert required in decision_text
    protocol = _section(decision_text, "Protocol version 1")
    assert re.search(r"\bprotocol version 1\b", protocol, flags=re.IGNORECASE)


def test_traceability_freezes_the_entire_issue_scope(decision_text: str) -> None:
    rows = _traceability_rows(decision_text)
    assert set(rows) == set(TRACEABILITY_TERMS)
    for row_id, required_term in TRACEABILITY_TERMS.items():
        requirement, target = rows[row_id]
        assert required_term.casefold() in requirement.casefold(), row_id
        assert any(allowed in target for allowed in ALLOWED_TRACEABILITY_TARGETS), row_id


def test_codex_capability_claims_are_version_pinned_to_primary_evidence(
    decision_text: str,
) -> None:
    capability = _section(decision_text, "Capability decision for Codex 0.145.0")
    for required in [
        CODEX_VERSION,
        CODEX_REVISION,
        "token_budget",
        "get_context_remaining",
        "PreCompact",
        "PostCompact",
    ]:
        assert required in capability
    for source_path in REQUIRED_PINNED_CODEX_PATHS:
        assert CODEX_REVISION in capability
        assert source_path in capability
    assert CURRENT_HOOK_COVERAGE_URL in capability


def test_authority_unavailable_behavior_preserves_byte_boundaries(
    decision_text: str,
) -> None:
    section = _section(decision_text, "Authority unavailable and byte ceilings").casefold()
    for required in [
        "watermark_unavailable",
        "upstream_gated",
        "raw-byte",
        "no numeric",
        "independent",
    ]:
        assert required in section


def test_decision_freezes_the_complete_producer_matrix(decision_text: str) -> None:
    coverage = _section(decision_text, "Producer coverage matrix")
    actual_rows = tuple(
        tuple(cell.strip().strip("`") for cell in line.strip().strip("|").split("|"))
        for line in coverage.splitlines()
        if line.startswith("| `")
    )
    expected_rows = tuple(
        (
            row.surface.name,
            row.evidence[0].configuration_mode,
            row.evidence[0].claim_id,
            row.control_point_owner,
            row.observation_state.name,
            row.authority_state.name,
        )
        for row in CONTEXT_ADMISSION_COVERAGE
    )

    assert actual_rows == expected_rows


def test_upstream_request_contains_all_three_authority_parts_and_minimum_fields(
    decision_text: str,
) -> None:
    request = _section(decision_text, "Upstream authority request")
    for required in [
        "atomic snapshot/reservation",
        "generated-output maximum",
        "synchronous blocking",
        "final ordered batch",
        "canonical representation manifest",
        "receiver fence",
        "durable/queryable journal",
        "history staging",
        "request inclusion",
        "provider acceptance",
        "output-usage reconciliation",
        "rollback",
        "truncation/compaction replacement",
        "authoritative reconciliation",
        "request_id",
        "batch_id",
        "ordered members",
        "reservation IDs",
        "thread/turn/agent lineage",
        "admission sequence",
        "window ID/number",
        "model/tokenizer identity",
        "snapshot sequence",
        "measurement kind/source",
        "active/hard-limit/remaining/proposed/max-output counts",
        "reserve class",
        "representation revision",
    ]:
        assert required in request


def test_privacy_table_freezes_field_governance(decision_text: str) -> None:
    privacy = _section(decision_text, "Privacy and observability")
    for required in [
        "Runtime/audit fields",
        "Lineage and source locator fields",
        "Aggregate telemetry fields",
        "Forbidden content",
        "Purpose",
        "Maximum length/cardinality",
        "Retention",
        "Access",
        "Deletion",
        "Export",
        "opaque",
        "lineage",
        "source locator",
        "content",
        "absolute paths",
        "bearer tokens",
        "content/artifact hashes",
    ]:
        assert required in privacy


REQUIRED_RUNTIME_AUDIT_FIELDS = (
    "protocol_version",
    "aggregate_revision",
    "admission_sequence",
    "event_id",
    "reservation_id",
    "witness_id",
    "batch_id",
    "request_id",
    "reservation_key",
    "occurrence_id",
    "attempt_id",
    "delivery_occurrence_id",
    "generation_reservation_id",
    "reason_code",
    "requested_count",
    "available_ordinary_count",
    "available_protected_count",
    "reserved_count",
    "committed_input_count",
    "unresolved_input_count",
    "retained_unresolved_count",
    "maximum_allowance",
    "exact_terminal_usage",
    "injected_count",
    "priority",
    "predicted_authoritative_maximum",
    "active_count",
    "hard_limit",
    "remaining_count",
    "highest_admitted_dispatch_sequence",
    "representation_revision",
    "tested_version",
    "tested_revision",
    "publication_revision",
    "checked_at",
    "freshness_policy",
    "verifier",
    "configuration_mode",
    "backend",
    "control_point_owner",
)

REQUIRED_LINEAGE_FIELDS = (
    "root_session_id",
    "current_session_id",
    "root_agent_id",
    "current_agent_id",
    "parent_agent_id",
    "root_thread_id",
    "current_thread_id",
    "parent_thread_id",
    "fork_occurrence_id",
    "turn_id",
    "producer_surface",
    "producer_instance_id",
    "tool_call_id",
    "model_item_id",
    "dispatch_identity",
    "source_locator",
)

REQUIRED_AGGREGATE_FIELDS = (
    "state",
    "reason_code",
    "version",
)

REQUIRED_FORBIDDEN_CONTENT = (
    "model content",
    "payloads",
    "prompts",
    "tool results",
    "absolute paths",
    "bearer",
    "credentials",
    "API keys",
    "session cookies",
    "content/artifact hashes",
    "sha256:",
    "blake2:",
    "content:",
)

CONCRETE_MAXIMA_HINTS = (
    "96 ASCII",
    "64 ASCII",
    "128 ASCII",
    "256 ASCII",
    "10 ASCII",
    "10⁴",
    "64-bit non-negative",
    "kebab-case",
    "ISO-8601",
    "30 days",
    "no `",
    "no absolute",
    "no secrets",
    "no home-directory",
    "no URLs",
)


def test_privacy_table_freezes_complete_runtime_audit_field_inventory(
    decision_text: str,
) -> None:
    privacy = _section(decision_text, "Privacy and observability")
    missing = [field for field in REQUIRED_RUNTIME_AUDIT_FIELDS if field not in privacy]
    assert not missing, f"missing runtime/audit fields in ADR-0007 privacy table: {missing}"


def test_privacy_table_freezes_complete_lineage_field_inventory(
    decision_text: str,
) -> None:
    privacy = _section(decision_text, "Privacy and observability")
    missing = [field for field in REQUIRED_LINEAGE_FIELDS if field not in privacy]
    assert not missing, f"missing lineage fields in ADR-0007 privacy table: {missing}"


def test_privacy_table_freezes_aggregate_telemetry_field_inventory(
    decision_text: str,
) -> None:
    privacy = _section(decision_text, "Privacy and observability")
    missing = [field for field in REQUIRED_AGGREGATE_FIELDS if field not in privacy]
    assert not missing, f"missing aggregate telemetry fields in ADR-0007 privacy table: {missing}"


def test_privacy_table_forbids_complete_content_categories(decision_text: str) -> None:
    privacy = _section(decision_text, "Privacy and observability").casefold()
    missing = [item for item in REQUIRED_FORBIDDEN_CONTENT if item.casefold() not in privacy]
    assert not missing, f"missing forbidden-content categories in ADR-0007: {missing}"


def test_privacy_table_requires_concrete_field_maxima(decision_text: str) -> None:
    privacy = _section(decision_text, "Privacy and observability")
    missing = [hint for hint in CONCRETE_MAXIMA_HINTS if hint not in privacy]
    assert not missing, f"ADR-0007 privacy table must specify concrete maxima: {missing}"


def test_decision_keeps_issue_non_goals_explicit(decision_text: str) -> None:
    non_goals = _section(decision_text, "Non-goals").casefold()
    for required in [
        "enforcement",
        "numeric budget defaults",
        "existing raw per-producer ceilings",
        "bytes",
        "exact token proxy",
        "digest",
        "access capability",
        "deduplication identity",
    ]:
        assert required in non_goals


def test_dependency_graph_freezes_exact_edges(decision_text: str) -> None:
    graph = _section(decision_text, "Downstream dependency graph")
    actual_edges = {line.strip() for line in graph.splitlines() if "->" in line}
    assert actual_edges == EXPECTED_DEPENDENCY_EDGES
