# autoskillit: policy-authority -- repository acceptance policy. Humans edit this file;
# automated repair sessions must not. Every approval needs a tracking issue and code-owner review.
"""Declared acceptance-policy surfaces and the human approval ledger.

A policy surface is a value the repository treats as the accepted limit for
something it measures. Because a surface and the measurement it gates live in
the same working tree, raising one is indistinguishable from satisfying it
unless a trusted revision is consulted; scripts/check_policy_relaxation.py
does that consulting and reads this file by AST from both revisions.
"""

from __future__ import annotations

import dataclasses
from typing import Literal

POLICY_AUTHORITY_SENTINEL = "autoskillit: policy-authority"

SurfaceKind = Literal["int_scalar", "int_map", "exemption_map"]


@dataclasses.dataclass(frozen=True)
class PolicySurface:
    """One module-level assignment holding an accepted limit."""

    path: str
    symbol: str
    kind: SurfaceKind
    default: int | None = None
    """int_map only: the limit an absent key already implies."""


@dataclasses.dataclass(frozen=True)
class PolicyRelaxationApproval:
    """A human's record that one specific loosening is intended.

    ``before`` and ``after`` must quote the checker's rendering exactly; an
    approval matches one relaxation and nothing else.
    """

    path: str
    symbol: str
    key: str | None
    before: str
    after: str
    issue: int
    approved_by: str


POLICY_SURFACES: tuple[PolicySurface, ...] = (
    PolicySurface(
        "tests/arch/_subpackage_isolation_line_limits.py",
        "_LINE_LIMIT_EXEMPTIONS",
        "exemption_map",
    ),
    PolicySurface(
        "tests/arch/test_subpackage_isolation_file_counts.py",
        "FILE_COUNT_LIMITS",
        "int_map",
        default=10,
    ),
    PolicySurface(
        "tests/arch/test_pyright_suppression_allowlist.py", "TYPE_IGNORE_BUDGET", "int_scalar"
    ),
    PolicySurface("tests/infra/test_ci_dev_config.py", "_E501_EXEMPTION_CAP", "int_scalar"),
    PolicySurface("tests/arch/test_xfail_bridge_policy.py", "_EXEMPT_CAP", "int_scalar"),
    PolicySurface("tests/arch/test_execution_source_split.py", "HEADLESS_SIZE_BUDGETS", "int_map"),
    PolicySurface("tests/arch/test_execution_source_split.py", "SESSION_SIZE_BUDGETS", "int_map"),
    PolicySurface(
        "tests/arch/test_execution_source_split.py", "SESSION_FSM_SIZE_BUDGETS", "int_map"
    ),
    PolicySurface("tests/arch/test_execution_source_split.py", "MQ_SIZE_BUDGETS", "int_map"),
    PolicySurface("tests/arch/test_file_size_budgets.py", "FORMATTER_SIZE_BUDGETS", "int_map"),
    PolicySurface("scripts/check_file_lengths.py", "HARD_CAP", "int_scalar"),
    PolicySurface("scripts/check_file_lengths.py", "ABSOLUTE_CAP", "int_scalar"),
    PolicySurface("tests/arch/test_api_orchestration_size.py", "_LINE_CEILING", "int_scalar"),
    PolicySurface(
        "tests/arch/test_session_skills_projected_artifact_size_ceilings.py",
        "_LINE_CEILING",
        "int_scalar",
    ),
    PolicySurface("tests/arch/test_config_module_size_guard.py", "LINE_BUDGET", "int_scalar"),
    PolicySurface("tests/arch/test_context_admission_ledger_split.py", "_MAX_LINES", "int_scalar"),
    PolicySurface(
        "tests/arch/test_audit_admission_ledger_shards.py", "CEILING_LINES", "int_scalar"
    ),
    PolicySurface(
        "tests/arch/test_audit_admission_ledger_shards.py",
        "FACADE_CEILING_LINES",
        "int_scalar",
    ),
    PolicySurface(
        "tests/arch/test_audit_admission_ledger_shards.py",
        "EXPECTED_PIPELINE_PY_COUNT",
        "int_scalar",
    ),
    PolicySurface("tests/arch/test_subpackage_isolation_layout.py", "SHARD_CEILING", "int_scalar"),
    PolicySurface("tests/arch/test_rule_severity_consistency.py", "_ALLOWLIST_CAP", "int_scalar"),
    PolicySurface(
        "tests/arch/test_recipe_diagram_freshness.py", "CURRENT_XFAIL_CAP", "int_scalar"
    ),
)

POLICY_RELAXATION_APPROVALS: tuple[PolicyRelaxationApproval, ...] = (
    PolicyRelaxationApproval(
        path="tests/arch/test_subpackage_isolation_file_counts.py",
        symbol="FILE_COUNT_LIMITS",
        key="core/io",
        before="8",
        after="9",
        issue=4671,
        approved_by="Trecek",
    ),
    PolicyRelaxationApproval(
        path="tests/arch/test_subpackage_isolation_file_counts.py",
        symbol="FILE_COUNT_LIMITS",
        key="core/plugins",
        before="5",
        after="10",
        issue=4671,
        approved_by="Trecek",
    ),
    # #4623 threads ManagedAttemptRecorder wiring (Step 5) and child_outcomes
    # telemetry (Step 6) through _execute_claude_headless. Already trimmed from
    # 749 to 738 lines via safe extraction (the recorder construction and its
    # on_spawn/on_session_id_resolved wrappers moved to
    # headless/_managed/_attempt.py, a sibling module with headroom); the
    # remainder is per-exception-handler recording call sites and cancellation-
    # handling closures that read/write this function's own nonlocal state and
    # cannot be extracted without a materially riskier restructuring of that
    # control flow. Approved as-is rather than attempted under time pressure;
    # a further-decomposition follow-up remains open against #4623.
    PolicyRelaxationApproval(
        path="tests/arch/test_execution_source_split.py",
        symbol="HEADLESS_SIZE_BUDGETS",
        key="headless/_headless_execute.py",
        before="711",
        after="738",
        issue=4623,
        approved_by="Trecek",
    ),
)

POLICY_AUTHORITY_PATHS = (
    "tests/arch/_acceptance_policy_surfaces.py",
    "scripts/check_policy_relaxation.py",
    "tests/arch/test_acceptance_policy_relaxation_gate.py",
    "tests/arch/_policy_gate_plumbing.py",
    ".github/CODEOWNERS",
)

CODEOWNER_REVIEWED_PATHS = POLICY_AUTHORITY_PATHS + (".github/workflows/tests.yml",)
