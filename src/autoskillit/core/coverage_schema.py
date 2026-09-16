"""Wire-format reason strings for the test-source-map artifact's ``unobservable_sources`` entries.

Centralized here so the producer (``scripts/compare-coverage-ast.py``) and any
consumer that needs to classify or assert on these strings imports the same
canonical values without depending on the script's internal globals.

Wire format (test-source-map.json, schema_version >= 2):
    ``{"path": ..., "reason": "not_measured" | "attributed_only_by_fixture"}``
"""

from __future__ import annotations

REASON_NOT_MEASURED: str = "not_measured"
REASON_ATTRIBUTED_ONLY_BY_FIXTURE: str = "attributed_only_by_fixture"

__all__ = ["REASON_NOT_MEASURED", "REASON_ATTRIBUTED_ONLY_BY_FIXTURE"]
