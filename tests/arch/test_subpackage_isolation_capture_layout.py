from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import SnapshotCaptureReason, SnapshotCaptureStatus
from tests.arch._helpers import SRC_ROOT
from tests.arch._subpackage_isolation_line_limits import _LINE_LIMIT_EXEMPTIONS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_capture_lifecycle_is_a_package_not_a_module() -> None:
    """REQ-CNST-010-DECOMPOSE-3: Step 3 converts the .py file into a package directory."""
    hooks = SRC_ROOT / "hooks"
    assert not (hooks / "_capture_lifecycle.py").exists(), (
        "_capture_lifecycle.py must be removed (replaced by package directory)"
    )
    assert (hooks / "_capture_lifecycle" / "__init__.py").exists(), (
        "_capture_lifecycle/__init__.py must exist as a regular package marker"
    )
    assert (hooks / "_capture_lifecycle" / "_store.py").exists(), (
        "_capture_lifecycle/_store.py must contain the lifecycle store class"
    )
    assert (hooks / "_capture_lifecycle" / "_admission.py").exists(), (
        "_capture_lifecycle/_admission.py must contain the admission helpers"
    )


@pytest.mark.parametrize(
    "package_dir",
    [
        SRC_ROOT / "exploration" / "snapshot",
        SRC_ROOT / "exploration" / "collectors" / "extractors",
    ],
    ids=["snapshot", "extractors"],
)
def test_decomposed_package_is_below_size_ceiling(package_dir: Path) -> None:
    """Every shard of a decomposed package is at most 750 lines.

    Stricter than the global 1000-line guard (``test_no_src_module_exceeds_line_limit``)
    so shard reorganisation fails early instead of colliding with the global cap.
    """
    for shard in sorted(package_dir.glob("*.py")):
        line_count = len(shard.read_text().splitlines())
        assert line_count <= 750, f"{shard.name}: {line_count} lines exceeds 750"


def test_snapshot_is_a_package_not_a_module() -> None:
    """REQ-CNST-010-DECOMPOSE-3: snapshot.py is replaced by snapshot/ directory package."""
    assert not (SRC_ROOT / "exploration" / "snapshot.py").exists()
    snapshot_dir = SRC_ROOT / "exploration" / "snapshot"
    assert snapshot_dir.is_dir()
    assert (snapshot_dir / "__init__.py").is_file()
    for required in ("_records.py", "_capture.py", "_artifact.py"):
        assert (snapshot_dir / required).is_file(), f"missing shard {required}"


def test_collectors_extractors_is_a_package_not_a_module() -> None:
    """REQ-CNST-010-DECOMPOSE-3: collectors/extractors.py is replaced by collectors/extractors/."""
    assert not (SRC_ROOT / "exploration" / "collectors" / "extractors.py").exists()
    extractors_dir = SRC_ROOT / "exploration" / "collectors" / "extractors"
    assert extractors_dir.is_dir()
    assert (extractors_dir / "__init__.py").is_file()
    for required in (
        "_records.py",
        "_evidence.py",
        "_file_search.py",
        "_python_ast.py",
        "_observational.py",
        "_registry.py",
    ):
        assert (extractors_dir / required).is_file(), f"missing shard {required}"


