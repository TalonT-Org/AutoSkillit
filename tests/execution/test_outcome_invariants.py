"""Contract: outcome-field invariants catch lying-model degradation.

Verifies the outcome-contract adjudication layer that parses declared
KEY = value output fields from session result text and demotes sessions
whose self-reported outcome tokens violate a contract invariant — e.g. a
skill claiming "verdict = already_green" (or "real_fix") while also
reporting fix_failures > 0. Covers RECT-011 through RECT-018.
"""

from __future__ import annotations

import dataclasses
import json
import re

import pytest

from autoskillit.core import RetryReason, WriteBehaviorSpec
from autoskillit.core.types import KillReason
from autoskillit.core.types._type_results import ApiRetryOutcome, SkillResult, WriteEvidence
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.headless import _build_skill_result
from autoskillit.execution.headless._headless_outcome import (
    evaluate_outcome_invariants,
    evaluate_success_qualifier,
    parse_outcome_fields,
)
from autoskillit.execution.headless._headless_result import _apply_post_session_adjudication
from autoskillit.recipe import (
    OutcomeInvariantEntry,
    SkillContract,
    SkillOutput,
    SuccessQualifierEntry,
    get_skill_contract,
    load_bundled_manifest,
)
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _resolve_review_contract() -> SkillContract:
    manifest = load_bundled_manifest()
    contract = get_skill_contract("resolve-review", manifest)
    assert contract is not None, "resolve-review missing from skill_contracts.yaml"
    assert contract.outcome_invariants, "resolve-review must declare outcome_invariants"
    return contract


def _e6_result_text(
    *, verdict: str, accept_count: int, fixes_applied: int, fix_failures: int
) -> str:
    return (
        f"verdict = {verdict}\n"
        f"fixes_applied = {fixes_applied}\n"
        f"accept_count = {accept_count}\n"
        f"fix_failures = {fix_failures}\n"
        "%%ORDER_UP%%"
    )


def _result_record(result_text: str, session_id: str = "test-sess") -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": result_text,
            "session_id": session_id,
        }
    )


def _stdout_with_edit_evidence(result_text: str, session_id: str = "test-sess") -> str:
    """Build NDJSON stdout with an Edit tool_use plus a success result record.

    Used for ``verdict = real_fix`` fixtures: the write-expectation gate
    (checked before outcome invariants) requires write evidence when the
    conditional pattern matches, so these fixtures must carry an Edit call
    to reach the outcome-invariant adjudication being tested.
    """
    assistant = {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "Edit", "id": "tu_0"}],
        },
    }
    return "\n".join([json.dumps(assistant), _result_record(result_text, session_id)])


# ---------------------------------------------------------------------------
# RECT-011 / RECT-012: E6-shaped demotion via full _build_skill_result pipeline
# ---------------------------------------------------------------------------


class TestOutcomeInvariantDemotion:
    """Synthetic 'already_green with fix_failures' sessions must be demoted."""

    def test_already_green_with_fix_failures_demoted(self) -> None:
        """RECT-011: verdict=already_green, accept_count=3, fixes_applied=0,
        fix_failures=3, with file_changes_count=1 evidence must be demoted."""
        stdout = _result_record(
            _e6_result_text(
                verdict="already_green", accept_count=3, fixes_applied=0, fix_failures=3
            )
        )
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/autoskillit:resolve-review feature-branch main",
            write_behavior=WriteBehaviorSpec(
                mode="conditional",
                expected_when=(r"verdict[ \t]*=[ \t]*real_fix",),
            ),
            skill_contract=_resolve_review_contract(),
            backend=ClaudeCodeBackend(),
        )
        assert sr.success is False, (
            "already_green with fix_failures=3 (accept_count > 0) must be demoted"
        )
        assert sr.subtype == "outcome_invariant_violation"
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.OUTCOME_INVARIANT

    def test_lying_model_real_fix_with_fix_failures_demoted(self) -> None:
        """RECT-012: verdict=real_fix, fixes_applied=0, fix_failures=3 demotes identically.

        Carries Edit-tool write evidence so the case reaches outcome-invariant
        adjudication rather than being intercepted by the zero-write gate
        (verdict=real_fix alone triggers the write-expectation pattern).
        """
        stdout = _stdout_with_edit_evidence(
            _e6_result_text(verdict="real_fix", accept_count=3, fixes_applied=0, fix_failures=3)
        )
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/autoskillit:resolve-review feature-branch main",
            write_behavior=WriteBehaviorSpec(
                mode="conditional",
                expected_when=(r"verdict[ \t]*=[ \t]*real_fix",),
            ),
            skill_contract=_resolve_review_contract(),
            backend=ClaudeCodeBackend(),
        )
        assert sr.success is False, (
            "real_fix with fix_failures=3 (accept_count > 0) must be demoted identically"
        )
        assert sr.subtype == "outcome_invariant_violation"
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.OUTCOME_INVARIANT


