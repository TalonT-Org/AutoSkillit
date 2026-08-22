"""Shared error types for shell-capture transport (stdlib-only).

Holds the cross-cutting ``CaptureContractError`` exception so child
modules in ``_capture/`` do not need to import from their parent facade.
"""

from __future__ import annotations

from . import _failure_policy
from ._module_identity import register_module_aliases

register_module_aliases(__name__)


class CaptureContractError(ValueError):
    """Raised when a V2 capture transport value is invalid or noncanonical."""

    failure_reason = _failure_policy.CaptureFailureReason.LEDGER_INTEGRITY
