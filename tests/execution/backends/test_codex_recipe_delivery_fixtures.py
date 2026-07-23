"""Integrity ratchets for protected-host and diagnostic recipe fixtures."""

from __future__ import annotations

import json

import pytest

from tests.fixtures.codex_recipe_diagnostic import (
    DIAGNOSTIC_FIXTURE_COUNT,
    DIAGNOSTIC_FIXTURE_NAMES,
    DIAGNOSTIC_FIXTURE_SCHEMA_VERSION,
    UNSIGNED_TRACE_V1,
    WRITABLE_ROLLOUT_V1,
)
from tests.fixtures.codex_recipe_diagnostic import (
    fixture_path as diagnostic_fixture_path,
)
from tests.fixtures.codex_recipe_protected import (
    PROTECTED_FUNCTIONS_EXEC_V1,
    PROTECTED_HOST_FIXTURE_COUNT,
    PROTECTED_HOST_FIXTURE_NAMES,
    PROTECTED_HOST_FIXTURE_SCHEMA_VERSION,
)
from tests.fixtures.codex_recipe_protected import (
    fixture_path as protected_fixture_path,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _jsonl(name: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in diagnostic_fixture_path(name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_protected_fixture_export_schema_and_count_ratchets() -> None:
    assert PROTECTED_HOST_FIXTURE_SCHEMA_VERSION == 1
    assert PROTECTED_HOST_FIXTURE_COUNT == len(PROTECTED_HOST_FIXTURE_NAMES) == 1
    assert PROTECTED_HOST_FIXTURE_NAMES == (PROTECTED_FUNCTIONS_EXEC_V1,)
    for name in PROTECTED_HOST_FIXTURE_NAMES:
        assert protected_fixture_path(name).is_file()
        record = json.loads(protected_fixture_path(name).read_text(encoding="utf-8"))
        assert record["schema_version"] == PROTECTED_HOST_FIXTURE_SCHEMA_VERSION


def test_diagnostic_fixture_export_schema_and_count_ratchets() -> None:
    assert DIAGNOSTIC_FIXTURE_SCHEMA_VERSION == 1
    assert DIAGNOSTIC_FIXTURE_COUNT == len(DIAGNOSTIC_FIXTURE_NAMES) == 2
    assert DIAGNOSTIC_FIXTURE_NAMES == (WRITABLE_ROLLOUT_V1, UNSIGNED_TRACE_V1)
    for name in DIAGNOSTIC_FIXTURE_NAMES:
        assert diagnostic_fixture_path(name).is_file()
        records = _jsonl(name)
        assert records
        assert all(
            record["schema_version"] == DIAGNOSTIC_FIXTURE_SCHEMA_VERSION for record in records
        )


def test_positive_fixture_is_protected_and_diagnostics_are_never_authority() -> None:
    protected = json.loads(
        protected_fixture_path(PROTECTED_FUNCTIONS_EXEC_V1).read_text(encoding="utf-8")
    )
    assert protected["authenticated"] is True
    assert protected["caller_writable"] is False

    rollout = _jsonl(WRITABLE_ROLLOUT_V1)
    trace = _jsonl(UNSIGNED_TRACE_V1)
    assert any(record.get("caller_writable") is True for record in rollout)
    assert any(record.get("authenticated") is False for record in trace)


def test_fixture_families_are_distinct_from_cli_output_ndjson() -> None:
    assert set(PROTECTED_HOST_FIXTURE_NAMES).isdisjoint(DIAGNOSTIC_FIXTURE_NAMES)
    for name in PROTECTED_HOST_FIXTURE_NAMES:
        assert "codex_recipe_protected" in protected_fixture_path(name).parts
    for name in DIAGNOSTIC_FIXTURE_NAMES:
        assert "codex_recipe_diagnostic" in diagnostic_fixture_path(name).parts
