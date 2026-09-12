"""Accepted cyclomatic-complexity limits for scripts/check_complexity.py.

Keys are `path::qualname` (repository-relative POSIX path, then the function's dotted
qualified name as reported by scripts/check_complexity.py). A function without an entry
here is allowed up to MAX_COMPLEXITY, or its complexity at the base revision if that is
higher -- see the ratchet rule in scripts/check_complexity.py.

Refactor first: reduce decision points before reaching for an exemption. An exemption is a
last resort, human-approved via a PolicyRelaxationApproval in
tests/arch/_acceptance_policy_surfaces.py naming a tracking issue and the human who
approved it -- an automated session must not add that approval itself.

Every exemption's rationale must describe the attempted refactor and why it was not viable,
in at least MIN_RATIONALE_CHARS characters.

An exemption self-invalidates -- and fails the check in both warn and fail mode -- when its
function: (1) no longer exists at the key's path::qualname, (2) has dropped to
MAX_COMPLEXITY or below (the exemption is no longer needed), or (3) exceeds its own
`limit` (the exemption no longer covers the actual complexity).
"""

from __future__ import annotations

import dataclasses

MAX_COMPLEXITY = 10  # acceptance-policy surface (int_scalar; registered in Step 5)
MIN_RATIONALE_CHARS = 60


@dataclasses.dataclass(frozen=True, slots=True)
class ComplexityExemption:
    """One function's approved complexity ceiling, above MAX_COMPLEXITY."""

    limit: int  # keyword name is load-bearing: check_policy_relaxation reads `limit=`
    rationale: str


COMPLEXITY_EXEMPTIONS: dict[
    str, ComplexityExemption
] = {}  # acceptance-policy surface (exemption_map)
