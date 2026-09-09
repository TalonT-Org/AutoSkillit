"""Quota-aware check for long-running pipeline recipes.

IL-1 package facade. The poll-based gate (check_and_sleep_if_needed and its
supporting types/helpers) lives in _quota_gate.py; observed-rate-limit
evidence persistence (record_observed_rate_limit,
record_skill_result_rate_limit) lives in _quota_observed.py — see each
sibling's own docstring. Does NOT sleep. Returns metadata; the orchestrator
sleeps via run_cmd.
"""

from __future__ import annotations

from autoskillit.execution.quota._quota_gate import (
    KNOWN_QUOTA_WINDOW_NAMES,
    LONG_WINDOW_NAMES,
    QUOTA_CACHE_SCHEMA_VERSION,
    QuotaFetchResult,
    QuotaStatus,
    QuotaWindowEntry,
    _compute_binding,  # noqa: F401 — re-export for callers/tests
    _fetch_quota,  # noqa: F401 — re-export for callers/tests
    _is_long_window,  # noqa: F401 — re-export for callers/tests
    _parse_resets_at,  # noqa: F401 — re-export for callers/tests
    _read_cache,  # noqa: F401 — re-export for callers/tests
    _read_credentials,  # noqa: F401 — re-export for callers/tests
    _refresh_quota_cache,  # noqa: F401 — re-export for callers/tests
    _threshold_for_window,  # noqa: F401 — re-export for callers/tests
    _write_cache,  # noqa: F401 — re-export for callers/tests
    check_and_sleep_if_needed,
    invalidate_cache,
    logger,  # noqa: F401 — re-export for callers/tests
)

__all__ = [
    "KNOWN_QUOTA_WINDOW_NAMES",
    "LONG_WINDOW_NAMES",
    "QUOTA_CACHE_SCHEMA_VERSION",
    "QuotaFetchResult",
    "QuotaStatus",
    "QuotaWindowEntry",
    "check_and_sleep_if_needed",
    "invalidate_cache",
]