class TestOutcomeInvariantCounterCases:
    """RECT-013: legitimate outcome shapes must NOT be demoted."""

    def test_all_reject_stays_success(self) -> None:
        """accept_count=0 → invariant's 'when' is false → skipped → success preserved."""
        stdout = _result_record(
            _e6_result_text(
                verdict="already_green", accept_count=0, fixes_applied=0, fix_failures=0
            )
        )
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/autoskillit:resolve-review feature-branch main",
            write_behavior=WriteBehaviorSpec(
                mode="conditional",
                expected_when=(r"verdict[ \t]*=[ \t]*real_fix",),
            ),
            skill_contract=_resolve_review_contract(),
            backend=ClaudeCodeBackend(),
        )
        assert sr.success is True
        assert sr.subtype != "outcome_invariant_violation"

    def test_full_success_stays_unqualified_success(self) -> None:
        """accept_count=3, fixes_applied=3, fix_failures=0 → invariant satisfied → success.

        Carries Edit-tool write evidence to satisfy the write-expectation gate
        (verdict=real_fix triggers it) so this exercises outcome-invariant
        adjudication rather than being intercepted upstream.
        """
        stdout = _stdout_with_edit_evidence(
            _e6_result_text(verdict="real_fix", accept_count=3, fixes_applied=3, fix_failures=0)
        )
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/autoskillit:resolve-review feature-branch main",
            write_behavior=WriteBehaviorSpec(
                mode="conditional",
                expected_when=(r"verdict[ \t]*=[ \t]*real_fix",),
            ),
            skill_contract=_resolve_review_contract(),
            backend=ClaudeCodeBackend(),
        )
        assert sr.success is True
        assert sr.subtype != "outcome_invariant_violation"
        assert sr.outcome_qualifier is None, "Full success must NOT carry a qualifier"

    def test_legitimate_all_skipped_already_green_qualified_not_demoted(self) -> None:
        """accept_count=1, fixes_applied=0, fix_failures=0 → success WITH qualifier
        'accepted_without_changes' (not demoted)."""
        stdout = _result_record(
            _e6_result_text(
                verdict="already_green", accept_count=1, fixes_applied=0, fix_failures=0
            )
        )
        sr = _build_skill_result(
            _make_result(returncode=0, stdout=stdout),
            skill_command="/autoskillit:resolve-review feature-branch main",
            write_behavior=WriteBehaviorSpec(
                mode="conditional",
                expected_when=(r"verdict[ \t]*=[ \t]*real_fix",),
            ),
            skill_contract=_resolve_review_contract(),
            backend=ClaudeCodeBackend(),
        )
        assert sr.success is True, (
            "Legitimate all-skipped already_green (fix_failures=0) must NOT be demoted"
        )
        assert sr.subtype != "outcome_invariant_violation"
        assert sr.outcome_qualifier == "accepted_without_changes"


class TestOutcomeInvariantRecoveryPaths:
    """RECT-014: E6-shaped output arriving via recovered-STALE / recovered-IDLE_STALL
    infra paths must demote identically to the normal-completion path."""

    def test_recovered_stale_demotes(self) -> None:
        from autoskillit.core.types._type_enums import TerminationReason

        stdout = _result_record(
            _e6_result_text(
                verdict="already_green", accept_count=3, fixes_applied=0, fix_failures=3
            )
        )
        sr = _build_skill_result(
            _make_result(
                returncode=0,
                stdout=stdout,
                termination_reason=TerminationReason.STALE,
            ),
            skill_command="/autoskillit:resolve-review feature-branch main",
            write_behavior=WriteBehaviorSpec(
                mode="conditional",
                expected_when=(r"verdict[ \t]*=[ \t]*real_fix",),
            ),
            skill_contract=_resolve_review_contract(),
            backend=ClaudeCodeBackend(),
        )
        assert sr.subtype != "recovered_from_stale", (
            "Recovery subtype must be overwritten by the invariant-violation subtype"
        )
        assert sr.success is False
        assert sr.subtype == "outcome_invariant_violation"
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.OUTCOME_INVARIANT

    def test_recovered_idle_stall_demotes(self) -> None:
        """Carries Edit-tool write evidence: verdict=real_fix triggers the
        write-expectation gate, which must be satisfied to reach outcome-invariant
        adjudication on the recovered-IDLE_STALL path."""
        from autoskillit.core.types._type_enums import TerminationReason

        stdout = _stdout_with_edit_evidence(
            _e6_result_text(verdict="real_fix", accept_count=3, fixes_applied=0, fix_failures=3)
        )
        sr = _build_skill_result(
            _make_result(
                returncode=0,
                stdout=stdout,
                termination_reason=TerminationReason.IDLE_STALL,
            ),
            skill_command="/autoskillit:resolve-review feature-branch main",
            write_behavior=WriteBehaviorSpec(
                mode="conditional",
                expected_when=(r"verdict[ \t]*=[ \t]*real_fix",),
            ),
            skill_contract=_resolve_review_contract(),
            backend=ClaudeCodeBackend(),
        )
        assert sr.subtype != "recovered_from_idle_stall", (
            "Recovery subtype must be overwritten by the invariant-violation subtype"
        )
        assert sr.success is False
        assert sr.subtype == "outcome_invariant_violation"
        assert sr.needs_retry is True
        assert sr.retry_reason == RetryReason.OUTCOME_INVARIANT


