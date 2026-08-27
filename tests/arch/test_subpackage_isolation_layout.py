"""Structural contracts for the subpackage-isolation guard layout."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests.arch import test_subpackage_isolation as facade

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

ROOT = Path(__file__).resolve().parents[2]
ARCH = ROOT / "tests" / "arch"
FACADE = ARCH / "test_subpackage_isolation.py"
SHARD_CEILING = 650
EXPECTED_RESPONSIBILITY_SHARDS = (
    "test_subpackage_isolation_singleton_io.py",
    "test_subpackage_isolation_topology.py",
    "test_subpackage_isolation_file_counts.py",
    "test_subpackage_isolation_size.py",
    "test_subpackage_isolation_module_boundaries.py",
    "test_subpackage_isolation_tool_context.py",
    "test_subpackage_isolation_migration_process.py",
    "test_subpackage_isolation_facades.py",
    "test_subpackage_isolation_capture_layout.py",
    "test_subpackage_isolation_smoke_review.py",
)


def _family_paths() -> list[Path]:
    return sorted(
        {
            *ARCH.glob("test_subpackage_isolation_*.py"),
            *ARCH.glob("_subpackage_isolation_*.py"),
        }
    )


def _module_has_arch_small_markers(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assignment = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "pytestmark"
                for target in node.targets
            )
        ),
        None,
    )
    if not isinstance(assignment, ast.Assign) or not isinstance(
        assignment.value, (ast.List, ast.Tuple)
    ):
        return False

    found_arch = False
    found_small = False
    for marker in assignment.value.elts:
        if not isinstance(marker, ast.Call) or not isinstance(marker.func, ast.Attribute):
            continue
        if marker.func.attr == "small":
            found_small = True
        if (
            marker.func.attr == "layer"
            and len(marker.args) == 1
            and isinstance(marker.args[0], ast.Constant)
            and marker.args[0].value == "arch"
        ):
            found_arch = True
    return found_arch and found_small


def test_facade_keeps_registration_and_line_limit_authority() -> None:
    from tests.arch import _subpackage_isolation_line_limits as line_limit_owner

    assert "REQ-ARCH-002" in facade.ISOLATION_RULES
    assert facade._LINE_LIMIT_EXEMPTIONS is line_limit_owner._LINE_LIMIT_EXEMPTIONS

    facade_tree = ast.parse(FACADE.read_text(encoding="utf-8"), filename=str(FACADE))
    behavioral_nodes = [
        node
        for node in facade_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name.startswith("test_")
    ]
    assert not behavioral_nodes
    assert len(FACADE.read_text(encoding="utf-8").splitlines()) <= 100


def test_responsibility_shards_are_collected_and_stay_small() -> None:
    missing = [name for name in EXPECTED_RESPONSIBILITY_SHARDS if not (ARCH / name).is_file()]
    assert not missing

    family_paths = _family_paths()
    oversized = [
        path.name
        for path in family_paths
        if len(path.read_text(encoding="utf-8").splitlines()) > SHARD_CEILING
    ]
    assert not oversized

    collected_shards = sorted(ARCH.glob("test_subpackage_isolation_*.py"))
    missing_markers = [
        path.name for path in collected_shards if not _module_has_arch_small_markers(path)
    ]
    assert not missing_markers


def test_file_count_policies_are_disjoint_and_composed_without_copying() -> None:
    from tests.arch._subpackage_isolation_file_counts_authoring import (
        AUTHORING_FILE_COUNT_LIMITS,
    )
    from tests.arch._subpackage_isolation_file_counts_foundation import (
        FOUNDATION_FILE_COUNT_LIMITS,
    )
    from tests.arch._subpackage_isolation_file_counts_runtime import (
        RUNTIME_FILE_COUNT_LIMITS,
    )
    from tests.arch._subpackage_isolation_file_counts_tooling import (
        TOOLING_FILE_COUNT_LIMITS,
    )
    from tests.arch.test_subpackage_isolation_file_counts import FILE_COUNT_LIMITS

    policies = (
        FOUNDATION_FILE_COUNT_LIMITS,
        AUTHORING_FILE_COUNT_LIMITS,
        RUNTIME_FILE_COUNT_LIMITS,
        TOOLING_FILE_COUNT_LIMITS,
    )
    key_sets = [set(policy) for policy in policies]
    for index, keys in enumerate(key_sets):
        for other_keys in key_sets[index + 1 :]:
            assert keys.isdisjoint(other_keys)

    all_keys = set().union(*key_sets)
    assert len(all_keys) == 23
    assert set(FILE_COUNT_LIMITS) == all_keys
    assert len(FILE_COUNT_LIMITS) == len(all_keys)
    assert all(sum(key in policy for policy in policies) == 1 for key in FILE_COUNT_LIMITS)

    scanner = (ARCH / "test_subpackage_isolation_file_counts.py").read_text(encoding="utf-8")
    assert "for sub_dir in sorted(SRC_ROOT.iterdir()):" in scanner
    assert "FILE_COUNT_LIMITS.get(rel_key, 10)" in scanner


@pytest.mark.xfail(
    strict=True,
    reason="coverage source map is regenerated after isolation guards move (#4886)",
)
def test_source_map_records_moved_behavioral_successors() -> None:
    data = json.loads((ROOT / ".autoskillit" / "test-source-map.json").read_text(encoding="utf-8"))
    source_map = data["map"]
    facade_path = "tests/arch/test_subpackage_isolation.py"
    facade_shard = "tests/arch/test_subpackage_isolation_facades.py"
    cli_sources = (
        "src/autoskillit/cli/install/__init__.py",
        "src/autoskillit/cli/install/_marketplace.py",
    )
    source_keys = (*cli_sources, "src/autoskillit/core/logging.py")

    for source_key in source_keys:
        assert facade_path not in source_map[source_key]
    for source_key in cli_sources:
        assert facade_shard in source_map[source_key]

    logging_successors = [
        path
        for path in source_map["src/autoskillit/core/logging.py"]
        if path.startswith("tests/arch/test_subpackage_isolation_")
    ]
    assert logging_successors
    assert all((ROOT / path).is_file() for path in logging_successors)
