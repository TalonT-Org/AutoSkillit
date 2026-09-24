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


def test_report_index_schema_version_matches_field_digest() -> None:
    """Field names are locked; type or meaning changes still require a manual version bump."""
    digest = hashlib.sha256(
        json.dumps(
            {
                kind: sorted(row_type.__annotations__)
                for kind, row_type in REPORT_ROW_TYPES.items()
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    actual = (REPORT_INDEX_SCHEMA_VERSION, digest)

    assert actual == _EXPECTED, (
        "Report row fields changed without a coordinated schema-version bump: "
        f"expected {_EXPECTED}, got {actual}"
    )


def test_row_decoders_cover_exactly_the_declared_fields() -> None:
    for kind, row_type in REPORT_ROW_TYPES.items():
        assert set(_ROW_FIELDS[kind]) == set(row_type.__annotations__) - {"kind", "key"}
