"""Static context-admission producer coverage contract."""

from __future__ import annotations

from dataclasses import dataclass

from ._type_context_admission_base import _ContractValue
from ._type_enums import CoverageEvidenceKind, CoverageState, ProducerSurface
from ._type_helpers import (
    _raise_invalid,
    _validate_bounded_text,
    _validate_canonical_tuple,
    _validate_freshness_policy,
    _validate_git_revision,
    _validate_iso_date,
    _validate_reason_code,
)


@dataclass(frozen=True, slots=True)
class CoverageEvidence(_ContractValue):
    claim_id: str
    kind: CoverageEvidenceKind
    backend: str
    configuration_mode: str
    verifier: str
    source_locator: str
    tested_version: str
    tested_revision: str
    checked_at: str
    freshness_policy: str

    def __post_init__(self) -> None:
        for value, reason, maximum in (
            (self.claim_id, "invalid_claim_id", 96),
            (self.backend, "invalid_evidence_backend", 64),
            (self.configuration_mode, "invalid_configuration_mode", 64),
            (self.verifier, "invalid_evidence_verifier", 64),
            (self.tested_version, "invalid_tested_version", 64),
        ):
            _validate_bounded_text(value, reason, maximum=maximum)
        _validate_git_revision(self.tested_revision)
        _validate_iso_date(self.checked_at)
        _validate_freshness_policy(self.freshness_policy)
        _validate_bounded_text(
            self.source_locator,
            "invalid_source_locator",
            maximum=256,
            locator=True,
        )


@dataclass(frozen=True, slots=True)
class ProducerCoverageDef(_ContractValue):
    surface: ProducerSurface
    control_point_owner: str
    observation_state: CoverageState
    authority_state: CoverageState
    evidence: tuple[CoverageEvidence, ...]
    reason_code: str

    def __post_init__(self) -> None:
        _validate_bounded_text(
            self.control_point_owner,
            "invalid_control_point_owner",
            maximum=96,
        )
        _validate_reason_code(self.reason_code)
        _validate_canonical_tuple(
            self.evidence,
            "noncanonical_coverage_evidence",
            key=lambda evidence: (
                evidence.kind.value,
                evidence.source_locator,
                evidence.claim_id,
            ),
        )
        if not self.evidence:
            _raise_invalid("coverage_evidence_required")
        if len(self.evidence) != 1:
            _raise_invalid("single_coverage_evidence_required")
        primary = tuple(
            item for item in self.evidence if item.kind is not CoverageEvidenceKind.INFERENCE
        )
        if (
            self.observation_state is CoverageState.VERIFIED
            or self.authority_state is CoverageState.VERIFIED
        ) and not primary:
            _raise_invalid("verified_coverage_requires_primary_evidence")


_VERIFIED_SURFACES = frozenset(
    {
        ProducerSurface.NATIVE_SHELL,
        ProducerSurface.AUTOSKILLIT_MCP,
        ProducerSurface.AUTOSKILLIT_LOCAL_FUNCTION,
        ProducerSurface.HOOK_FEEDBACK,
        ProducerSurface.HEADLESS_CHILD_PROMPT,
        ProducerSurface.PARENT_VISIBLE_CHILD_DELIVERY,
    }
)
_UNOBSERVABLE_SURFACES = frozenset(
    {
        ProducerSurface.CLIENT_PROVIDER_RETRIEVAL,
        ProducerSurface.OTHER_CONTEXT_INJECTION,
    }
)
_CONTROL_POINT_OWNERS = {
    ProducerSurface.NATIVE_SHELL: "shell_capture_hook",
    ProducerSurface.AUTOSKILLIT_MCP: "track_response_size",
    ProducerSurface.AUTOSKILLIT_LOCAL_FUNCTION: "local_function_dispatch",
    ProducerSurface.HOOK_FEEDBACK: "hook_registry",
    ProducerSurface.HEADLESS_CHILD_PROMPT: "headless_prompt_builder",
    ProducerSurface.PARENT_VISIBLE_CHILD_DELIVERY: "child_delivery_receipt",
    ProducerSurface.EXTERNAL_MCP: "fastmcp_client",
    ProducerSurface.MCP_RESOURCE: "fastmcp_client",
    ProducerSurface.COMPACTION_MODEL_WINDOW_TRANSITION: "compaction_receiver",
}
_LOCAL_SOURCE_LOCATORS = {
    ProducerSurface.NATIVE_SHELL: "src/autoskillit/hooks/shell_capture_hook.py",
    ProducerSurface.AUTOSKILLIT_MCP: "src/autoskillit/server/_notify.py",
    ProducerSurface.AUTOSKILLIT_LOCAL_FUNCTION: (
        "src/autoskillit/execution/headless/_headless_helpers.py"
    ),
    ProducerSurface.HOOK_FEEDBACK: "src/autoskillit/hook_registry.py",
    ProducerSurface.HEADLESS_CHILD_PROMPT: (
        "src/autoskillit/execution/headless/_headless_helpers.py"
    ),
    ProducerSurface.PARENT_VISIBLE_CHILD_DELIVERY: ("src/autoskillit/server/_recipe_delivery.py"),
}


