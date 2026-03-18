"""PR analysis helpers: linked issue extraction, fidelity finding validation,
and file domain partitioning.

These utilities serve the review-pr, analyze-prs, and open-integration-pr
skills. They have no dependency on pipeline state management and belong in
execution/ as headless skill result helpers.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Linked issue extraction (used by review-pr fidelity subagent)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# File domain partitioning (used by analyze-prs and open-integration-pr skills)
# ---------------------------------------------------------------------------

DOMAIN_PATHS: dict[str, list[str]] = {
    "Server/MCP Tools": ["src/autoskillit/server/"],
    "Pipeline/Execution": ["src/autoskillit/execution/", "src/autoskillit/pipeline/"],
    "Recipe/Validation": ["src/autoskillit/recipe/"],
    "CLI/Workspace": ["src/autoskillit/cli/", "src/autoskillit/workspace/"],
    "Skills": ["src/autoskillit/skills/", "src/autoskillit/skills_extended/"],
    "Tests": ["tests/"],
    "Core/Config/Infra": [
        "src/autoskillit/core/",
        "src/autoskillit/config/",
        "src/autoskillit/migration/",
        "src/autoskillit/hooks/",
        "src/autoskillit/recipes/",
    ],
}


def partition_files_by_domain(
    file_paths: list[str],
    domain_paths: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Bucket each file path into the first matching domain prefix.

    Unmatched paths go to "Other".
    """
    mapping = domain_paths if domain_paths is not None else DOMAIN_PATHS
    buckets: dict[str, list[str]] = {}

    for path in file_paths:
        assigned = False
        for domain, prefixes in mapping.items():
            if any(path.startswith(prefix) for prefix in prefixes):
                buckets.setdefault(domain, []).append(path)
                assigned = True
                break
        if not assigned:
            buckets.setdefault("Other", []).append(path)

    return buckets
