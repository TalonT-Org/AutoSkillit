from __future__ import annotations

import ast
import dataclasses
from collections.abc import Callable
from pathlib import Path


@dataclasses.dataclass(frozen=True)
class LineLimitExemption:
    """A REQ-CNST-010-EN-NN entry permitting one src module to exceed the
    1000 non-import-line full-tree default (measured by ``count_budget_lines``)
    enforced by test_no_src_module_exceeds_line_limit, up to `limit` (today's
    exemptions range as high as 1600; this table enforces no absolute ceiling
    of its own).

    `predicate`, when present, is a zero-argument callable that re-verifies the
    rationale's factual claim at check time. An exemption with `predicate=None`
    is honored by the full-tree test_no_src_module_exceeds_line_limit guard
    (legacy rationale-only contract, unchanged) but will be voided by the
    diff-scoped REQ-CNST-010 gate a follow-on part adds -- whose 750 non-import-line
    default ceiling applies only to lines touched in a diff -- touching that file in
    a future diff will force either decomposition or a real, verifiable
    predicate.
    """

    limit: int
    rationale: str
    predicate: Callable[[], bool] | None = None


def _physical_line_count(source: str) -> int:
    """Count lines on the tokenizer's numbering.

    ``Path.read_text`` already normalises CR and CRLF endings to LF; counting LF
    (plus an unterminated final line) keeps this total on the same numbering as
    ``ast`` ``lineno``/``end_lineno``. ``str.splitlines`` does not: it also splits on
    form feeds and other separators the tokenizer treats as whitespace.
    """
    if not source:
        return 0
    return source.count("\n") + (0 if source.endswith("\n") else 1)


def count_budget_lines(path: Path) -> int:
    """Return the REQ-CNST-010 line count for ``path``: physical lines minus import lines.

    Every physical line occupied by an ``import``/``from ... import`` statement is
    excluded wherever it appears (module level, under ``if TYPE_CHECKING:``, inside
    ``try``, inside a function) and however it is laid out (single line, parenthesised
    block, backslash continuation). Blank and comment-only lines within a multiline
    import span are excluded too. Every line outside those spans counts, including
    separately laid-out enclosing statements, blanks, comments, docstrings and
    ``__all__`` lists. Shared import lines are subtracted only once. A file Python
    cannot parse raises ``SyntaxError``; the gate fails closed instead of guessing.

    ``from __future__`` statements are excluded like other imports. Dynamic import
    calls remain counted as ordinary expressions; source code is never executed.

    This function is REQ-CNST-010's measurement. It is not a registered
    ``PolicySurface`` -- ``scripts/check_policy_relaxation.py`` does not model
    measurement function bodies -- so an edit here changes every file's effective budget
    without tripping that gate. Treat any change as a policy change: cite a tracking
    issue and obtain code-owner review. Issue #4965 records the import exclusion.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    import_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            assert node.end_lineno is not None
            import_lines.update(range(node.lineno, node.end_lineno + 1))
    return _physical_line_count(source) - len(import_lines)


_LINE_LIMIT_EXEMPTIONS: dict[str, LineLimitExemption] = {
    "hooks/_capture/_runner.py": LineLimitExemption(
        1050,
        "REQ-CNST-010-E31: #4511 requires a dedicated setup failure boundary before "
        "the command-body handler because the latter assumes an initialized artifact; "
        "keeping both stages in the runner preserves one owner for capture settlement",
    ),
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
    "hooks/_command_classification.py": LineLimitExemption(
        1600,
        "REQ-CNST-010-E10: shared command-classification primitive consumed by all "
        "command-inspecting guards — tokenization, shell-payload extraction, "
        "interpreter-write detection, protected-path reads, and recursive payload "
        "segmentation; the stdlib-only hook boundary and shared parser prevent "
        "policy drift across guard processes. Cap reduced to 1300 by #4665's "
        "decomposition of GitHub mutation cardinality/route authority into the "
        "_github_mutation_analysis.py sibling under E26. Bumped to 1600 for Issue "
        "#4655's rectify: ArgvToken threads quote provenance through the tokenizer "
        "(_tokenize_command_segments_with_redirects, _partition_output_redirect_"
        "indices/_select_executable_argv_tokens, _verb_start_index), and the CLI-"
        "agnostic _FlagArity/_consume_argv_flag/_consume_str_flag spec-table engine "
        "(plus _GIT_GLOBAL_FLAG_SPEC and _PIP_GLOBAL_FLAG_SPEC, and "
        "extract_git_subcommand_and_flags's fail-closed unrecognized-global-flag fix) "
        "-- these are shared, CLI-agnostic primitives every command-inspecting guard "
        "consumes (git, curl, pip, and gh's own spec table in "
        "_github_mutation_analysis.py, which imports this engine rather than "
        "duplicating it), so they stay adjacent to the tokenizer they extend rather "
        "than the gh-specific consumer module the split already separated them from.",
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
