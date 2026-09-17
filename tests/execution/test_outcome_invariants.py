"""Contract: outcome-field invariants catch lying-model degradation.

Verifies the outcome-contract adjudication layer that parses declared
KEY = value output fields from session result text and demotes sessions
whose self-reported outcome tokens violate a contract invariant — e.g. a
skill claiming "verdict = already_green" (or "real_fix") while also
reporting fix_failures > 0. Covers RECT-011 through RECT-018.
"""

from __future__ import annotations

import dataclasses
import errno
import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.core import RetryReason, WriteBehaviorSpec
from autoskillit.core.types import KillReason
from autoskillit.core.types._type_results import ApiRetryOutcome, SkillResult, WriteEvidence
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.headless import _build_skill_result
from autoskillit.execution.headless._headless_adjudication import (
    _apply_post_session_adjudication,
    _validate_declared_artifact,
)
from autoskillit.execution.headless._headless_outcome import (
    evaluate_outcome_invariants,
    evaluate_success_qualifier,
    parse_outcome_fields,
)
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
    """Return a scalar-only contract for the reusable invariant engine tests."""
    return SkillContract(
        inputs=(),
        outputs=[
            SkillOutput(name="verdict", type="string"),
            SkillOutput(name="fixes_applied", type="integer"),
            SkillOutput(name="accept_count", type="integer"),
            SkillOutput(name="fix_failures", type="integer"),
        ],
        outcome_invariants=[
            OutcomeInvariantEntry(
                when="accept_count > 0",
                require="fix_failures == 0",
            )
        ],
        success_qualifiers=[
            SuccessQualifierEntry(
                when="accept_count > 0 and fixes_applied == 0 and fix_failures == 0",
                qualifier="accepted_without_changes",
            )
        ],
    )


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

    def test_scalar_real_fix_with_fix_failures_demoted(self) -> None:
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

    def test_scalar_all_skipped_already_green_qualified_not_demoted(self) -> None:
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
            inputs=(),
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

    def test_adversarial_prose_before_block_does_not_overwrite(self) -> None:
        """Trailing-block parser ignores field-like lines in earlier prose."""
        result_text = (
            "I set verdict = already_green because there was nothing to fix.\n"
            "The accept_count = 2 findings were all rejected.\n"
            "\n"
            "resolve-review complete\n"
            "Fixes applied: 3\n"
            "\n"
            "verdict = real_fix\n"
            "fixes_applied = 3\n"
            "accept_count = 4\n"
            "fix_failures = 0\n"
            "%%ORDER_UP%%"
        )
        fields = parse_outcome_fields(result_text, self._contract())
        assert fields["verdict"] == "real_fix"
        assert fields["fixes_applied"] == 3
        assert fields["accept_count"] == 4
        assert fields["fix_failures"] == 0

    def test_trailing_gate_tag_skipped(self) -> None:
        """Gate tags after the field block don't break contiguous detection."""
        result_text = (
            "verdict = real_fix\n"
            "fixes_applied = 1\n"
            "accept_count = 1\n"
            "fix_failures = 0\n"
            "%%ORDER_UP::abc123%%"
        )
        fields = parse_outcome_fields(result_text, self._contract())
        assert fields["verdict"] == "real_fix"
        assert fields["fixes_applied"] == 1

    def test_fields_only_no_trailing_tag(self) -> None:
        """Parser works when no gate tag follows the field block."""
        result_text = "verdict = already_green\nfixes_applied = 0\n"
        fields = parse_outcome_fields(result_text, self._contract())
        assert fields["verdict"] == "already_green"
        assert fields["fixes_applied"] == 0


# ---------------------------------------------------------------------------
# RECT-016 / Part B: token-emission sync for resolver-owned output fields
# ---------------------------------------------------------------------------