def test_snapshot_facade_all_resolves() -> None:
    """Every public symbol exposed by the original snapshot.py is still resolvable.

    Includes the names that test_snapshot.py monkeypatches through the facade
    and the names that production code resolves via _snapshot_facade lookups
    (resolve_repository_identity, read_stable_contained_file, observe_path_mode,
    DEFAULT_IGNORE_POLICY). A facade that re-exports the public surface but does
    not expose these helpers breaks either the test suite or production
    monkeypatch propagation silently.
    """
    import autoskillit.exploration.snapshot as snapshot_module
    from autoskillit.exploration.snapshot import _artifact as artifact_shard
    from autoskillit.exploration.snapshot import _capture as capture_shard
    from autoskillit.exploration.snapshot import _records as records_shard

    facade_names = {
        # Public API surface (was 11 names)
        "ArtifactCaptureError",
        "ArtifactCaptureStatus",
        "StableArtifactCapture",
        "SnapshotCaptureLimits",
        "SnapshotCaptureReason",
        "SnapshotCaptureResult",
        "SnapshotCaptureStatus",
        "capture_repository_snapshot",
        "capture_stable_artifact",
        "resolve_repository_path",
        "stable_artifact_matches",
        # Helpers tests monkeypatch through the facade
        "_capture_once",
        "activate_repository_profiles",
        "observe_path_mode",
        # Production code resolves these via _snapshot_facade lookups
        "resolve_repository_identity",
        "read_stable_contained_file",
        "DEFAULT_IGNORE_POLICY",
    }
    stdlib_modules: set[str] = set()
    function_anchors = {
        "_capture_once": capture_shard._capture_once,
        "activate_repository_profiles": capture_shard.activate_repository_profiles,
        "observe_path_mode": capture_shard.observe_path_mode,
        "resolve_repository_identity": capture_shard.resolve_repository_identity,
        "read_stable_contained_file": artifact_shard.read_stable_contained_file,
    }
    data_anchors = {
        "DEFAULT_IGNORE_POLICY": records_shard.DEFAULT_IGNORE_POLICY,
    }
    for name in facade_names:
        assert hasattr(snapshot_module, name), (
            f"snapshot facade missing {name} — test_snapshot.py monkeypatch sites "
            f"rely on this re-export"
        )
        if name in stdlib_modules:
            assert getattr(snapshot_module, name) is __import__(name), (
                f"snapshot facade {name} must re-export the stdlib module"
            )
        elif name in function_anchors:
            assert getattr(snapshot_module, name) is function_anchors[name], (
                f"snapshot facade {name} must re-export the function defined in its source shard"
            )
        elif name in data_anchors:
            assert getattr(snapshot_module, name) == data_anchors[name], (
                f"snapshot facade {name} must re-export the value defined in its source shard"
            )


def test_collectors_extractors_facade_all_resolves() -> None:
    """Every public symbol exposed by the original extractors.py is still resolvable."""
    import autoskillit.exploration.collectors.extractors as extractors

    expected = {
        "COLLECTOR_PROFILES",
        "CollectorInvocation",
        "CollectorProfile",
        "collect_architecture",
        "collect_artifact",
        "collect_autoskillit_registry",
        "collect_autoskillit_toml",
        "collect_coverage_observation",
        "collect_file_list",
        "collect_generated_artifact",
        "collect_python_ast",
        "collect_python_stub",
        "collect_search",
        "collect_test_map_observation",
        "collect_unsupported",
        "collector_manifest_digest",
    }
    for name in expected:
        assert hasattr(extractors, name), f"extractors facade missing {name}"


def test_snapshot_shards_do_not_import_the_facade() -> None:
    """Internal snapshot shards must not import the public snapshot facade.

    One-way dependency: shards depend on core/stdlib, facade depends on shards.
    A shard importing the facade (via either ``import X`` or ``from X import Y``)
    re-introduces the cycle the decomposition removed.
    """
    import ast as _ast

    FORBIDDEN_MODULES = {"autoskillit.exploration.snapshot", "autoskillit.exploration"}
    snapshot_dir = SRC_ROOT / "exploration" / "snapshot"
    for shard in sorted(snapshot_dir.glob("_*.py")):
        tree = _ast.parse(shard.read_text())
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom) and node.module:
                if any(
                    node.module == m or node.module.startswith(m + ".") for m in FORBIDDEN_MODULES
                ):
                    pytest.fail(
                        f"snapshot/{shard.name}: {node.lineno}: "
                        f"`from {node.module} import ...` violates one-way graph"
                    )
            elif isinstance(node, _ast.Import):
                for alias in node.names:
                    if any(
                        alias.name == m or alias.name.startswith(m + ".")
                        for m in FORBIDDEN_MODULES
                    ):
                        pytest.fail(
                            f"snapshot/{shard.name}: {node.lineno}: "
                            f"`import {alias.name}` violates one-way graph"
                        )


def test_extractors_shards_do_not_import_the_facade() -> None:
    """Internal extractors shards must not import the public extractors facade."""
    import ast as _ast

    FORBIDDEN_MODULES = {
        "autoskillit.exploration.collectors.extractors",
        "autoskillit.exploration.collectors",
    }
    extractors_dir = SRC_ROOT / "exploration" / "collectors" / "extractors"
    for shard in sorted(extractors_dir.glob("_*.py")):
        tree = _ast.parse(shard.read_text())
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom) and node.module:
                if any(
                    node.module == m or node.module.startswith(m + ".") for m in FORBIDDEN_MODULES
                ):
                    pytest.fail(
                        f"extractors/{shard.name}: {node.lineno}: "
                        f"`from {node.module} import ...` violates one-way graph"
                    )
            elif isinstance(node, _ast.Import):
                for alias in node.names:
                    if any(
                        alias.name == m or alias.name.startswith(m + ".")
                        for m in FORBIDDEN_MODULES
                    ):
                        pytest.fail(
                            f"extractors/{shard.name}: {node.lineno}: "
                            f"`import {alias.name}` violates one-way graph"
                        )


