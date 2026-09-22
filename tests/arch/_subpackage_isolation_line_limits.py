from __future__ import annotations

import dataclasses
from collections.abc import Callable


@dataclasses.dataclass(frozen=True)
class LineLimitExemption:
    """A REQ-CNST-010-EN-NN entry permitting one src module to exceed the
    full-tree default of 1000 non-import lines (measured by ``count_budget_lines``)
    enforced by test_no_src_module_exceeds_line_limit, up to `limit`.

    `predicate`, when present, is a zero-argument callable that re-verifies the
    rationale's factual claim at check time. The full-tree guard honors
    `predicate=None` within its ceiling; the diff-scoped checker requires a
    predicate. An entry whose existing target does not exceed 750 non-import
    lines is stale and must be removed.
    """

    limit: int
    rationale: str
    predicate: Callable[[], bool] | None = None


_LINE_LIMIT_EXEMPTIONS: dict[str, LineLimitExemption] = {}
