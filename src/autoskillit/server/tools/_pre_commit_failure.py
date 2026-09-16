"""Pre-commit failure classification helpers.

Split out of ``tools_workspace.py`` to keep that module under REQ-CNST-010's
750 non-import line cap. The classification logic is consumed by the
``commit_files`` tool to attribute hook failures to infrastructure vs.
rejection vs. unhandled output.
"""

from __future__ import annotations

from autoskillit.core import CommitFailureClass

__all__ = [
    "parse_combined_process_output",
    "parse_hook_failure_class",
    "pre_commit_failure_class",
]

_PRE_COMMIT_INFRASTRUCTURE_SIGNATURES = frozenset(
    {
        "os error 30",
        "read-only file system",
    }
)
_PRE_COMMIT_CACHE_PATH_SIGNATURES = frozenset({".cache", "/cache/", "uv-cache"})


def parse_combined_process_output(stderr: str, stdout: str, fallback: str) -> str:
    combined = "\n".join(part for part in (stderr.strip(), stdout.strip()) if part)
    return combined or fallback


def pre_commit_failure_class(output: str) -> CommitFailureClass:
    normalized = output.casefold()
    known_infrastructure_failure = any(
        signature in normalized for signature in _PRE_COMMIT_INFRASTRUCTURE_SIGNATURES
    )
    cache_permission_failure = "permission denied" in normalized and any(
        signature in normalized for signature in _PRE_COMMIT_CACHE_PATH_SIGNATURES
    )
    if known_infrastructure_failure or cache_permission_failure:
        return CommitFailureClass.HOOK_INFRASTRUCTURE
    # Callers always supply a non-empty diagnostic via parse_combined_process_output's
    # fallback. If a future caller bypasses that helper and passes an empty
    # string, classify the failure as UNHANDLED rather than mis-attribute it as
    # a user-rejected hook.
    if not normalized.strip():
        return CommitFailureClass.UNHANDLED
    return CommitFailureClass.HOOK_REJECTED


def parse_hook_failure_class(raw: object) -> CommitFailureClass:
    """Resolve a raw ``failure_class`` from hook output to a known member.

    Falls back to ``UNHANDLED`` for missing keys or unrecognized values rather
    than propagating ``KeyError`` / ``ValueError`` to the outer ledger envelope,
    which would lose the original classification signal and corrupt accounting.
    """
    if isinstance(raw, CommitFailureClass):
        return raw
    if not isinstance(raw, str) or not raw:
        return CommitFailureClass.UNHANDLED
    try:
        return CommitFailureClass(raw)
    except ValueError:
        return CommitFailureClass.UNHANDLED