SNAPSHOT_SYMBOL_HOMES: dict[str, str] = {
    # _records.py (dataclasses, errors, status/reason, identity/published split)
    "ArtifactCaptureStatus": "_records",
    "ArtifactCaptureError": "_records",
    "StableArtifactCapture": "_records",
    "SnapshotCaptureLimits": "_records",
    "RepositoryPathState": "_records",
    "_ObservedPath": "_records",
    "CapturedRepositoryState": "_records",
    "SnapshotCaptureResult": "_records",
    "_CaptureAborted": "_records",
    "_expected_status_for_reason": "_records",
    # Module constants — colocated with records because they identify the schema
    "SNAPSHOT_SCHEMA_VERSION": "_records",
    "SNAPSHOT_SCHEMA_ID": "_records",
    "SNAPSHOT_DIGEST_DOMAIN": "_records",
    "DEFAULT_IGNORE_POLICY": "_records",
    "STABLE_ARTIFACT_DIGEST_DOMAIN": "_records",
    "_MAX_STABLE_ARTIFACT_BYTES": "_records",
    "_MAX_STABLE_ARTIFACT_ATTEMPTS": "_records",
    # _capture.py (atomic capture pipeline — must stay cohesive per #4756 / E30 rationale)
    "_check_deadline": "_capture",
    "_git": "_capture",
    "_decode_path": "_capture",
    "_nul_paths": "_capture",
    "_index_records": "_capture",
    "_hash_file": "_capture",
    "_path_state": "_capture",
    "_state_payload": "_capture",
    "_identity_state_payload": "_capture",
    "_untracked_special_paths": "_capture",
    "_capture_once": "_capture",
    "_snapshot_pagination_identity": "_capture",
    "_complete_snapshot": "_capture",
    "_terminal_snapshot": "_capture",
    "capture_repository_snapshot": "_capture",
    "resolve_repository_path": "_capture",
    # _capture_stage.py (extracted via §7.6 size-rebalance protocol)
    "_classify_capture_once_failure": "_capture_stage",
    "_stage": "_capture_stage",
    "_capture_stage": "_capture_stage",
    # _artifact.py (stable-artifact capture, separate public API)
    "_artifact_path": "_artifact",
    "_artifact_deadline_remaining": "_artifact",
    "_artifact_index_records": "_artifact",
    "_artifact_repository_identity": "_artifact",
    "_artifact_unsupported_reason": "_artifact",
    "capture_stable_artifact": "_artifact",
    "stable_artifact_matches": "_artifact",
}


EXTRACTORS_SYMBOL_HOMES: dict[str, str] = {
    # _records.py
    "CollectorInvocation": "_records",
    "CollectorProfile": "_records",
    # _COLLECTOR_VERSION lives in _records because CollectorProfile.version
    # uses it as a runtime dataclass default (avoids _records ↔ _evidence cycle)
    "_COLLECTOR_VERSION": "_records",
    # _evidence.py (manifest digest + report/evidence helpers + metadata lookup)
    "collector_manifest_digest": "_evidence",
    "_OBSERVATION_UNCERTAINTY": "_evidence",
    "_RG_DECODE_DETAIL_MAX_BYTES": "_evidence",
    "_RG_DECODE_RAW_LINE_MAX_BYTES": "_evidence",
    "_RG_DECODE_DIAGNOSTIC_MAX_BYTES": "_evidence",
    "_InvocationReports": "_records",  # colocation rationale in shard docstring
    "_InvocationAdapter": "_records",
    "_PerScopeCollector": "_records",
    "_report": "_evidence",
    "_bounded_diagnostic_text": "_evidence",
    "_invalid_rg_json_diagnostic": "_evidence",
    "_evidence": "_evidence",
    # _collector_metadata lives in _evidence because collector_manifest_digest
    # and _evidence both consult it; moving COLLECTOR_PROFILES to _registry would
    # create a transitive cycle (avoids _evidence ↔ _registry cycle)
    "_collector_metadata": "_evidence",
    # _file_search.py
    "_normalise_scope": "_file_search",
    "_scoped_paths": "_file_search",
    "collect_artifact": "_file_search",
    "collect_file_list": "_file_search",
    "collect_search": "_file_search",
    # _python_ast.py
    "_qualified_name": "_python_ast",
    "_is_named_base": "_python_ast",
    "collect_python_ast": "_python_ast",
    # _observational.py
    "collect_unsupported": "_observational",
    "collect_autoskillit_toml": "_observational",
    "collect_observational_artifact": "_observational",
    "_relabel": "_observational",
    "collect_autoskillit_registry": "_observational",
    "collect_architecture": "_observational",
    "collect_python_stub": "_observational",
    "collect_generated_artifact": "_observational",
    "collect_coverage_observation": "_observational",
    "collect_test_map_observation": "_observational",
    # _registry.py (COLLECTOR_PROFILES data + invocation factories only)
    "_per_scope_invocation": "_registry",
    "_search_invocation": "_registry",
    "_unsupported_invocation": "_registry",
    "COLLECTOR_PROFILES": "_registry",
}