# ---------------------------------------------------------------------------
# RECT-015: parse_outcome_fields unit tests
# ---------------------------------------------------------------------------


class TestParseOutcomeFields:
    """Contract-field parser: declared fields only, typed per contract."""

    def _contract(self) -> SkillContract:
        return SkillContract(
            inputs=[],
            outputs=[
                SkillOutput(name="verdict", type="string"),
                SkillOutput(name="accept_count", type="integer"),
                SkillOutput(name="fixes_applied", type="integer"),
                SkillOutput(name="fix_failures", type="integer"),
            ],
        )

    def test_declared_integer_field_parses_to_int(self) -> None:
        fields = parse_outcome_fields("accept_count = 5\n", self._contract())
        assert fields["accept_count"] == 5
        assert isinstance(fields["accept_count"], int)

    def test_malformed_integer_field_stays_raw_string(self) -> None:
        fields = parse_outcome_fields("accept_count = not_a_number\n", self._contract())
        assert fields["accept_count"] == "not_a_number"

    def test_undeclared_tokens_ignored(self) -> None:
        fields = parse_outcome_fields(
            "accept_count = 2\nsome_undeclared_field = 99\n", self._contract()
        )
        assert "some_undeclared_field" not in fields
        assert fields["accept_count"] == 2

    def test_realistic_step7_output_parses_all_fields(self) -> None:
        result_text = _e6_result_text(
            verdict="real_fix", accept_count=4, fixes_applied=4, fix_failures=0
        )
        fields = parse_outcome_fields(result_text, self._contract())
        assert fields["verdict"] == "real_fix"
        assert fields["accept_count"] == 4
        assert fields["fixes_applied"] == 4
        assert fields["fix_failures"] == 0


# ---------------------------------------------------------------------------
# RECT-016: token-emission sync — SKILL.md must declare every invariant field
# ---------------------------------------------------------------------------


class TestTokenEmissionSync:
    """Every field referenced by an outcome_invariants entry must be both a
    declared output in skill_contracts.yaml AND appear as an emitted
    ``field = `` line inside the resolve-review SKILL.md Output section."""

    @staticmethod
    def _skill_md_output_section_text() -> str:
        from autoskillit.core import pkg_root

        skill_md = pkg_root() / "skills_extended" / "resolve-review" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        marker = "\n## Output\n"
        idx = content.find(marker)
        assert idx != -1, "resolve-review SKILL.md missing '## Output' section"
        return content[idx:]

    def test_invariant_fields_declared_and_emitted(self) -> None:
        contract = _resolve_review_contract()
        declared_names = {o.name for o in contract.outputs}
        output_section = self._skill_md_output_section_text()

        referenced_fields: set[str] = set()
        for inv in contract.outcome_invariants:
            for expr in (inv.when, inv.require):
                field_name = expr.split()[0] if expr.split() else ""
                referenced_fields.add(field_name)

        assert referenced_fields, "outcome_invariants must reference at least one field"

        for field_name in referenced_fields:
            assert field_name in declared_names, (
                f"outcome_invariants references {field_name!r} which is not a declared "
                "output in skill_contracts.yaml"
            )
            line_pattern = re.compile(rf"^{re.escape(field_name)}\s*=", re.MULTILINE)
            assert line_pattern.search(output_section), (
                f"outcome_invariants field {field_name!r} has no '{field_name} = ' line "
                "in resolve-review SKILL.md Output section"
            )


