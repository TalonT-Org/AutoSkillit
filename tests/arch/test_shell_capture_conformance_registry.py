"""Always-run conformance registry, ADR, and native-version contracts."""

import re
from pathlib import Path

import pytest

from tests.hooks._shell_conformance_matrix import (
    CONFORMANCE_CASES,
    CONFORMANCE_INVARIANTS,
    NATIVE_BASELINE_CODEX_VERSION,
    ConformanceExpectation,
    ConformanceMode,
)

pytestmark = pytest.mark.medium


def test_conformance_matrix_is_total() -> None:
    assert {case.invariant for case in CONFORMANCE_CASES} == set(CONFORMANCE_INVARIANTS)
    assert all(case.invariant in CONFORMANCE_INVARIANTS for case in CONFORMANCE_CASES)
    assert all(set(case.expect) == set(ConformanceMode) for case in CONFORMANCE_CASES)
    case_ids = [case.id for case in CONFORMANCE_CASES]
    assert len(case_ids) == len(set(case_ids))
    assert {
        expectation for case in CONFORMANCE_CASES for expectation in case.expect.values()
    } == set(ConformanceExpectation)


def test_adr_0008_conformance_matrix_matches_registry() -> None:
    project_root = Path(__file__).resolve().parents[2]
    adr = (project_root / "docs/decisions/0008-shell-capture-snapshot-authority.md").read_text()
    section = re.search(
        r"(?ms)^### Execution conformance matrix\s*\n(.*?)(?=^#{1,3}\s|\Z)",
        adr,
    )
    assert section is not None
    table_rows = [line for line in section.group(1).splitlines() if line.lstrip().startswith("|")]
    header = next(
        (
            index
            for index, row in enumerate(table_rows)
            if row.strip() == "| Invariant | Baseline | Statement |"
        ),
        None,
    )
    assert header is not None

    documented: dict[str, tuple[str, str]] = {}
    for row in table_rows[header + 1 :]:
        columns = [column.strip() for column in row.strip().strip("|").split("|")]
        if len(columns) != 3 or all(set(column) <= {"-", ":", " "} for column in columns):
            continue
        invariant_id = columns[0].strip("`")
        documented[invariant_id] = (columns[1].strip("`"), columns[2])

    registered = {
        invariant.id: (invariant.baseline.value, invariant.statement)
        for invariant in CONFORMANCE_INVARIANTS.values()
    }
    assert documented == registered


def test_native_baseline_matches_verification_image() -> None:
    project_root = Path(__file__).resolve().parents[2]
    dockerfile = (project_root / "scripts/docker/Dockerfile").read_text()
    version = re.search(r"(?m)^ARG CODEX_VERSION=(\S+)\s*$", dockerfile)
    assert version is not None
    assert version.group(1) == NATIVE_BASELINE_CODEX_VERSION
