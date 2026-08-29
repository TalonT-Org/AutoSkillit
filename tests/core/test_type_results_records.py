"""Ownership and compatibility contracts for result record types."""

from __future__ import annotations

from typing import get_args

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

ROOT_PUBLIC_NAMES = (
    "CapturedStream",
    "SpilledOutput",
    "SpillSpec",
    "FailureRecord",
    "CleanupResult",
    "CloneSuccessResult",
    "CloneGateUncommitted",
    "CloneGateUnpublished",
    "CloneResult",
    "ModelTotalEntry",
    "SESSION_INDEX_SCHEMA_VERSION",
)
INTERNAL_INDEX_NAMES = (
    "TokenUsageFileEntry",
    "SessionIndexEntry",
)
MOVED_NAMES = ROOT_PUBLIC_NAMES + INTERNAL_INDEX_NAMES

RUNTIME_TYPE_NAMES = tuple(
    name for name in MOVED_NAMES if name not in {"CloneResult", "SESSION_INDEX_SCHEMA_VERSION"}
)


def test_record_shard_owns_exact_public_surface() -> None:
    from autoskillit.core.types import _type_results_records as records

    assert tuple(records.__all__) == MOVED_NAMES
    assert len(records.__all__) == len(set(records.__all__))


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_legacy_facade_and_types_hub_preserve_identity(name: str) -> None:
    import autoskillit.core.types as types_hub
    from autoskillit.core.types import _type_results as facade
    from autoskillit.core.types import _type_results_records as records

    canonical = getattr(records, name)
    assert getattr(facade, name) is canonical
    assert facade.__all__.count(name) == 1
    assert getattr(types_hub, name) is canonical
    assert types_hub.__all__.count(name) == 1


@pytest.mark.parametrize("name", RUNTIME_TYPE_NAMES)
def test_runtime_types_are_defined_by_record_shard(name: str) -> None:
    from autoskillit.core.types import _type_results_records as records

    assert getattr(records, name).__module__ == "autoskillit.core.types._type_results_records"


def test_clone_result_is_composed_from_shard_owned_types() -> None:
    from autoskillit.core.types import _type_results_records as records

    assert get_args(records.CloneResult) == (
        records.CloneSuccessResult,
        records.CloneGateUncommitted,
        records.CloneGateUnpublished,
    )


def test_root_public_and_internal_index_names_partition_moved_names() -> None:
    """ROOT_PUBLIC_NAMES and INTERNAL_INDEX_NAMES are explicit, non-overlapping, exhaustive."""
    partition = set(ROOT_PUBLIC_NAMES) | set(INTERNAL_INDEX_NAMES)
    assert partition == set(MOVED_NAMES)
    assert set(ROOT_PUBLIC_NAMES).isdisjoint(set(INTERNAL_INDEX_NAMES))
    assert len(MOVED_NAMES) == len(set(MOVED_NAMES))


@pytest.mark.parametrize("name", ROOT_PUBLIC_NAMES)
def test_existing_root_exports_preserve_identity(name: str) -> None:
    import autoskillit.core as core
    from autoskillit.core.types import _type_results_records as records

    assert getattr(core, name) is getattr(records, name)


@pytest.mark.parametrize("name", INTERNAL_INDEX_NAMES)
def test_internal_index_types_remain_absent_from_root_gateway(name: str) -> None:
    import autoskillit.core as core

    assert not hasattr(core, name)
