"""Freeze context-admission producer coverage and its supporting evidence."""

from __future__ import annotations

from typing import NamedTuple

import pytest

from autoskillit.core import (
    CONTEXT_ADMISSION_COVERAGE,
    CoverageEvidenceKind,
    CoverageState,
    ProducerSurface,
    resolve_context_admission_coverage,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

CHECKED_AT = "2026-07-23"
FRESHNESS_POLICY = "verify_on_version_or_configuration_change"
CODEX_REVISION = "25af12f7e61572b0bc18ddb1008be543b91519b0"
REASON_CODE = "authoritative-watermark-unavailable"


class ExpectedEvidence(NamedTuple):
    claim_id: str
    kind: str
    backend: str
    configuration_mode: str
    verifier: str
    source_locator: str
    tested_version: str
    tested_revision: str
    checked_at: str
    freshness_policy: str


class ExpectedCoverageRow(NamedTuple):
    surface: str
    control_point_owner: str
    observation_state: str
    authority_state: str
    evidence: tuple[ExpectedEvidence, ...]
    reason_code: str


def _autoskillit_evidence(claim_id: str, source_locator: str) -> ExpectedEvidence:
    return ExpectedEvidence(
        claim_id,
        "AUTOSKILLIT_SOURCE",
        "autoskillit",
        "default",
        "source_inspection",
        source_locator,
        "0.10.890",
        "ac8f653a00d2",
        CHECKED_AT,
        FRESHNESS_POLICY,
    )


def _codex_evidence(claim_id: str) -> ExpectedEvidence:
    return ExpectedEvidence(
        claim_id,
        "CODEX_SOURCE",
        "codex",
        "default",
        "source_inspection",
        "codex-rs/core/src/context_manager/history.rs",
        "0.145.0",
        CODEX_REVISION,
        CHECKED_AT,
        FRESHNESS_POLICY,
    )


def _gap_evidence(claim_id: str) -> ExpectedEvidence:
    return ExpectedEvidence(
        claim_id,
        "INFERENCE",
        "codex",
        "default",
        "source_gap_analysis",
        "docs/decisions/0007-context-admission.md",
        "0.145.0",
        CODEX_REVISION,
        CHECKED_AT,
        FRESHNESS_POLICY,
    )


def _row(
    surface: str,
    owner: str,
    observation_state: str,
    evidence: ExpectedEvidence,
) -> ExpectedCoverageRow:
    return ExpectedCoverageRow(
        surface,
        owner,
        observation_state,
        "UPSTREAM_GATED",
        (evidence,),
        REASON_CODE,
    )


EXPECTED_COVERAGE = (
    _row(
        "NATIVE_SHELL",
        "shell_capture_hook",
        "VERIFIED",
        _autoskillit_evidence(
            "COV-NATIVE-SHELL",
            "src/autoskillit/hooks/shell_capture_hook.py",
        ),
    ),
    _row(
        "UNIFIED_EXEC_AND_WRITE_STDIN",
        "codex_host",
        "PARTIAL",
        _codex_evidence("COV-UNIFIED-EXEC-AND-WRITE-STDIN"),
    ),
    _row(
        "APPLY_PATCH",
        "codex_host",
        "PARTIAL",
        _codex_evidence("COV-APPLY-PATCH"),
    ),
    _row(
        "AUTOSKILLIT_MCP",
        "track_response_size",
        "VERIFIED",
        _autoskillit_evidence(
            "COV-AUTOSKILLIT-MCP",
            "src/autoskillit/server/_notify.py",
        ),
    ),
    _row(
        "EXTERNAL_MCP",
        "fastmcp_client",
        "PARTIAL",
        _codex_evidence("COV-EXTERNAL-MCP"),
    ),
    _row(
        "AUTOSKILLIT_LOCAL_FUNCTION",
        "local_function_dispatch",
        "VERIFIED",
        _autoskillit_evidence(
            "COV-AUTOSKILLIT-LOCAL-FUNCTION",
            "src/autoskillit/execution/headless/_headless_helpers.py",
        ),
    ),
    _row(
        "OTHER_LOCAL_FUNCTION",
        "codex_host",
        "PARTIAL",
        _codex_evidence("COV-OTHER-LOCAL-FUNCTION"),
    ),
    _row(
        "MCP_RESOURCE",
        "fastmcp_client",
        "PARTIAL",
        _codex_evidence("COV-MCP-RESOURCE"),
    ),
    _row(
        "CLIENT_PROVIDER_RETRIEVAL",
        "codex_host",
        "UPSTREAM_GATED",
        _gap_evidence("COV-CLIENT-PROVIDER-RETRIEVAL"),
    ),
    _row(
        "CODE_MODE_AGGREGATE",
        "codex_host",
        "PARTIAL",
        _codex_evidence("COV-CODE-MODE-AGGREGATE"),
    ),
    _row(
        "HOSTED_SPECIALIZED_TOOL",
        "codex_host",
        "PARTIAL",
        _codex_evidence("COV-HOSTED-SPECIALIZED-TOOL"),
    ),
    _row(
        "HOOK_FEEDBACK",
        "hook_registry",
        "VERIFIED",
        _autoskillit_evidence(
            "COV-HOOK-FEEDBACK",
            "src/autoskillit/hook_registry.py",
        ),
    ),
    _row(
        "TOOL_ARGUMENT",
        "final_request_assembler",
        "PARTIAL",
        _codex_evidence("COV-TOOL-ARGUMENT"),
    ),
    _row(
        "TOOL_RESULT_ENVELOPE",
        "final_request_assembler",
        "PARTIAL",
        _codex_evidence("COV-TOOL-RESULT-ENVELOPE"),
    ),
    _row(
        "USER_PROMPT",
        "final_request_assembler",
        "PARTIAL",
        _codex_evidence("COV-USER-PROMPT"),
    ),
    _row(
        "ASSISTANT_OUTPUT_HISTORY",
        "final_request_assembler",
        "PARTIAL",
        _codex_evidence("COV-ASSISTANT-OUTPUT-HISTORY"),
    ),
    _row(
        "SKILL_PLUGIN_CONTEXT",
        "final_request_assembler",
        "PARTIAL",
        _codex_evidence("COV-SKILL-PLUGIN-CONTEXT"),
    ),
    _row(
        "OTHER_CONTEXT_INJECTION",
        "final_request_assembler",
        "UPSTREAM_GATED",
        _gap_evidence("COV-OTHER-CONTEXT-INJECTION"),
    ),
    _row(
        "HEADLESS_CHILD_PROMPT",
        "headless_prompt_builder",
        "VERIFIED",
        _autoskillit_evidence(
            "COV-HEADLESS-CHILD-PROMPT",
            "src/autoskillit/execution/headless/_headless_helpers.py",
        ),
    ),
    _row(
        "PARENT_VISIBLE_CHILD_DELIVERY",
        "child_delivery_receipt",
        "VERIFIED",
        _autoskillit_evidence(
            "COV-PARENT-VISIBLE-CHILD-DELIVERY",
            "src/autoskillit/server/_recipe_delivery.py",
        ),
    ),
    _row(
        "COMPACTION_MODEL_WINDOW_TRANSITION",
        "compaction_receiver",
        "PARTIAL",
        _codex_evidence("COV-COMPACTION-MODEL-WINDOW-TRANSITION"),
    ),
)

EXPECTED_SURFACES = (
    "NATIVE_SHELL",
    "UNIFIED_EXEC_AND_WRITE_STDIN",
    "APPLY_PATCH",
    "AUTOSKILLIT_MCP",
    "EXTERNAL_MCP",
    "AUTOSKILLIT_LOCAL_FUNCTION",
    "OTHER_LOCAL_FUNCTION",
    "MCP_RESOURCE",
    "CLIENT_PROVIDER_RETRIEVAL",
    "CODE_MODE_AGGREGATE",
    "HOSTED_SPECIALIZED_TOOL",
    "HOOK_FEEDBACK",
    "TOOL_ARGUMENT",
    "TOOL_RESULT_ENVELOPE",
    "USER_PROMPT",
    "ASSISTANT_OUTPUT_HISTORY",
    "SKILL_PLUGIN_CONTEXT",
    "OTHER_CONTEXT_INJECTION",
    "HEADLESS_CHILD_PROMPT",
    "PARENT_VISIBLE_CHILD_DELIVERY",
    "COMPACTION_MODEL_WINDOW_TRANSITION",
)


def _coverage_projection() -> tuple[ExpectedCoverageRow, ...]:
    return tuple(
        ExpectedCoverageRow(
            row.surface.name,
            row.control_point_owner,
            row.observation_state.name,
            row.authority_state.name,
            tuple(
                ExpectedEvidence(
                    item.claim_id,
                    item.kind.name,
                    item.backend,
                    item.configuration_mode,
                    item.verifier,
                    item.source_locator,
                    item.tested_version,
                    item.tested_revision,
                    str(item.checked_at),
                    item.freshness_policy,
                )
                for item in row.evidence
            ),
            row.reason_code,
        )
        for row in CONTEXT_ADMISSION_COVERAGE
    )


def test_producer_surface_and_registry_are_independently_exhaustive() -> None:
    assert tuple(member.name for member in ProducerSurface) == EXPECTED_SURFACES
    assert tuple(row.surface.name for row in CONTEXT_ADMISSION_COVERAGE) == EXPECTED_SURFACES


def test_coverage_registry_freezes_every_row_and_evidence_field() -> None:
    assert isinstance(CONTEXT_ADMISSION_COVERAGE, tuple)
    assert _coverage_projection() == EXPECTED_COVERAGE
    assert all(isinstance(row.evidence, tuple) for row in CONTEXT_ADMISSION_COVERAGE)


def test_verified_observations_have_primary_evidence() -> None:
    primary_kinds = {
        CoverageEvidenceKind.AUTOSKILLIT_SOURCE,
        CoverageEvidenceKind.CODEX_SOURCE,
        CoverageEvidenceKind.CODEX_OFFICIAL_DOC,
        CoverageEvidenceKind.CODEX_RUNTIME_PROBE,
    }
    for row in CONTEXT_ADMISSION_COVERAGE:
        if row.observation_state is CoverageState.VERIFIED:
            assert any(item.kind in primary_kinds for item in row.evidence)
            assert all(item.kind is not CoverageEvidenceKind.INFERENCE for item in row.evidence)


def test_claim_ids_are_stable_and_unique() -> None:
    expected = tuple(f"COV-{surface.replace('_', '-')}" for surface in EXPECTED_SURFACES)
    actual = tuple(row.evidence[0].claim_id for row in CONTEXT_ADMISSION_COVERAGE)
    assert actual == expected
    assert len(actual) == len(set(actual))


def test_runtime_version_or_configuration_mismatch_degrades_deterministically() -> None:
    for row in CONTEXT_ADMISSION_COVERAGE:
        evidence = row.evidence[0]
        resolved = resolve_context_admission_coverage(
            row.surface,
            evidence.backend,
            evidence.configuration_mode,
            evidence.tested_version,
            CHECKED_AT,
        )
        assert resolved == row

        version_mismatch = resolve_context_admission_coverage(
            row.surface,
            evidence.backend,
            evidence.configuration_mode,
            f"{evidence.tested_version}-mismatch",
            CHECKED_AT,
        )
        configuration_mismatch = resolve_context_admission_coverage(
            row.surface,
            evidence.backend,
            f"{evidence.configuration_mode}-mismatch",
            evidence.tested_version,
            CHECKED_AT,
        )
        assert version_mismatch.observation_state is CoverageState.UPSTREAM_GATED
        assert version_mismatch.authority_state is CoverageState.UPSTREAM_GATED
        assert configuration_mismatch.observation_state is CoverageState.UPSTREAM_GATED
        assert configuration_mismatch.authority_state is CoverageState.UPSTREAM_GATED
        assert version_mismatch == resolve_context_admission_coverage(
            row.surface,
            evidence.backend,
            evidence.configuration_mode,
            f"{evidence.tested_version}-mismatch",
            CHECKED_AT,
        )


def test_compaction_observation_does_not_imply_authority() -> None:
    row = next(
        item
        for item in CONTEXT_ADMISSION_COVERAGE
        if item.surface is ProducerSurface.COMPACTION_MODEL_WINDOW_TRANSITION
    )
    assert row.observation_state is CoverageState.PARTIAL
    assert row.authority_state is CoverageState.UPSTREAM_GATED
