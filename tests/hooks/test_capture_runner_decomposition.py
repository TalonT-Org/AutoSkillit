"""Capture-runner facade decomposition contracts."""

from __future__ import annotations

import importlib
import sys

import pytest

import autoskillit.hooks._capture._authority as capture_authority
import autoskillit.hooks._capture._reconcile as capture_reconcile
import autoskillit.hooks._capture._runner as capture_runner
import autoskillit.hooks._capture._types as capture_types
import autoskillit.hooks._capture_artifacts as capture_artifacts

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def test_capture_artifacts_facade_preserves_canonical_identities() -> None:
    assert capture_artifacts.__all__ == [
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

    assert capture_artifacts.CAPTURE_PATH_COMPONENTS is capture_authority.CAPTURE_PATH_COMPONENTS
    assert capture_artifacts.CaptureRoot is capture_authority.CaptureRoot
    assert capture_artifacts.CaptureSetupError is capture_authority.CaptureSetupError
    assert capture_artifacts.ProjectAnchor is capture_authority.ProjectAnchor
    assert capture_artifacts.open_capture_lifecycle is capture_authority.open_capture_lifecycle
    assert capture_artifacts.open_capture_root is capture_authority.open_capture_root
    assert capture_artifacts.open_project_anchor is capture_authority.open_project_anchor

    assert capture_artifacts.CaptureStoreStats is capture_reconcile.CaptureStoreStats
    assert capture_artifacts.capture_store_stats is capture_reconcile.capture_store_stats
    assert capture_artifacts.reconcile_capture_store is capture_reconcile.reconcile_capture_store

    assert capture_artifacts.CleanupBlocker is capture_types.CleanupBlocker
    assert capture_artifacts.CleanupProgress is capture_types.CleanupProgress
    assert capture_artifacts.SweepBudgetSpec is capture_types.SweepBudgetSpec

    assert capture_artifacts.CaptureArtifact is capture_runner.CaptureArtifact
    assert capture_artifacts.CapturePolicy is capture_runner.CapturePolicy
    assert capture_artifacts.create_capture_artifact is capture_runner.create_capture_artifact
    assert capture_artifacts.read_capture_policy is capture_runner.read_capture_policy
    assert capture_artifacts.run_capture is capture_runner.run_capture
    assert (
        capture_artifacts.verify_reference_publication_binding
        is capture_runner.verify_reference_publication_binding
    )


def test_runner_module_identity_uses_both_supported_import_spellings() -> None:
    dotted = importlib.import_module("autoskillit.hooks._capture._runner")
    short = importlib.import_module("_capture._runner")

    assert dotted is short
    assert sys.modules.get("autoskillit.hooks._capture._runner") is dotted
    assert sys.modules.get("_capture._runner") is dotted
