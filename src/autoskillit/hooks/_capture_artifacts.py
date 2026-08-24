"""Public executable facade for the descriptor-anchored capture runner."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

_HOOKS_DIR = str(Path(__file__).resolve().parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

if TYPE_CHECKING:
    from autoskillit.hooks._capture._authority import (
        CAPTURE_PATH_COMPONENTS,
        CaptureRoot,
        CaptureSetupError,
        ProjectAnchor,
        open_capture_lifecycle,
        open_capture_root,
        open_project_anchor,
    )
    from autoskillit.hooks._capture._reconcile import (
        CaptureStoreStats,
        CleanupBlocker,
        CleanupProgress,
        SweepBudgetSpec,
        capture_store_stats,
        reconcile_capture_store,
    )
    from autoskillit.hooks._capture._runner import (
        CaptureArtifact,
        CapturePolicy,
        _main,
        create_capture_artifact,
        read_capture_policy,
        run_capture,
        verify_reference_publication_binding,
    )
elif __package__ in (None, ""):
    from _capture._authority import (
        CAPTURE_PATH_COMPONENTS,
        CaptureRoot,
        CaptureSetupError,
        ProjectAnchor,
        open_capture_lifecycle,
        open_capture_root,
        open_project_anchor,
    )
    from _capture._reconcile import (
        CaptureStoreStats,
        CleanupBlocker,
        CleanupProgress,
        SweepBudgetSpec,
        capture_store_stats,
        reconcile_capture_store,
    )
    from _capture._runner import (
        CaptureArtifact,
        CapturePolicy,
        _main,
        create_capture_artifact,
        read_capture_policy,
        run_capture,
        verify_reference_publication_binding,
    )
else:
    from ._capture._authority import (
        CAPTURE_PATH_COMPONENTS,
        CaptureRoot,
        CaptureSetupError,
        ProjectAnchor,
        open_capture_lifecycle,
        open_capture_root,
        open_project_anchor,
    )
    from ._capture._reconcile import (
        CaptureStoreStats,
        CleanupBlocker,
        CleanupProgress,
        SweepBudgetSpec,
        capture_store_stats,
        reconcile_capture_store,
    )
    from ._capture._runner import (
        CaptureArtifact,
        CapturePolicy,
        _main,
        create_capture_artifact,
        read_capture_policy,
        run_capture,
        verify_reference_publication_binding,
    )

__all__ = [
    "CAPTURE_PATH_COMPONENTS",
    "CaptureArtifact",
    "CapturePolicy",
    "CaptureRoot",
    "CaptureSetupError",
    "CaptureStoreStats",
    "CleanupBlocker",
    "CleanupProgress",
    "ProjectAnchor",
    "SweepBudgetSpec",
    "capture_store_stats",
    "create_capture_artifact",
    "open_capture_lifecycle",
    "open_capture_root",
    "open_project_anchor",
    "read_capture_policy",
    "reconcile_capture_store",
    "run_capture",
    "verify_reference_publication_binding",
]


if __name__ == "__main__":
    sys.exit(_main())
