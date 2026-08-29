"""Contract tests for the shared backend-admission ledger."""

from __future__ import annotations

import pytest

from autoskillit.execution.backends import BACKEND_REGISTRY
from tests.contracts import _skill_admission_ledger as admission_ledger

pytestmark = pytest.mark.medium

_COMBINATION_IDS = tuple(
    f"{role.value}-cook-{str(cook_session).lower()}"
    for role, cook_session in admission_ledger.PINNED_COMBINATIONS
)


@pytest.mark.parametrize(
    "combination",
    admission_ledger.PINNED_COMBINATIONS,
    ids=_COMBINATION_IDS,
)
def test_no_silent_admission_additions(
    combination: admission_ledger.CatalogCombination,
) -> None:
    live = admission_ledger._live_admission_rows(combination)
    golden = admission_ledger.SKILL_ADMISSION_LEDGER[combination]
    additions = {
        skill_name: live[skill_name] for skill_name in sorted(live.keys() - golden.keys())
    }
    assert not additions, f"skills missing from SKILL_ADMISSION_LEDGER: {additions!r}"


@pytest.mark.parametrize(
    "combination",
    admission_ledger.PINNED_COMBINATIONS,
    ids=_COMBINATION_IDS,
)
def test_no_silent_admission_removals(
    combination: admission_ledger.CatalogCombination,
) -> None:
    live = admission_ledger._live_admission_rows(combination)
    golden = admission_ledger.SKILL_ADMISSION_LEDGER[combination]
    removals = sorted(golden.keys() - live.keys())
    assert not removals, f"stale skills in SKILL_ADMISSION_LEDGER: {removals!r}"


@pytest.mark.parametrize(
    "combination",
    admission_ledger.PINNED_COMBINATIONS,
    ids=_COMBINATION_IDS,
)
def test_no_silent_status_changes(
    combination: admission_ledger.CatalogCombination,
) -> None:
    live = admission_ledger._live_admission_rows(combination)
    golden = admission_ledger.SKILL_ADMISSION_LEDGER[combination]
    changes = sorted(
        (
            skill_name,
            backend_name,
            golden[skill_name][backend_name],
            live[skill_name][backend_name],
        )
        for skill_name in live.keys() & golden.keys()
        for backend_name in live[skill_name].keys() & golden[skill_name].keys()
        if live[skill_name][backend_name] != golden[skill_name][backend_name]
    )
    assert not changes, (
        "backend admission status changed without a matching "
        f"SKILL_ADMISSION_LEDGER edit: {changes!r}"
    )


def test_ledger_dimensions_match_registry_and_pinned_combinations() -> None:
    assert tuple(admission_ledger.SKILL_ADMISSION_LEDGER) == (admission_ledger.PINNED_COMBINATIONS)
    expected_backends = tuple(sorted(BACKEND_REGISTRY))
    for rows in admission_ledger.SKILL_ADMISSION_LEDGER.values():
        for backend_statuses in rows.values():
            assert tuple(backend_statuses) == expected_backends


@pytest.mark.parametrize("combination", _COMBINATION_IDS, ids=_COMBINATION_IDS)
def test_managed_codex_admission_rows_are_complete_and_join_refusal_free(
    combination: admission_ledger.CatalogCombination,
) -> None:
    rows = admission_ledger._live_admission_rows(combination)

    # Length is derived from the golden ledger so this test does not rot
    # when skills are added or removed.
    assert len(rows) == len(admission_ledger.SKILL_ADMISSION_LEDGER[combination])
    assert all(statuses["codex"] == "admitted" for statuses in rows.values())


def test_ledger_is_sorted() -> None:
    assert tuple(admission_ledger.SKILL_ADMISSION_LEDGER) == (admission_ledger.PINNED_COMBINATIONS)
    for rows in admission_ledger.SKILL_ADMISSION_LEDGER.values():
        assert tuple(rows) == tuple(sorted(rows))
        for backend_statuses in rows.values():
            assert tuple(backend_statuses) == tuple(sorted(backend_statuses))
