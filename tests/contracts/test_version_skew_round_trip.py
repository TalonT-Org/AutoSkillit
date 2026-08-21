"""Every persisted format has executable enum- and version-skew coverage.

The domain tests build each real format with its native fixtures. This compact
matrix prevents a ledger entry from shipping without both required behaviors
while avoiding a second set of format constructors in the contract layer.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from autoskillit.core import PERSISTED_FORMAT_LEDGER

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class _SkewCoverage:
    unknown_member_file: str
    unknown_member_test: str
    future_version_file: str
    future_version_test: str


_FORMAT_COVERAGE = {
    "retiring_cache": _SkewCoverage(
        unknown_member_file="tests/contracts/test_launch_path_survives_unsafe_queue.py",
        unknown_member_test="test_unknown_artifact_kind_does_not_condemn_the_sibling_records",
        future_version_file="tests/core/test_plugin_cache.py",
        future_version_test="test_future_schema_is_preserved_and_mutation_is_refused",
    ),
    "fleet_campaign_state": _SkewCoverage(
        unknown_member_file="tests/fleet/test_state_schema.py",
        unknown_member_test="test_unknown_persisted_dispatch_status_retains_original_token",
        future_version_file="tests/fleet/test_state_schema.py",
        future_version_test="test_read_state_returns_none_on_future_version",
    ),
    "capture_lifecycle_ledger": _SkewCoverage(
        unknown_member_file="tests/hooks/test_capture_lifecycle.py",
        unknown_member_test=(
            "test_unknown_lifecycle_enum_frames_survive_incremental_load_and_compaction"
        ),
        future_version_file="tests/hooks/test_capture_lifecycle.py",
        future_version_test="test_future_ledger_format_reports_observed_and_current_versions",
    ),
    "skill_session_contract": _SkewCoverage(
        unknown_member_file="tests/execution/test_skill_session_contract_store.py",
        unknown_member_test=(
            "test_store_quarantines_unknown_exploration_vector_enums_and_preserves_raw_record"
        ),
        future_version_file="tests/execution/test_skill_session_contract_store.py",
        future_version_test="test_store_load_classifies_future_outer_schema",
    ),
}


def _test_functions(relative_path: str) -> frozenset[str]:
    path = _ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return frozenset(node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))


@pytest.mark.parametrize("format_id", sorted(_FORMAT_COVERAGE))
def test_older_reader_survives_a_newer_writers_enum_member(format_id: str) -> None:
    coverage = _FORMAT_COVERAGE[format_id]
    assert coverage.unknown_member_test in _test_functions(coverage.unknown_member_file)


@pytest.mark.parametrize("format_id", sorted(_FORMAT_COVERAGE))
def test_older_reader_reports_unsupported_future_for_a_bumped_version(
    format_id: str,
) -> None:
    coverage = _FORMAT_COVERAGE[format_id]
    assert coverage.future_version_test in _test_functions(coverage.future_version_file)


def test_every_registered_format_has_both_version_skew_contracts() -> None:
    assert set(_FORMAT_COVERAGE) == set(PERSISTED_FORMAT_LEDGER)