# ---------------------------------------------------------------------------
# RECT-017: contract loader validation
# ---------------------------------------------------------------------------


class TestContractLoaderValidation:
    """get_skill_contract must reject malformed outcome_invariants entries."""

    def test_undeclared_field_reference_fails_load(self) -> None:
        manifest = {
            "skills": {
                "fake-skill": {
                    "outputs": [{"name": "accept_count", "type": "integer"}],
                    "outcome_invariants": [
                        {"when": "accept_count > 0", "require": "undeclared_field == 0"}
                    ],
                }
            }
        }
        with pytest.raises(ValueError, match="undeclared output"):
            get_skill_contract("fake-skill", manifest)

    def test_non_numeric_declared_field_fails_load(self) -> None:
        manifest = {
            "skills": {
                "fake-skill": {
                    "outputs": [
                        {"name": "accept_count", "type": "integer"},
                        {"name": "verdict", "type": "string"},
                    ],
                    "outcome_invariants": [
                        {"when": "accept_count > 0", "require": "verdict == 0"}
                    ],
                }
            }
        }
        with pytest.raises(ValueError, match="non-integer output"):
            get_skill_contract("fake-skill", manifest)

    def test_missing_when_or_require_fails_load(self) -> None:
        manifest = {
            "skills": {
                "fake-skill": {
                    "outputs": [{"name": "accept_count", "type": "integer"}],
                    "outcome_invariants": [{"when": "accept_count > 0"}],
                }
            }
        }
        with pytest.raises(ValueError, match="missing 'when' or 'require'"):
            get_skill_contract("fake-skill", manifest)

    def test_well_formed_invariant_loads_successfully(self) -> None:
        manifest = {
            "skills": {
                "fake-skill": {
                    "outputs": [
                        {"name": "accept_count", "type": "integer"},
                        {"name": "fix_failures", "type": "integer"},
                    ],
                    "outcome_invariants": [
                        {"when": "accept_count > 0", "require": "fix_failures == 0"}
                    ],
                }
            }
        }
        contract = get_skill_contract("fake-skill", manifest)
        assert contract is not None
        assert len(contract.outcome_invariants) == 1
        assert contract.outcome_invariants[0].when == "accept_count > 0"
        assert contract.outcome_invariants[0].require == "fix_failures == 0"


# ---------------------------------------------------------------------------
# RECT-018: evidence semantics regression
# ---------------------------------------------------------------------------


class TestEvidenceSemanticsRegression:
    """has_implementation_evidence must remain unchanged by this feature —
    git_writes_detected alone does NOT satisfy it."""

    def test_git_writes_alone_does_not_satisfy_implementation_evidence(self) -> None:
        evidence = WriteEvidence(
            write_call_count=0,
            fs_writes_detected=False,
            git_writes_detected=True,
            file_changes_count=0,
        )
        assert evidence.has_implementation_evidence is False
        assert evidence.has_evidence is True, (
            "git_writes_detected alone still satisfies the broader has_evidence signal"
        )

    def test_fs_writes_alone_does_not_satisfy_implementation_evidence(self) -> None:
        evidence = WriteEvidence(
            write_call_count=0,
            fs_writes_detected=True,
            git_writes_detected=False,
            file_changes_count=0,
        )
        assert evidence.has_implementation_evidence is False

    def test_write_call_count_satisfies_implementation_evidence(self) -> None:
        evidence = WriteEvidence(
            write_call_count=1,
            fs_writes_detected=False,
            git_writes_detected=False,
            file_changes_count=0,
        )
        assert evidence.has_implementation_evidence is True

    def test_file_changes_count_satisfies_implementation_evidence(self) -> None:
        evidence = WriteEvidence(
            write_call_count=0,
            fs_writes_detected=False,
            git_writes_detected=False,
            file_changes_count=1,
        )
        assert evidence.has_implementation_evidence is True


# ---------------------------------------------------------------------------
# Direct unit tests: evaluate_outcome_invariants / evaluate_success_qualifier
# ---------------------------------------------------------------------------


