"""Ownership and compatibility contracts for result record types."""

from __future__ import annotations

from typing import get_args

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

MOVED_NAMES = (
    "CapturedStream",
    "SpilledOutput",
    "FailureRecord",
    "CleanupResult",
    "CloneSuccessResult",
    "CloneGateUncommitted",
    "CloneGateUnpublished",
    "CloneResult",
    "ModelTotalEntry",
    "TokenUsageFileEntry",
    "SessionIndexEntry",
)

RUNTIME_TYPE_NAMES = tuple(name for name in MOVED_NAMES if name != "CloneResult")
ROOT_PUBLIC_NAMES = MOVED_NAMES[:9]
INTERNAL_INDEX_NAMES = MOVED_NAMES[9:]


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


@pytest.mark.parametrize("name", ROOT_PUBLIC_NAMES)
def test_existing_root_exports_preserve_identity(name: str) -> None:
    import autoskillit.core as core
    from autoskillit.core.types import _type_results_records as records

    assert getattr(core, name) is getattr(records, name)


@pytest.mark.parametrize("name", INTERNAL_INDEX_NAMES)
def test_internal_index_types_remain_absent_from_root_gateway(name: str) -> None:
    import autoskillit.core as core

    assert not hasattr(core, name)
