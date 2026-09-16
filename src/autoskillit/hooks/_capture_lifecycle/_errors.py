"""Domain errors shared by capture lifecycle stores and transactions."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

if TYPE_CHECKING:
    from autoskillit.hooks._capture import _capacity as _capture_capacity
    from autoskillit.hooks._capture import _failure_policy as _capture_failure_policy
    from autoskillit.hooks._capture import _module_identity
    from autoskillit.hooks._capture import _types as _capture_types
else:
    _capture_capacity = importlib.import_module("_capture._capacity")
    _capture_failure_policy = importlib.import_module("_capture._failure_policy")
    _module_identity = importlib.import_module("_capture._module_identity")
    _capture_types = importlib.import_module("_capture._types")

_module_identity.register_module_aliases(__name__)

CaptureCapacityReason = _capture_types.CaptureCapacityReason


class CaptureLifecycleError(RuntimeError):
    failure_reason = _capture_failure_policy.CaptureFailureReason.LEDGER_INTEGRITY

    @classmethod
    def from_os_error(
        cls,
        detail: str,
        exc: OSError,
    ) -> CaptureLifecycleError:
        error = cls(detail)
        error.failure_reason = _capture_failure_policy.os_failure_reason(exc)
        return error


class CaptureLedgerError(CaptureLifecycleError):
    reason = "corrupt"
    observed_version: int | None = None
    current_version: int | None = None


class CaptureCapacityError(CaptureLedgerError):
    def __init__(
        self,
        reason: CaptureCapacityReason,
        assist_transition_limit: int | None = None,
    ) -> None:
        self.reason = reason
        self.assist_transition_limit = assist_transition_limit
        self.failure_reason = _capture_capacity.failure_reason(reason)
        super().__init__(_capture_capacity.reason_detail(reason))
