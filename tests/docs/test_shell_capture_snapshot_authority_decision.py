"""Ratchet ADR-0008 against executable shell-capture authority contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY, LIFECYCLE_CONTRACTS
from autoskillit.hooks._capture._snapshot import verify_capture_snapshot
from autoskillit.hooks._capture_artifacts import verify_reference_publication_binding
from autoskillit.hooks._capture_contract import (
    CaptureFailureV2,
    CaptureV2Fields,
    capture_v2_encoded_length,
    capture_v2_worst_case_bytes,
    parse_capture_failure_v2,
    parse_capture_v2,
    render_capture_failure_v2,
    render_capture_v2,
)
from autoskillit.hooks._capture_lifecycle import (
    CaptureDeliveryStatus,
    CaptureLifecycleStore,
    CaptureReferenceStatus,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION = REPO_ROOT / "docs/decisions/0008-shell-capture-snapshot-authority.md"
INDEX = REPO_ROOT / "docs/decisions/README.md"

pytestmark = [pytest.mark.layer("docs"), pytest.mark.medium]


class _Renderable:
    def __init__(self, fields: CaptureV2Fields) -> None:
        self._fields = fields

    def capture_v2_fields(self) -> CaptureV2Fields:
        return self._fields


@pytest.fixture(scope="module")
def decision_text() -> str:
    return DECISION.read_text(encoding="utf-8")


def test_adr_is_accepted_indexed_and_traces_the_source_issue(
    decision_text: str,
) -> None:
    assert "**Status:** Accepted" in decision_text
    assert "**Source issue:** [#4322]" in decision_text
    assert "[ADR-0006](0006-output-containment.md)" in decision_text
    assert "0008-shell-capture-snapshot-authority.md" in INDEX.read_text(encoding="utf-8")


def test_documented_v2_codec_is_the_executable_canonical_codec() -> None:
    fields = CaptureV2Fields(
        capture_id="0123456789abcdef",
        finalized_at_revision=4,
        total_bytes=12_345,
        sha256="a" * 64,
        command_outcome_kind="signaled",
        command_outcome_value=15,
        shell_returncode=143,
        reference_status="published",
        reference=f"ascr2:0123456789abcdef:{'1' * 32}:{'2' * 64}",
        unavailable_reason=None,
    )
    marker = render_capture_v2(_Renderable(fields))

    assert parse_capture_v2(marker) == fields
    assert capture_v2_encoded_length(_Renderable(fields)) == len(marker)
    assert capture_v2_worst_case_bytes() >= len(marker)
    assert b"complete=true" not in marker
    assert b".log" not in marker

    failure = CaptureFailureV2(
        stage="capture_readback",
        detail="pipe read failed",
        shell_returncode=None,
        settlement_returncode=-15,
    )
    assert parse_capture_failure_v2(render_capture_failure_v2(failure)) == failure


def test_reference_and_delivery_statuses_match_the_decision(decision_text: str) -> None:
    reference_values = {status.value for status in CaptureReferenceStatus}
    delivery_values = {status.value for status in CaptureDeliveryStatus}
    assert reference_values == {
        "not_requested",
        "issued",
        "published",
        "unavailable",
        "unknown",
        "expired",
        "revoked",
    }
    assert delivery_values == {
        "not_attempted",
        "attempting",
        "delivered",
        "failed",
        "unknown",
    }
    for status in reference_values | delivery_values:
        assert f"`{status}`" in decision_text or status in decision_text


def test_single_lifecycle_resource_keeps_both_existing_owners() -> None:
    contracts = [
        contract for contract in LIFECYCLE_CONTRACTS if contract.resource == "shell-captures"
    ]
    assert len(contracts) == 1
    assert contracts[0].required_owner_roles == {"same_runner", "session_start"}
    owner_scripts = {
        script
        for hook in HOOK_REGISTRY
        if "shell-captures" in (hook.reclaims_resources | hook.self_reclaims_resources)
        for script in hook.scripts
    }
    assert owner_scripts == {"shell_capture_hook.py", "capture_lifecycle_hook.py"}


def test_authority_apis_named_by_the_decision_are_executable(
    decision_text: str,
) -> None:
    assert callable(verify_capture_snapshot)
    assert callable(verify_reference_publication_binding)
    assert callable(CaptureLifecycleStore.open_verified_capture)
    assert callable(CaptureLifecycleStore.recover_interrupted_delivery)
    for symbol in (
        "verify_capture_snapshot()",
        "commit_verified_snapshot()",
        "render_capture_v2()",
        "parse_capture_v2()",
        "open_verified_capture(token)",
        "verify_reference_publication_binding()",
        "capture_v2_encoded_length()",
        "capture_v2_worst_case_bytes()",
    ):
        assert symbol in decision_text


def test_stream_durability_and_visibility_boundaries_are_explicit(
    decision_text: str,
) -> None:
    normalized = " ".join(decision_text.split())
    for required in (
        "Only pipe EOF ends the managed stream",
        "no capture-local completeness deadline",
        "does not claim application-level causal ordering",
        "host receipt",
        "model visibility",
        "not an authenticated ledger head",
        "power-loss durability",
        "hostile same-UID",
    ):
        assert required in normalized


def test_downstream_issues_have_explicit_non_goals(decision_text: str) -> None:
    normalized = " ".join(decision_text.split())
    for issue in ("#4323", "#4324", "#4325", "#4326", "#4327", "#4329", "#4335"):
        assert issue in normalized
    for non_goal in (
        "does not install shell traps",
        "not implemented here",
        "no public retrieval tool is implemented",
        "live UI or model visibility is not implemented",
        "does not extend Codex shell authority",
    ):
        assert non_goal in normalized