class TestResolverEmitsOnlyModelOwnedFields:
    """Resolvers emit statuses and disposition rows, never derived counters."""

    _RESOLVER_SKILLS = (
        "resolve-review",
        "resolve-research-review",
        "resolve-claims-review",
    )
    _DERIVED_COUNTERS = {
        "accept_count",
        "fixes_applied",
        "fix_failures",
        "skipped_in_fix_phase",
    }

    @staticmethod
    def _template_text(skill_name: str, template_name: str) -> str:
        from autoskillit.core import pkg_root

        skill_md = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        match = re.search(
            rf"<!-- {template_name}:begin -->\s*(?P<body>.*?)\s*"
            rf"<!-- {template_name}:end -->",
            content,
            re.DOTALL,
        )
        assert match, f"{skill_name} missing {template_name} template delimiter"
        return match["body"]

    @pytest.mark.parametrize("skill_name", _RESOLVER_SKILLS)
    def test_resolver_fields_are_declared_but_only_model_owned_fields_emit(
        self,
        skill_name: str,
    ) -> None:
        manifest = load_bundled_manifest()
        contract = get_skill_contract(skill_name, manifest)
        assert contract is not None, f"{skill_name} missing from skill_contracts.yaml"
        declared_names = {output.name for output in contract.outputs}
        assert {"review_status", "finding_disposition"} <= declared_names
        assert self._DERIVED_COUNTERS <= declared_names

        for template_name in (
            "resolver-operative-output",
            "resolver-final-output",
        ):
            template = self._template_text(skill_name, template_name)
            assert re.search(r"^review_status\s*=", template, re.MULTILINE)
            assert re.search(r"^finding_disposition\s*=", template, re.MULTILINE)
            for counter in self._DERIVED_COUNTERS:
                assert not re.search(rf"^{re.escape(counter)}\s*=", template, re.MULTILINE), (
                    f"{skill_name} must not emit server-derived {counter}"
                )


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
            sr, WriteEvidence.none_observed(), None, _resolve_review_contract(), ""
        )
        assert result is sr

    def test_no_contract_passthrough(self) -> None:
        sr = _base_skill_result(
            result_text=_e6_result_text(
                verdict="already_green", accept_count=3, fixes_applied=0, fix_failures=3
            )
        )
        result = _apply_post_session_adjudication(
            sr, WriteEvidence.none_observed(), None, None, ""
        )
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
            result, evidence, None, _resolve_review_contract(), ""
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
            sr, WriteEvidence.none_observed(), None, _resolve_review_contract(), ""
        )
        assert result.success is True
        assert result.subtype != "outcome_invariant_violation"


def _artifact_contract() -> SkillContract:
    return SkillContract(
        inputs=(),
        outputs=[
            SkillOutput(name="artifact", type="file_path"),
            SkillOutput(name="note", type="string"),
        ],
    )


