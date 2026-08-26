"""Pure eligibility predicates for explorer binding and exploration auto-provisioning.

Both predicates are pure functions of their inputs; they own no state.  The
server corridor (``_explorer_projection.py``) and the two boot entry points
(``pre_reveal_kitchen`` and ``open_kitchen``) call them directly.
"""

from __future__ import annotations

from autoskillit.core import SessionType

from ._constants import EXPLORER_INELIGIBLE_SESSION_TYPES


def is_explorer_binding_eligible(
    *,
    has_identity: bool,
    has_backend: bool,
    terminal_explorer_capable: bool,
    session_scoped_explorer_capable: bool,
    parent_sandbox_mode: str,
    session_type: SessionType | None = None,
) -> bool:
    """Pure eligibility predicate for explorer binding mint.

    Used by the server corridor in ``_explorer_projection.py``.  The server
    wrapper adds store presence and invocation-identity resolution; this
    function owns only the structural gates.
    """
    if not has_identity or not has_backend:
        return False
    if session_type in EXPLORER_INELIGIBLE_SESSION_TYPES:
        return False
    if terminal_explorer_capable or session_scoped_explorer_capable:
        return parent_sandbox_mode == "read-only"
    return False


def exploration_auto_provision_eligible(
    *, auto_provision: bool, session_type: SessionType
) -> bool:
    """Pure eligibility predicate for exploration tag auto-provisioning at boot.

    Shared by both boot entry points (pre_reveal_kitchen and open_kitchen) so
    the "is auto-provisioning eligible for this session" rule is defined once.
    Visibility-only — the per-call HMAC capability lease minted by
    enable_exploration remains the authorization boundary regardless of tag
    visibility.
    """
    return auto_provision and session_type not in EXPLORER_INELIGIBLE_SESSION_TYPES
