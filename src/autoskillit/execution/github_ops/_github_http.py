"""Shared GitHub HTTP response classification primitives."""

from __future__ import annotations

import json
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from typing import Any


def github_error_message(data: Any) -> str | None:
    """Return GitHub's structured top-level message when present."""

    if not isinstance(data, Mapping):
        return None
    message = data.get("message")
    return message if isinstance(message, str) and message else None


def github_error_detail(data: Any) -> str | None:
    """Return GitHub's message plus bounded string or object ``errors[]`` details."""
    message = github_error_message(data)
    errors = data.get("errors") if isinstance(data, Mapping) else None
    if not isinstance(errors, list) or not errors:
        return message[:500] if message else None
    items = "; ".join(
        item if isinstance(item, str) else json.dumps(item, sort_keys=True) for item in errors
    )
    return (f"{message}: {items}" if message else items)[:500]


def is_secondary_rate_limit(
    *,
    status_code: int,
    data: Any,
    headers: Mapping[str, str],
) -> bool:
    """Classify only definitive GitHub secondary-limit responses."""

    if status_code == 429:
        return True
    if status_code != 403:
        return False
    message = (github_error_message(data) or "").casefold()
    return "secondary rate limit" in message or "abuse detection" in message


def retry_after_seconds(
    headers: Mapping[str, str],
    *,
    wall_time: float,
    conservative_default: float = 60.0,
) -> float:
    """Resolve Retry-After/reset evidence with a conservative fallback."""

    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            try:
                deadline = parsedate_to_datetime(retry_after).timestamp()
            except (TypeError, ValueError, OverflowError):
                pass
            else:
                return max(1.0, deadline - wall_time)
    reset = headers.get("x-ratelimit-reset")
    if reset:
        try:
            return max(1.0, float(reset) - wall_time)
        except ValueError:
            pass
    return conservative_default
