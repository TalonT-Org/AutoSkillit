"""Fidelity dimension helpers: linked issue extraction and finding validation.

Used by the review-pr skill to extract GitHub issue references from PR body
and commit messages, then launch a fidelity audit subagent.
"""

from __future__ import annotations

import re
from typing import Any

_LINKED_ISSUE_PATTERN = re.compile(
    r"(?:closes|fixes|resolves)\s+#(\d+)",
    re.IGNORECASE,
)

_VALID_FIDELITY_SEVERITIES = frozenset({"critical", "warning"})


def extract_linked_issues(text: str) -> list[str]:
    """Extract GitHub issue numbers from Closes/Fixes/Resolves references.

    Returns a deduplicated sorted list of issue number strings (e.g. ["123", "456"]).
    The input may be PR body text, commit messages, or any concatenation thereof.

    Examples::

        >>> extract_linked_issues("Closes #123")
        ['123']
        >>> extract_linked_issues("Fixes #456\\nCloses #123")
        ['123', '456']
        >>> extract_linked_issues("No refs here")
        []
    """
    numbers = _LINKED_ISSUE_PATTERN.findall(text)
    return sorted(set(numbers), key=int)


def is_valid_fidelity_finding(finding: dict[str, Any]) -> bool:
    """Return True if a finding dict has the correct fidelity format.

    Required fields:
    - dimension == "fidelity"
    - severity in {"critical", "warning"}
    - file: str
    - line: int
    - message: str
    - requires_decision: bool
    """
    return (
        finding.get("dimension") == "fidelity"
        and finding.get("severity") in _VALID_FIDELITY_SEVERITIES
        and isinstance(finding.get("file"), str)
        and isinstance(finding.get("line"), int)
        and not isinstance(finding.get("line"), bool)
        and isinstance(finding.get("message"), str)
        and isinstance(finding.get("requires_decision"), bool)
    )