class TestEvaluateOutcomeInvariantsUnit:
    """Direct unit coverage of evaluate_outcome_invariants against parsed fields."""

    def _invariants(self) -> list[OutcomeInvariantEntry]:
        return [OutcomeInvariantEntry(when="accept_count > 0", require="fix_failures == 0")]

    def test_violation_detected(self) -> None:
        fields = {"accept_count": 3, "fix_failures": 3}
        violated, detail = evaluate_outcome_invariants(fields, self._invariants())
        assert violated is True
        assert "accept_count > 0" in detail

    def test_no_violation_when_require_satisfied(self) -> None:
        fields = {"accept_count": 3, "fix_failures": 0}
        violated, _ = evaluate_outcome_invariants(fields, self._invariants())
        assert violated is False

    def test_skipped_when_when_field_missing(self) -> None:
        """A missing 'when' field means the token was never emitted — legitimate
        no-PR-found exit — the invariant must be skipped, not violated."""
        fields: dict[str, int | str] = {}
        violated, _ = evaluate_outcome_invariants(fields, self._invariants())
        assert violated is False

    def test_fail_closed_when_require_field_missing(self) -> None:
        """'when' true but 'require' field absent → fail-closed violation."""
        fields: dict[str, int | str] = {"accept_count": 1}
        violated, _ = evaluate_outcome_invariants(fields, self._invariants())
        assert violated is True

    def test_no_invariants_never_violates(self) -> None:
        violated, detail = evaluate_outcome_invariants({"accept_count": 5}, [])
        assert violated is False
        assert detail == ""


class TestEvaluateSuccessQualifierUnit:
    """Direct unit coverage of evaluate_success_qualifier."""

    def _qualifiers(self) -> list[SuccessQualifierEntry]:
        return [
            SuccessQualifierEntry(
                when="accept_count > 0 and fixes_applied == 0 and fix_failures == 0",
                qualifier="accepted_without_changes",
            )
        ]

    def test_qualifier_matches(self) -> None:
        fields = {"accept_count": 1, "fixes_applied": 0, "fix_failures": 0}
        result = evaluate_success_qualifier(fields, self._qualifiers())
        assert result == "accepted_without_changes"

    def test_no_qualifier_when_no_match(self) -> None:
        fields = {"accept_count": 3, "fixes_applied": 3, "fix_failures": 0}
        result = evaluate_success_qualifier(fields, self._qualifiers())
        assert result is None

    def test_no_qualifiers_returns_none(self) -> None:
        result = evaluate_success_qualifier({"accept_count": 1}, [])
        assert result is None


# ---------------------------------------------------------------------------
# Direct unit tests: _apply_post_session_adjudication
# ---------------------------------------------------------------------------


def _base_skill_result(*, success: bool = True, result_text: str = "") -> SkillResult:
    return SkillResult(
        success=success,
        result=result_text,
        session_id="test-sess",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
        kill_reason=KillReason.NATURAL_EXIT,
        api_retry=ApiRetryOutcome(),
    )


class TestApplyPostSessionAdjudicationUnit:
    """Direct unit coverage of _apply_post_session_adjudication."""

    def test_non_success_passthrough(self) -> None:
        sr = _base_skill_result(success=False)
        result = _apply_post_session_adjudication(
            sr, WriteEvidence.none_observed(), None, _resolve_review_contract()
        )
        assert result is sr

    def test_no_contract_passthrough(self) -> None:
        sr = _base_skill_result(
            result_text=_e6_result_text(
                verdict="already_green", accept_count=3, fixes_applied=0, fix_failures=3
            )
        )
        result = _apply_post_session_adjudication(sr, WriteEvidence.none_observed(), None, None)
        assert result.success is True
        assert result.subtype != "outcome_invariant_violation"

    def test_violated_invariant_demotes_directly(self) -> None:
        sr = _base_skill_result(
            result_text=_e6_result_text(
                verdict="already_green", accept_count=3, fixes_applied=0, fix_failures=3
            )
        )
        evidence = WriteEvidence(
            write_call_count=0,
            fs_writes_detected=False,
            git_writes_detected=False,
            file_changes_count=1,
        )
        result = dataclasses.replace(sr)  # sanity: replace works on SkillResult
        result = _apply_post_session_adjudication(
            result, evidence, None, _resolve_review_contract()
        )
        assert result.success is False
        assert result.subtype == "outcome_invariant_violation"
        assert result.needs_retry is True
        assert result.retry_reason == RetryReason.OUTCOME_INVARIANT

    def test_satisfied_invariant_preserves_success(self) -> None:
        sr = _base_skill_result(
            result_text=_e6_result_text(
                verdict="real_fix", accept_count=3, fixes_applied=3, fix_failures=0
            )
        )
        result = _apply_post_session_adjudication(
            sr, WriteEvidence.none_observed(), None, _resolve_review_contract()
        )
        assert result.success is True
        assert result.subtype != "outcome_invariant_violation"