def test_snapshot_symbols_live_in_their_expected_shard() -> None:
    """Every symbol in SNAPSHOT_SYMBOL_HOMES is defined in exactly its named shard."""
    import ast as _ast

    snapshot_dir = SRC_ROOT / "exploration" / "snapshot"
    defined: dict[str, list[str]] = {}
    for shard_file in sorted(snapshot_dir.glob("_*.py")):
        tree = _ast.parse(shard_file.read_text())
        stem = shard_file.stem  # e.g., "_records"
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                defined.setdefault(node.name, []).append(stem)
            elif isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
                if node.value is None:
                    continue
                defined.setdefault(node.target.id, []).append(stem)
            elif isinstance(node, _ast.Assign):
                for target in node.targets:
                    if isinstance(target, _ast.Name):
                        defined.setdefault(target.id, []).append(stem)
    failures = []
    for sym, expected_shard in SNAPSHOT_SYMBOL_HOMES.items():
        homes = defined.get(sym, [])
        if homes != [expected_shard]:
            failures.append(f"{sym}: expected [{expected_shard}], found {homes}")
    assert not failures, "snapshot symbol(s) not in their expected shard:\n" + "\n".join(
        f"  {f}" for f in failures
    )


def test_extractors_symbols_live_in_their_expected_shard() -> None:
    """Every symbol in EXTRACTORS_SYMBOL_HOMES is defined in exactly its named shard."""
    import ast as _ast

    extractors_dir = SRC_ROOT / "exploration" / "collectors" / "extractors"
    defined: dict[str, list[str]] = {}
    for shard_file in sorted(extractors_dir.glob("_*.py")):
        tree = _ast.parse(shard_file.read_text())
        stem = shard_file.stem
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                defined.setdefault(node.name, []).append(stem)
            elif isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
                if node.value is None:
                    continue
                defined.setdefault(node.target.id, []).append(stem)
            elif isinstance(node, _ast.Assign):
                for target in node.targets:
                    if isinstance(target, _ast.Name):
                        defined.setdefault(target.id, []).append(stem)
    failures = []
    for sym, expected_shard in EXTRACTORS_SYMBOL_HOMES.items():
        homes = defined.get(sym, [])
        if homes != [expected_shard]:
            failures.append(f"{sym}: expected [{expected_shard}], found {homes}")
    assert not failures, "extractors symbol(s) not in their expected shard:\n" + "\n".join(
        f"  {f}" for f in failures
    )


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        # Matching pairs — must succeed (raises nothing)
        (SnapshotCaptureStatus.TRUNCATED, SnapshotCaptureReason.PATH_COUNT_EXCEEDED),
        (SnapshotCaptureStatus.TRUNCATED, SnapshotCaptureReason.FILE_BYTES_EXCEEDED),
        (SnapshotCaptureStatus.TRUNCATED, SnapshotCaptureReason.TOTAL_BYTES_EXCEEDED),
        (SnapshotCaptureStatus.STALE, SnapshotCaptureReason.IDENTITY_DRIFT),
        (SnapshotCaptureStatus.FAILED, SnapshotCaptureReason.CAPTURE_DEADLINE_EXCEEDED),
        (SnapshotCaptureStatus.FAILED, SnapshotCaptureReason.GIT_TIMEOUT),
        (SnapshotCaptureStatus.FAILED, SnapshotCaptureReason.GIT_COMMAND_FAILED),
        (SnapshotCaptureStatus.FAILED, SnapshotCaptureReason.ROOT_NOT_WORKTREE),
        (SnapshotCaptureStatus.FAILED, SnapshotCaptureReason.IDENTITY_UNRESOLVED),
        (SnapshotCaptureStatus.FAILED, SnapshotCaptureReason.PROFILE_ACTIVATION_FAILED),
        (SnapshotCaptureStatus.FAILED, SnapshotCaptureReason.WORKTREE_UNREADABLE),
        (SnapshotCaptureStatus.FAILED, SnapshotCaptureReason.COLLECTOR_SAFETY_FAULT),
        (SnapshotCaptureStatus.FAILED, SnapshotCaptureReason.MANIFEST_DIGEST_EMPTY),
    ],
)
def test_capture_aborted_accepts_legal_status_reason_pairs(
    status: SnapshotCaptureStatus,
    reason: SnapshotCaptureReason,
) -> None:
    """Every legal (status, reason) pair constructs cleanly."""
    from autoskillit.exploration.snapshot._records import _CaptureAborted

    _CaptureAborted(status, reason, "detail")  # must not raise


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        # Mismatched pairs — assertion must fire
        (SnapshotCaptureStatus.TRUNCATED, SnapshotCaptureReason.GIT_TIMEOUT),
        (SnapshotCaptureStatus.TRUNCATED, SnapshotCaptureReason.CAPTURE_DEADLINE_EXCEEDED),
        (SnapshotCaptureStatus.FAILED, SnapshotCaptureReason.PATH_COUNT_EXCEEDED),
        (SnapshotCaptureStatus.FAILED, SnapshotCaptureReason.FILE_BYTES_EXCEEDED),
        (SnapshotCaptureStatus.COMPLETE, SnapshotCaptureReason.PATH_COUNT_EXCEEDED),
    ],
)
def test_capture_aborted_rejects_illegal_status_reason_pairs(
    status: SnapshotCaptureStatus,
    reason: SnapshotCaptureReason,
) -> None:
    """An illegal (status, reason) pair must trigger the __init__ assertion."""
    from autoskillit.exploration.snapshot._records import _CaptureAborted

    with pytest.raises(AssertionError):
        _CaptureAborted(status, reason, "detail")


