"""Canonical issue-URL extraction from recipe ingredients.

Single accessor for the dual-key ``issue_urls`` (plural, CSV) and ``issue_url``
(singular) ingredient forms. Plural wins when both are present — it is the
canonical recipe-side key for batch recipes (``bem-wrapper``, ``implement-findings``);
singular is the fallback for single-issue recipes (``implementation``,
``remediation``, ``research-*``).

Centralizing the lookup at a single function prevents the singular/plural
mismatch that orphaned labels for the 7th time (issue #4112).
"""

from __future__ import annotations


def extract_issue_urls(ingredients: dict[str, str] | None) -> str:
    """Return the issue-URL CSV from a recipe ingredients dict.

    Tries ``issue_urls`` first (plural, batch recipes), then falls back to
    ``issue_url`` (singular, single-issue recipes). Returns ``""`` if neither
    key is present, both values are empty strings, or the ingredients dict is
    ``None``/empty.
    """
    if not ingredients:
        return ""
    raw = ingredients.get("issue_urls", "")
    if not raw:
        raw = ingredients.get("issue_url", "")
    return raw
