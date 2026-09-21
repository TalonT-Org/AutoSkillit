from __future__ import annotations

import dataclasses
from collections.abc import Callable


@dataclasses.dataclass(frozen=True)
class LineLimitExemption:
    """A REQ-CNST-010-EN-NN entry permitting one src module to exceed the
    full-tree default of 1000 non-import lines (measured by ``count_budget_lines``)
    enforced by test_no_src_module_exceeds_line_limit, up to `limit` (today's
    exemptions range as high as 1600; this table enforces no absolute ceiling
    of its own).

    `predicate`, when present, is a zero-argument callable that re-verifies the
    rationale's factual claim at check time. An exemption with `predicate=None`
    is honored by the full-tree test_no_src_module_exceeds_line_limit guard
    (legacy rationale-only contract, unchanged) but will be voided by the
    diff-scoped REQ-CNST-010 gate a follow-on part adds -- whose default ceiling
    of 750 non-import lines applies only to lines touched in a diff -- touching
    that file in a future diff will force either decomposition or a real,
    verifiable predicate.
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
    "hooks/_runtime/_hook_settings.py": LineLimitExemption(
        900,
        "REQ-CNST-010-E30: PR #5114's session-scope rebase landed the worktree's "
        "table-driven enforce_script_session_scope wrapper, fail-closed "
        "_deny_scope_authority_unavailable helper, and read_session_binding "
        "compat shim in this module alongside develop's existing literal-scope "
        "enforce_session_scope and session_join_admission. Splitting them "
        "would separate two halves of the same API surface (literal-scope "
        "and script-identity overloads) that share private helpers like "
        "admit_hook_session_scope and hook_session_shape.",
        predicate=lambda: (
            __import__(
                "autoskillit.hooks._runtime._hook_settings",
                fromlist=["enforce_session_scope", "enforce_script_session_scope"],
            ).enforce_session_scope.__name__
            == "enforce_session_scope"
            and __import__(
                "autoskillit.hooks._runtime._hook_settings",
                fromlist=["enforce_session_scope", "enforce_script_session_scope"],
            ).enforce_script_session_scope.__name__
            == "enforce_script_session_scope"
        ),
    ),
}
