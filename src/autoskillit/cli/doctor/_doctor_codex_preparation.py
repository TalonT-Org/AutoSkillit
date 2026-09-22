"""Managed Codex preparation doctor check."""

from __future__ import annotations

import time
from pathlib import Path

from autoskillit.core import CodingAgentBackend, Severity, resolve_temp_dir
from autoskillit.execution import CodexBackend

from ._doctor_types import DoctorResult


def _check_codex_managed_preparation(
    *,
    backend: CodingAgentBackend | None = None,
    configured_model: str,
    project_dir: Path,
    workspace_temp_dir: str | None = None,
) -> DoctorResult:
    """Probe cache-independent managed Codex preparation without issuing authority."""
    check_name = "codex_managed_preparation"
    if not isinstance(backend, CodexBackend):
        return DoctorResult(
            Severity.OK,
            check_name,
            "Skipped (selected backend has no managed Codex preparation route)",
        )
    scratch_root = (
        resolve_temp_dir(project_dir, workspace_temp_dir) / "doctor-managed-codex-preparation"
    )
    try:
        model, effort, _projection = backend.prepare_managed_codex_catalog(
            configured_model,
            scratch_root=scratch_root,
            deadline=time.monotonic() + 10.0,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Managed Codex preparation unavailable: {exc}",
        )
    return DoctorResult(
        Severity.OK,
        check_name,
        f"Managed Codex preparation ready for model={model}, effort={effort}.",
    )
