"""Branch protection validation.

Pure-function guards for protected-branch enforcement.
No I/O, no subprocess calls — caller provides all inputs.
"""

from __future__ import annotations


def is_protected_branch(
    branch: str,
    protected: list[str],
) -> bool:
    """Return True if *branch* is in the protected list.

    Comparison is exact and case-sensitive (git branch names are
    case-sensitive). Empty strings are never protected.

    Parameters
    ----------
    branch:
        Branch name to check.
    protected:
        Caller-supplied list of protected branch names, typically
        sourced from ``config.safety.protected_branches``.
    """
    if not branch:
        return False
    return branch in protected