def test_collector_registry_preserves_13_entries_in_order() -> None:
    """Decomposition must not change the COLLECTOR_PROFILES tuple."""
    from autoskillit.exploration.collectors import COLLECTOR_PROFILES
    from autoskillit.exploration.collectors.extractors import collector_manifest_digest

    expected_ids = (
        "contained-artifact",
        "contained-list",
        "bounded-rg-search",
        "python-ast",
        "native-lsp",
        "native-tree-sitter",
        "autoskillit-registry",
        "autoskillit-manifest",
        "autoskillit-architecture",
        "python-stub",
        "generated-artifact",
        "coverage-observation",
        "test-map-observation",
    )
    actual_ids = tuple(p.collector_id for p in COLLECTOR_PROFILES)
    assert actual_ids == expected_ids
    actual_digest = collector_manifest_digest()
    expected_digest = "0b5d94f7f018c4bf7df84a370cabfb4b5e32c09dfedb6ca3d43e04e1ed2126df"
    assert actual_digest == expected_digest, (
        f"COLLECTOR_PROFILES digest drifted from {expected_digest!r} to {actual_digest!r}. "
        f"This signals a registry change (added/removed collector, reordered tuple, "
        f"changed method/profile string, or version bump in _COLLECTOR_VERSION). "
        f"Inspect git log for collector changes; if the change is intentional, "
        f"update expected_digest in test_collector_registry_preserves_13_entries_in_order."
    )


def test_e30_exemption_is_retired() -> None:
    """REQ-CNST-010-E30 is retired without replacement after #4836 lands."""
    exemptions = _LINE_LIMIT_EXEMPTIONS
    assert "exploration/snapshot.py" not in exemptions, (
        "E30 must be removed from _LINE_LIMIT_EXEMPTIONS; "
        "snapshot.py no longer exists as a module after decomposition"
    )


def test_ignored_bytes_accounting_originates_in_records_shard() -> None:
    """_ObservedPath and the identity-state-payload logic live in the right shards.

    The two-byte-accounting split (#4756) keeps _ObservedPath with its
    identity_content_digest field in _records.py; the producer
    (_path_state) and the payload helper that surfaces the private digest
    (_identity_state_payload) stay in _capture.py.
    """
    records_path = SRC_ROOT / "exploration" / "snapshot" / "_records.py"
    capture_path = SRC_ROOT / "exploration" / "snapshot" / "_capture.py"
    records_text = records_path.read_text()
    capture_text = capture_path.read_text()
    assert "class _ObservedPath" in records_text, "_ObservedPath must live in _records.py"
    assert "identity_content_digest" in records_text, "_records.py owns identity/published split"
    assert "class _ObservedPath" not in capture_text, "_ObservedPath stays in _records.py"
    assert "def _identity_state_payload" in capture_text, (
        "_identity_state_payload lives in _capture.py where _path_state threads the public digest"
    )