class TestDeclaredArtifactAdjudication:
    @pytest.mark.parametrize("value", ["bad\0name", None])
    def test_invalid_path_value_is_producer_failure(self, tmp_path, value: object) -> None:
        result = _validate_declared_artifact(
            str(tmp_path),
            "artifact",
            value,  # type: ignore[arg-type]
        )

        assert result is not None
        assert result[0] == "artifact_contract_violation"

    def test_existing_declared_file_is_preserved(self, tmp_path) -> None:
        (tmp_path / "report.md").write_text("report")
        sr = _base_skill_result(result_text="artifact = report.md")

        result = _apply_post_session_adjudication(
            sr, WriteEvidence.none_observed(), None, _artifact_contract(), str(tmp_path)
        )

        assert result.success is True
        assert result.outcome_fields == {"artifact": "report.md"}

    @pytest.mark.parametrize("path_kind", ["absolute", "symlink"])
    def test_inside_cwd_declared_file_is_preserved(self, tmp_path, path_kind: str) -> None:
        target = tmp_path / "report.md"
        target.write_text("report")
        if path_kind == "absolute":
            declared_path = str(target)
        else:
            link = tmp_path / "report-link.md"
            link.symlink_to(target)
            declared_path = link.name
        sr = _base_skill_result(result_text=f"artifact = {declared_path}")

        result = _apply_post_session_adjudication(
            sr, WriteEvidence.none_observed(), None, _artifact_contract(), str(tmp_path)
        )

        assert result.success is True
        assert result.outcome_fields == {"artifact": declared_path}

    @pytest.mark.parametrize("artifact_name", ["missing.md", "directory"])
    def test_missing_or_non_file_artifact_is_producer_failure(
        self, tmp_path, artifact_name: str
    ) -> None:
        if artifact_name == "directory":
            (tmp_path / artifact_name).mkdir()
        sr = _base_skill_result(result_text=f"artifact = {artifact_name}")

        result = _apply_post_session_adjudication(
            sr, WriteEvidence.none_observed(), None, _artifact_contract(), str(tmp_path)
        )

        assert result.success is False
        assert result.subtype == "artifact_contract_violation"
        assert result.outcome_fields is None
        assert artifact_name in result.result

    def test_symlink_escape_is_producer_failure(self, tmp_path) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
        outside.write_text("outside")
        (tmp_path / "report.md").symlink_to(outside)
        sr = _base_skill_result(result_text="artifact = report.md")

        result = _apply_post_session_adjudication(
            sr, WriteEvidence.none_observed(), None, _artifact_contract(), str(tmp_path)
        )

        assert result.success is False
        assert result.subtype == "artifact_contract_violation"
        assert result.outcome_fields is None
        assert "report.md" in result.result

    def test_absolute_path_escape_is_producer_failure(self, tmp_path) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
        outside.write_text("outside")
        sr = _base_skill_result(result_text=f"artifact = {outside}")

        result = _apply_post_session_adjudication(
            sr, WriteEvidence.none_observed(), None, _artifact_contract(), str(tmp_path)
        )

        assert result.success is False
        assert result.subtype == "artifact_contract_violation"
        assert result.outcome_fields is None
        assert outside.name in result.result

    def test_absent_optional_path_and_non_path_output_are_not_validated(self, tmp_path) -> None:
        sr = _base_skill_result(result_text="note = missing.md")

        result = _apply_post_session_adjudication(
            sr, WriteEvidence.none_observed(), None, _artifact_contract(), str(tmp_path)
        )

        assert result.success is True
        assert result.outcome_fields == {"note": "missing.md"}

    @pytest.mark.parametrize("error_number", [errno.EACCES, errno.EIO, errno.EAGAIN])
    def test_filesystem_access_failure_is_infrastructure_error(
        self, monkeypatch, tmp_path, error_number: int
    ) -> None:
        from autoskillit.execution.headless import _headless_adjudication

        sr = _base_skill_result(result_text="artifact = report.md")
        warning = Mock()
        original_stat = Path.stat

        def _raise(path: Path, *args, **kwargs):
            if path.name == "report.md":
                raise OSError(error_number, "unavailable")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", _raise)
        monkeypatch.setattr(_headless_adjudication.logger, "warning", warning)
        result = _apply_post_session_adjudication(
            sr, WriteEvidence.none_observed(), None, _artifact_contract(), str(tmp_path)
        )

        assert result.success is False
        assert result.subtype == "artifact_adjudication_error"
        assert result.outcome_fields is None
        warning.assert_called_once_with(
            "artifact_adjudication_error",
            field_name="artifact",
            artifact_name="report.md",
            exc_info=True,
        )

    @pytest.mark.parametrize("error_number", [errno.ENOTDIR, errno.ELOOP])
    def test_invalid_artifact_path_is_producer_failure(
        self, monkeypatch, tmp_path, error_number: int
    ) -> None:
        sr = _base_skill_result(result_text="artifact = report.md")
        original_stat = Path.stat

        def _raise(path: Path, *args, **kwargs):
            if path.name == "report.md":
                raise OSError(error_number, "invalid artifact path")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", _raise)
        result = _apply_post_session_adjudication(
            sr, WriteEvidence.none_observed(), None, _artifact_contract(), str(tmp_path)
        )

        assert result.success is False
        assert result.subtype == "artifact_contract_violation"
        assert result.outcome_fields is None
        assert "report.md" in result.result
