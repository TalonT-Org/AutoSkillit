from __future__ import annotations

import dataclasses
from collections.abc import Callable


@dataclasses.dataclass(frozen=True)
class LineLimitExemption:
    """A REQ-CNST-010-EN-NN entry permitting one src module to exceed the
    1000-line full-tree default enforced by test_no_src_module_exceeds_line_limit,
    up to `limit` (today's exemptions range as high as 1600; this table enforces
    no absolute ceiling of its own).

    `predicate`, when present, is a zero-argument callable that re-verifies the
    rationale's factual claim at check time. An exemption with `predicate=None`
    is honored by the full-tree test_no_src_module_exceeds_line_limit guard
    (legacy rationale-only contract, unchanged) but will be voided by the
    diff-scoped REQ-CNST-010 gate a follow-on part adds -- whose 750-line default
    ceiling applies only to lines touched in a diff -- touching that file in
    a future diff will force either decomposition or a real, verifiable
    predicate.
    """

    limit: int
    rationale: str
    predicate: Callable[[], bool] | None = None


_LINE_LIMIT_EXEMPTIONS: dict[str, LineLimitExemption] = {
    "core/types/_type_constants.py": LineLimitExemption(
        1050,
        "REQ-CNST-010-E29: #4597 Phase 3 added a RETIRED_INSTALL_ARTIFACT_SHAPES entry "
        "for the pre-immutable-roots shared uv tool root and two DURABLE_ARTIFACT_WRITERS "
        "entries (the entrypoint shim, the install-root generation writer). Both registries "
        "are append-only forcing functions per the file's own model comment; splitting them "
        "out of this module would separate them from the frozensets and validation "
        "functions their own docstrings and tests reference by exact module path.",
    ),
    "execution/evidence_reader.py": LineLimitExemption(
        1500,
        "REQ-CNST-010-E25: #4585 keeps sterile auth, projection, probes, managed process "
        "lifecycle, and strict result validation behind one evidence-reader launch interface",
    ),
    "execution/process/__init__.py": LineLimitExemption(
        1050,
        "REQ-CNST-010-E27: #4678 rectify threads ceiling_seconds through run_managed_async/"
        "run_managed_sync/DefaultSubprocessRunner and adds the PTY-wrapper workload-identity "
        "resolution for the process-tether spawner-death immunity mechanism — this facade is "
        "the single composition point for both spawn paths and must stay adjacent to the "
        "spawn call sites it wires the tether into.",
    ),
    "hooks/_capture_artifacts.py": LineLimitExemption(
        1200,
        "REQ-CNST-010-E22: descriptor-anchored capture authority and isolated runner — "
        "re-exports capture_store_stats, reconcile_capture_store, CaptureStoreStats, "
        "CleanupBlocker, CleanupProgress, and SweepBudgetSpec from its own dual-mode "
        "(flat sys.path / dotted package) _capture import bootstrap so hooks/__init__.py "
        "can gateway them to cli/ops/_capture_store.py without importing _capture submodules "
        "directly, which would race the standalone hook scripts' own flat-style bootstrap "
        "of sys.modules['_capture']. Bumped for ADR-0009's failure-disposition routing "
        "(bookkeeping vs. integrity) and the capacity injection seam (issue #4479).",
    ),
    "hooks/_capture_lifecycle/_store.py": LineLimitExemption(
        1250,
        "REQ-CNST-010-E28: post-split capture-lifecycle store (#4727) — the "
        "4 admission helpers (_acquire_flock, _admission_reason, _admit_new_record, "
        "_scan_and_adopt_orphans) are now thin wrappers around module-level "
        "implementations in the sibling _admission.py, but the rest of the class "
        "body (state-machine transitions, ledger-compaction, capacity-rescue, "
        "delivery wiring, sweep orchestration) shares the same self-accounting "
        "invariants the original E21 entry called out. The class body alone is "
        "~960 lines after the wrappers extract; the limit stays at 1250 to match "
        "the pre-split E21 ceiling. E21 was retired by issue #4853 (decomposing "
        "hook_registry.py); this entry remains the load-bearing exemption for "
        "_capture_lifecycle/_store.py until the class body is further decomposed "
        "(issue #4727).",
    ),
    "hooks/_capture_contract.py": LineLimitExemption(
        1100,
        "REQ-CNST-010-E23: CaptureFailureV3 envelope framing — carries the full "
        "CaptureFailureReason wire vocabulary and its (V2 marker) rendering; ADR-0009 "
        "added the SNAPSHOT_INTEGRITY reason and degraded-delivery envelope fields, "
        "which must stay co-located with the rest of the envelope schema they extend "
        "(issue #4479).",
    ),
    "hooks/guards/git_ops_guard.py": LineLimitExemption(
        1050,
        "REQ-CNST-010-E28: Issue #4655's rectify moves this guard's checked-out-ref "
        "dynamic-value check onto the shared _DYNAMIC_SHELL_TOKEN_RE regex, which "
        "#4665's decomposition relocated to hooks/_github_mutation_analysis.py -- a "
        "second module-scope import block (alongside the existing "
        "_command_classification one) is needed since the two symbols now live in "
        "different sibling modules. Cap bumped from the 1000-line default to give "
        "this guard's own destructive-op/fetch/checked-out-ref classification room "
        "without re-tripping the limit on the next small addition.",
    ),
    "execution/headless/_headless_result.py": LineLimitExemption(
        900,
        "REQ-CNST-010-E25-narrowed: #4233 keeps the async-obligation success gate adjacent to "
        "the existing stale, idle, timeout, and content adjudication order it must preempt. "
        "After #4664 decomposition, adjudication helpers live in _headless_adjudication.py "
        "— including the #4641/#4644 _should_flag_cleanup_incomplete diagnostic shared by "
        "both SkillResult construction seams; _build_skill_result remains here as the "
        "headless orchestration authority. The 827-line residual is dominated by that "
        "single 741-line function, which owns the success-gate adjacency rule.",
    ),
}
