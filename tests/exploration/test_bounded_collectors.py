from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import CollectorStatus, RelationshipKind
from autoskillit.exploration.collectors import (
    CollectorLimits,
    CollectorSafetyError,
    _bounded,
    list_contained_files,
    read_contained_file,
    resolve_contained_path,
)
from autoskillit.exploration.collectors.extractors import (
    COLLECTOR_PROFILES,
    collect_artifact,
    collect_autoskillit_registry,
    collect_autoskillit_toml,
    collect_coverage_observation,
    collect_generated_artifact,
    collect_python_ast,
    collect_test_map_observation,
    collect_unsupported,
    collector_manifest_digest,
)
from autoskillit.exploration.graph import build_canonical_evidence_graph

pytestmark = [
    pytest.mark.layer("exploration"),
    pytest.mark.feature("exploration"),
    pytest.mark.medium,
]


def test_read_contained_file_rejects_parent_escape_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("credential")
    (root / "linked.txt").symlink_to(outside)

    with pytest.raises(CollectorSafetyError, match="contained"):
        resolve_contained_path(root, "../secret.txt")
    with pytest.raises(CollectorSafetyError, match="escapes collector root"):
        read_contained_file(root, "linked.txt", CollectorLimits())


def test_read_contained_file_enforces_byte_limit(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "large.txt").write_bytes(b"x" * 9)

    with pytest.raises(CollectorSafetyError, match="byte limit"):
        read_contained_file(root, "large.txt", CollectorLimits(max_file_bytes=8))


def test_bounded_rg_resolves_host_executable_before_sterile_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    host_bin = tmp_path / "host-bin"
    host_bin.mkdir()
    fake_rg = host_bin / "rg"
    fake_rg.write_text('#!/bin/sh\n[ "$PATH" = "/usr/bin:/bin" ] || exit 2\nexit 1\n')
    fake_rg.chmod(0o755)
    monkeypatch.setenv("PATH", str(host_bin))

    result = _bounded.run_bounded_rg(root, "needle", limits=CollectorLimits())

    assert result.failure is None
    assert result.returncode == 1


def test_bounded_rg_rejects_repository_controlled_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    fake_rg = root / "rg"
    fake_rg.write_text("#!/bin/sh\nexit 1\n")
    fake_rg.chmod(0o755)
    monkeypatch.setenv("PATH", str(root))

    result = _bounded.run_bounded_rg(root, "needle", limits=CollectorLimits())

    assert result.failure == "rg unavailable (untrusted repository path)"
    assert result.returncode is None


def test_read_contained_file_rejects_post_open_inode_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    artifact = root / "artifact.txt"
    artifact.write_text("approved")
    replacement = root / "replacement.txt"
    replacement.write_text("replacement")
    original_open = _bounded.os.open

    def swap_after_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if path == "artifact.txt" and dir_fd is not None:
            os.replace(replacement, artifact)
        return descriptor

    monkeypatch.setattr(_bounded.os, "open", swap_after_open)

    with pytest.raises(CollectorSafetyError, match="changed while opening"):
        read_contained_file(root, "artifact.txt", CollectorLimits())


def test_contained_open_rejects_special_file_without_reading_it(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    pipe = root / "collector.pipe"
    os.mkfifo(pipe)

    with pytest.raises(CollectorSafetyError, match="non-symlink regular file"):
        _bounded._open_contained_regular_file(root, pipe.name)


def test_list_contained_files_is_sorted_and_skips_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "nested").mkdir(parents=True)
    (root / "z.txt").write_text("z")
    (root / "nested" / "a.txt").write_text("a")
    (root / "linked.txt").symlink_to(root / "z.txt")

    assert list_contained_files(root, CollectorLimits()) == ("nested/a.txt", "z.txt")


def test_list_contained_files_fails_closed_at_limit(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("a")
    (root / "b.txt").write_text("b")

    with pytest.raises(CollectorSafetyError, match="limit exceeded"):
        list_contained_files(root, CollectorLimits(max_files=1))


def test_collector_manifest_is_derived_from_the_versioned_registry() -> None:
    original = collector_manifest_digest()
    changed = (
        replace(COLLECTOR_PROFILES[0], version="test-mutated-version"),
        *COLLECTOR_PROFILES[1:],
    )

    assert original != collector_manifest_digest(changed)


def test_seeded_collectors_observe_structural_and_artifact_inputs_without_claiming_execution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    package = root / "package"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("from .module import implementation as public\n")
    (package / "module.py").write_text(
        "from abc import ABC\n"
        "from typing import Protocol\n"
        "import importlib\n"
        "import collections as collections_alias\n"
        "alias = collections_alias\n\n"
        "class NominalContract(ABC):\n"
        "    pass\n\n"
        "class Contract(Protocol):\n"
        "    def run(self) -> None: ...\n\n"
        "@override\n"
        "def implementation() -> Contract:\n"
        "    runtime.handler = implementation\n"
        "    importlib.import_module('package.plugin')\n"
        "    importlib.import_module(dynamic_plugin)\n"
        "    monkeypatch.setattr(runtime, 'handler', implementation)\n"
        "    return Contract()\n\n"
        "REGISTRY = {'public': 'package.module'}\n"
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_consumers.py").write_text("def test_consumer():\n    implementation()\n")
    (root / "registry.py").write_text("REGISTRY = {'public': 'package.module'}\n")
    (root / "config.toml").write_text("[collector]\nenabled = true\n")
    (root / "artifact.json").write_text('{"artifact":"observed"}\n')
    (root / "coverage.json").write_text('{"coverage":"observed"}\n')
    (root / "test-map.json").write_text('{"tests":"observed"}\n')
    limits = CollectorLimits(max_matches=100)

    structural = collect_python_ast(root, "snapshot", "", limits)
    registry = collect_autoskillit_registry(root, "snapshot", "", limits)
    config = collect_autoskillit_toml(root, "snapshot", "config.toml", limits)
    artifact = collect_artifact(root, "snapshot", "artifact.json", limits)
    generated = collect_generated_artifact(root, "snapshot", "artifact.json", limits)
    coverage = collect_coverage_observation(root, "snapshot", "coverage.json", limits)
    test_map = collect_test_map_observation(root, "snapshot", "test-map.json", limits)
    unavailable = collect_artifact(root, "snapshot", "missing.json", limits)
    unsupported = collect_unsupported(root, "snapshot", "native-lsp", limits)

    assert structural.status is CollectorStatus.SUCCEEDED
    assert {
        record.subject.namespace for record in structural.evidence if record.subject is not None
    } >= {
        "python-alias",
        "python-call",
        "python-dynamic-import",
        "python-import",
        "python-nominal-protocol",
        "python-protocol",
        "python-reexport",
        "python-registry",
        "python-runtime-patch",
        "python-runtime-wiring",
        "python-symbol",
        "python-test-consumer",
    }
    assert registry.status is CollectorStatus.SUCCEEDED
    assert all(
        report.status is CollectorStatus.SUCCEEDED
        for report in (config, artifact, generated, coverage, test_map)
    )
    graph = build_canonical_evidence_graph(
        record
        for report in (structural, registry, config, generated, coverage, test_map)
        for record in report.evidence
    )
    assert {
        RelationshipKind.AFFECTS,
        RelationshipKind.CALLS,
        RelationshipKind.DECLARES,
        RelationshipKind.DEFINES,
        RelationshipKind.IMPORTS,
        RelationshipKind.REFERENCES,
    }.issubset({edge.relationship for edge in graph.edges})
    assert unavailable.status is CollectorStatus.FAILED
    assert unsupported.status is CollectorStatus.UNSUPPORTED
