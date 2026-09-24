"""Lock report-row field names to the manually managed schema version."""

from __future__ import annotations

import hashlib
import json

import pytest

from autoskillit.execution._report_index_rows import (
    _ROW_FIELDS,
    REPORT_INDEX_SCHEMA_VERSION,
    REPORT_ROW_TYPES,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_EXPECTED = (1, "8418c2eb676180f7865ce20982478b76f175e0d7d3527f5353f71e4a26117d06")
# Locked snapshot of per-kind field name orderings that produced ``_EXPECTED``.
# Bump ``_EXPECTED[0]`` and refresh this dict in lockstep when row fields change;
# ``test_report_index_schema_version_matches_field_digest`` is what fails first
# when a contributor forgets one of those two updates, and the field diff in
# its failure message uses this snapshot to name the new or removed fields.
_EXPECTED_FIELDS: dict[str, list[str]] = {
    "request": [
        "agent_name",
        "cache_read_tokens",
        "cache_write_tokens",
        "cost_usd",
        "duration_ms",
        "event_sequence",
        "harness",
        "input_tokens",
        "key",
        "kind",
        "model",
        "output_tokens",
        "query_source",
        "request_id",
        "schema_version",
        "session_id",
        "time_ms",
    ],
    "session": [
        "adjudication_reason",
        "adjudication_subtype",
        "assistant_turn_count",
        "cache_read_tokens",
        "cache_write_tokens",
        "caller_session_id",
        "campaign_id",
        "dispatch_id",
        "duration_seconds",
        "harness",
        "input_tokens",
        "key",
        "kind",
        "kitchen_id",
        "level",
        "model",
        "order_id",
        "output_tokens",
        "parent_session_id",
        "provider",
        "recipe",
        "schema_version",
        "session_id",
        "skill",
        "step",
        "subtype",
        "success",
        "time_ms",
        "tool_counts",
    ],
    "subagent": [
        "agent_type",
        "event_sequence",
        "final_model",
        "harness",
        "key",
        "kind",
        "model",
        "model_swapped",
        "schema_version",
        "session_id",
        "time_ms",
    ],
    "tool": [
        "agent_name",
        "duration_ms",
        "error_type",
        "event_sequence",
        "harness",
        "key",
        "kind",
        "schema_version",
        "session_id",
        "success",
        "time_ms",
        "tool_input_size_bytes",
        "tool_name",
        "tool_result_size_bytes",
        "tool_use_id",
    ],
}


def _field_diff(expected: dict[str, list[str]], actual: dict[str, list[str]]) -> list[str]:
    """Return human-readable lines describing per-kind field-set differences."""
    lines: list[str] = []
    kinds = sorted(set(expected) | set(actual))
    for kind in kinds:
        expected_fields = set(expected.get(kind, []))
        actual_fields = set(actual.get(kind, []))
        added = sorted(actual_fields - expected_fields)
        removed = sorted(expected_fields - actual_fields)
        if added:
            lines.append(f"  + {kind}: added {added}")
        if removed:
            lines.append(f"  - {kind}: removed {removed}")
    return lines


def test_report_index_schema_version_matches_field_digest() -> None:
    """Field names are locked; type or meaning changes still require a manual version bump."""
    actual_fields = {
        kind: sorted(row_type.__annotations__) for kind, row_type in REPORT_ROW_TYPES.items()
    }
    digest = hashlib.sha256(json.dumps(actual_fields, sort_keys=True).encode()).hexdigest()
    actual = (REPORT_INDEX_SCHEMA_VERSION, digest)

    if actual != _EXPECTED:
        pytest.fail(
            "Report row fields changed without a coordinated schema-version bump. "
            "Update REPORT_INDEX_SCHEMA_VERSION in src/autoskillit/execution/"
            "_report_index_rows.py and refresh _EXPECTED and _EXPECTED_FIELDS in "
            "this test. Field diff (relative to the locked snapshot):\n"
            + "\n".join(_field_diff(_EXPECTED_FIELDS, actual_fields))
            + f"\nexpected={_EXPECTED} actual={actual}"
        )


def test_row_decoders_cover_exactly_the_declared_fields() -> None:
    for kind, row_type in REPORT_ROW_TYPES.items():
        assert set(_ROW_FIELDS[kind]) == set(row_type.__annotations__) - {"kind", "key"}
