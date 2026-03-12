"""Branch protection validation.

Pure-function guards for protected-branch enforcement.
No I/O, no subprocess calls — caller provides all inputs.
"""

from __future__ import annotations

_DEFAULT_PROTECTED: list[str] = ["main", "integration", "stable"]


def is_protected_branch(
    branch: str,
    protected: list[str] | None = None,
) -> bool:
    """Return True if *branch* is in the protected list.

    Comparison is exact and case-sensitive (git branch names are
    case-sensitive). Empty strings are never protected.

    Parameters
    ----------
    branch:
        Branch name to check.
    protected:
        Override list. When ``None``, uses the module default
        (main, integration, stable). Pass an explicit list to
        use config-driven values.
    """
    if not branch:
        return False
    targets = protected if protected is not None else _DEFAULT_PROTECTED
    return branch in targets
