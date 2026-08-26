"""Capture-stage helpers that operate ABOVE the atomic capture loop.

Extracted from ``_capture.py`` per the plan's §7.6 size-rebalance protocol —
``_capture.py`` exceeded 750 lines because of these three helpers. They are
the only helpers that operate ABOVE ``_capture_once`` and do not participate
in deadline propagation, so they can move to a sibling shard without
separating the budget plumbing from the single capture loop it threads
through.

Contains:
- ``_classify_capture_once_failure`` — exception-class dispatch.
- ``_stage`` — one identity/activation operation wrapper.
- ``_capture_stage`` — wraps ``_capture_once`` with classification, looking
  up ``_capture_once`` through the facade module so monkeypatch sites
  (``test_snapshot.py:426, 898``) propagate.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from autoskillit.core import (
    SnapshotCaptureReason,
    SnapshotCaptureStatus,
)

from ..collectors._bounded import CollectorSafetyError
from ._records import (
    CapturedRepositoryState,
    SnapshotCaptureLimits,
    _CaptureAborted,
)

_StageResult = TypeVar("_StageResult")

# Capture the facade module so ``_capture_stage`` looks up ``_capture_once``
# through the package attribute. Same late-binding rationale as in
# ``_capture.py`` and ``_artifact.py``: ``test_snapshot.py`` monkeypatches
# ``snapshot_module._capture_once`` (lines 426, 898) and expects the patch to
# propagate. The cycle resolves via ``sys.modules``.
_snapshot_facade = sys.modules[__package__ or "autoskillit.exploration.snapshot"]


def _classify_capture_once_failure(exc: Exception) -> SnapshotCaptureReason:
    """Map an exception that escaped ``_capture_once`` to its terminal cause.

    Dispatches on exception class first (the source-of-truth signal), the same
    discipline ``_artifact_unsupported_reason`` documents below: a new error
    message that happens to share a substring with an existing case must not
    silently misclassify. ``CollectorSafetyError`` is checked before the
    generic ``ValueError`` arm because it is one of that class's subclasses;
    the only member of the caught tuple left once ``TimeoutExpired``,
    ``ValueError``, and ``RuntimeError`` are excluded is ``OSError``.
    """
    if isinstance(exc, subprocess.TimeoutExpired):
        return SnapshotCaptureReason.GIT_TIMEOUT
    if isinstance(exc, CollectorSafetyError):
        return SnapshotCaptureReason.COLLECTOR_SAFETY_FAULT
    if isinstance(exc, ValueError):
        return SnapshotCaptureReason.ROOT_NOT_WORKTREE
    if isinstance(exc, RuntimeError):
        return SnapshotCaptureReason.GIT_COMMAND_FAILED
    return SnapshotCaptureReason.WORKTREE_UNREADABLE


def _stage(
    reason: SnapshotCaptureReason,
    func: Callable[..., _StageResult],
    *args: object,
    **kwargs: object,
) -> _StageResult:
    """Run one identity/activation operation, attaching one fixed reason to any
    failure. Unlike ``_capture_stage`` below, this operation has exactly one
    meaningful way to fail at this level, so no further classification is
    needed."""
    try:
        return func(*args, **kwargs)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        raise _CaptureAborted(
            SnapshotCaptureStatus.FAILED, reason, f"{type(exc).__name__}: {exc}"
        ) from exc


def _capture_stage(
    root: Path, limits: SnapshotCaptureLimits, *, deadline: float
) -> CapturedRepositoryState:
    """Run one ``_capture_once`` attempt, classifying any exception that escapes it.

    A ``_CaptureAborted`` raised from inside ``_capture_once`` already names its
    own cause (a budget trip or a deadline overrun) and must pass through
    unmodified — reclassifying it here by "which stage failed" would collapse
    all three truncation reasons onto ``GIT_COMMAND_FAILED`` and status
    ``FAILED``, silently defeating the distinction those raise sites exist to
    make.

    ``_capture_once`` is resolved through the facade module so ``test_snapshot.py``
    monkeypatches ``snapshot_module._capture_once`` (lines 426, 898) propagate.
    """
    try:
        return _snapshot_facade._capture_once(root, limits, deadline=deadline)
    except _CaptureAborted:
        raise
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        raise _CaptureAborted(
            SnapshotCaptureStatus.FAILED,
            _classify_capture_once_failure(exc),
            f"{type(exc).__name__}: {exc}",
        ) from exc