def _coverage_row(surface: ProducerSurface, mode: str = "default") -> ProducerCoverageDef:
    native_shell_direct = surface is ProducerSurface.NATIVE_SHELL and mode == "direct"
    if surface in _VERIFIED_SURFACES:
        observation_state = (
            CoverageState.PARTIAL if native_shell_direct else CoverageState.VERIFIED
        )
        evidence_kind = CoverageEvidenceKind.AUTOSKILLIT_SOURCE
        backend = "autoskillit"
        verifier = "source_inspection"
        locator = (
            "src/autoskillit/hooks/_capture/_runner.py"
            if native_shell_direct
            else _LOCAL_SOURCE_LOCATORS[surface]
        )
        version = "0.10.1013" if native_shell_direct else "0.10.890"
        revision = (
            "548883ae5547d8a2cebc561d940c7a80ae7de47a"
            if native_shell_direct
            else "ac8f653a00d24b6be50ef285958cfb0e1b7a351b"
        )
    elif surface in _UNOBSERVABLE_SURFACES:
        observation_state = CoverageState.UPSTREAM_GATED
        evidence_kind = CoverageEvidenceKind.INFERENCE
        backend = "codex"
        verifier = "source_gap_analysis"
        locator = "docs/decisions/0007-context-admission.md"
        version = "0.145.0"
        revision = "25af12f7e61572b0bc18ddb1008be543b91519b0"
    else:
        observation_state = CoverageState.PARTIAL
        evidence_kind = CoverageEvidenceKind.CODEX_SOURCE
        backend = "codex"
        verifier = "source_inspection"
        locator = "codex-rs/core/src/context_manager/history.rs"
        version = "0.145.0"
        revision = "25af12f7e61572b0bc18ddb1008be543b91519b0"
    owner = _CONTROL_POINT_OWNERS.get(surface)
    if owner is None:
        if surface in {
            ProducerSurface.UNIFIED_EXEC_AND_WRITE_STDIN,
            ProducerSurface.APPLY_PATCH,
            ProducerSurface.OTHER_LOCAL_FUNCTION,
            ProducerSurface.CLIENT_PROVIDER_RETRIEVAL,
            ProducerSurface.CODE_MODE_AGGREGATE,
            ProducerSurface.HOSTED_SPECIALIZED_TOOL,
        }:
            owner = "codex_host"
        else:
            owner = "final_request_assembler"
    claim_id = f"COV-{surface.name.replace('_', '-')}"
    if mode != "default":
        claim_id = f"{claim_id}-{mode.replace('_', '-').upper()}"
    evidence = CoverageEvidence(
        claim_id=claim_id,
        kind=evidence_kind,
        backend=backend,
        configuration_mode=mode,
        verifier=verifier,
        source_locator=locator,
        tested_version=version,
        tested_revision=revision,
        checked_at="2026-08-23" if native_shell_direct else "2026-07-23",
        freshness_policy="verify_on_version_or_configuration_change",
    )
    return ProducerCoverageDef(
        surface=surface,
        control_point_owner=owner,
        observation_state=observation_state,
        authority_state=CoverageState.UPSTREAM_GATED,
        evidence=(evidence,),
        reason_code="authoritative-watermark-unavailable",
    )


CONTEXT_ADMISSION_COVERAGE = tuple(
    _coverage_row(surface, mode)
    for surface in ProducerSurface
    for mode in (
        ("default", "direct") if surface is ProducerSurface.NATIVE_SHELL else ("default",)
    )
)
