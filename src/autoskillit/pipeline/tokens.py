"""Pipeline token usage tracking with source-pair-aware availability."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import regex as re

from autoskillit.core import CANONICAL_ACCOUNTING_FIELDS, ModelTotalEntry, TokenMeasure, get_logger
from autoskillit.pipeline.audit import _iter_session_log_entries

logger = get_logger(__name__)

_TOKEN_FIELDS = CANONICAL_ACCOUNTING_FIELDS


def canonical_step_name(step_name: str) -> str:
    """Strip the clone-disambiguation suffix from a recipe step name."""
    if not step_name or ":" in step_name or step_name.startswith("("):
        return step_name
    return re.sub(r"-\d+$", "", step_name)


def _measure(raw: object, *, legacy: bool = False) -> TokenMeasure:
    """Decode one live or durable measure through the canonical helper."""
    return TokenMeasure.measure_from_raw(raw, legacy=legacy)


def _combine(left: TokenMeasure, right: TokenMeasure) -> TokenMeasure:
    """Combine evidence conservatively when a partial observation is incompatible."""
    return TokenMeasure.combine_or_unknown(left, right)


def _maximum(left: TokenMeasure, right: TokenMeasure) -> TokenMeasure:
    return TokenMeasure.maximum_or_unknown(left, right)


def _primary_model(token_usage: dict[str, Any]) -> str:
    """Return the model with the greatest observed output-token count."""
    breakdown = token_usage.get("model_breakdown")
    if not isinstance(breakdown, dict):
        return ""
    for model, values in breakdown.items():
        if not isinstance(values, dict):
            logger.warning(
                "Unexpected model_breakdown entry type for %r: %r",
                model,
                type(values).__name__,
            )
    candidates = [
        (model, _measure(values.get("output_tokens")).value)
        for model, values in breakdown.items()
        if isinstance(model, str) and isinstance(values, dict)
    ]
    return (
        max(candidates, key=lambda item: item[1] if item[1] is not None else -1)[0]
        if candidates
        else ""
    )


def _source_pair(token_usage: dict[str, Any], backend: str, provider_used: str) -> tuple[str, str]:
    return (
        backend or str(token_usage.get("backend") or "unknown"),
        provider_used or str(token_usage.get("provider_used") or "unknown"),
    )


@dataclass
class TokenEntry:
    """One homogeneous (step, backend, provider) token aggregation bucket."""

    step_name: str
    backend: str = "unknown"
    provider_used: str = "unknown"
    model: str = ""
    input_tokens: TokenMeasure = field(default_factory=TokenMeasure.unknown)
    output_tokens: TokenMeasure = field(default_factory=TokenMeasure.unknown)
    cache_write_tokens: TokenMeasure = field(default_factory=TokenMeasure.unknown)
    cache_read_tokens: TokenMeasure = field(default_factory=TokenMeasure.unknown)
    invocation_count: int = 0
    elapsed_seconds: float = 0.0
    loc_insertions: int = 0
    loc_deletions: int = 0
    peak_context: TokenMeasure = field(default_factory=TokenMeasure.unknown)
    turn_count: int = 0

    def add(self, token_usage: dict[str, Any], *, legacy: bool = False) -> None:
        for name in _TOKEN_FIELDS:
            value = token_usage.get(name)
            if value is None and name == "cache_write_tokens":
                value = token_usage.get("cache_creation_input_tokens")
            if value is None and name == "cache_read_tokens":
                value = token_usage.get("cache_read_input_tokens")
            parsed = _measure(value, legacy=legacy)
            setattr(
                self,
                name,
                parsed if self.invocation_count == 0 else _combine(getattr(self, name), parsed),
            )
        peak = _measure(token_usage.get("peak_context"), legacy=legacy)
        self.peak_context = (
            peak if self.invocation_count == 0 else _maximum(self.peak_context, peak)
        )

    def merge(self, other: TokenEntry) -> None:
        if (self.backend, self.provider_used) != (other.backend, other.provider_used):
            raise ValueError("Token entries from different source pairs cannot be merged")
        if other.model and not self.model:
            self.model = other.model
        for name in (*_TOKEN_FIELDS, "peak_context"):
            operation = _maximum if name == "peak_context" else _combine
            setattr(self, name, operation(getattr(self, name), getattr(other, name)))
        self.invocation_count += other.invocation_count
        self.elapsed_seconds += other.elapsed_seconds
        self.loc_insertions += other.loc_insertions
        self.loc_deletions += other.loc_deletions
        self.turn_count += other.turn_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "backend": self.backend,
            "provider_used": self.provider_used,
            "model": self.model,
            **{name: getattr(self, name).to_dict() for name in _TOKEN_FIELDS},
            "invocation_count": self.invocation_count,
            "elapsed_seconds": self.elapsed_seconds,
            "loc_insertions": self.loc_insertions,
            "loc_deletions": self.loc_deletions,
            "peak_context": self.peak_context.to_dict(),
            "turn_count": self.turn_count,
        }


__all__ = ["DefaultTokenLog", "TokenEntry", "canonical_step_name"]


class DefaultTokenLog:
    """In-memory token log that never pools different provider observations."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str, str], TokenEntry] = {}

    def record(
        self,
        step_name: str,
        token_usage: dict[str, Any] | None,
        *,
        start_ts: str = "",
        end_ts: str = "",
        elapsed_seconds: float | None = None,
        order_id: str = "",
        loc_insertions: int = 0,
        loc_deletions: int = 0,
        model: str = "",
        backend: str = "",
        provider_used: str = "",
    ) -> None:
        """Record one execution under its complete reporting source pair."""
        if not step_name or not token_usage:
            return
        pair = _source_pair(token_usage, backend, provider_used)
        canonical = canonical_step_name(step_name)
        key = (order_id, canonical, *pair)
        entry = self._entries.setdefault(
            key,
            TokenEntry(step_name=canonical, backend=pair[0], provider_used=pair[1]),
        )
        selected_model = model or _primary_model(token_usage)
        if selected_model and not entry.model:
            entry.model = selected_model
        entry.add(token_usage)
        entry.invocation_count += 1
        entry.loc_insertions += loc_insertions
        entry.loc_deletions += loc_deletions
        turns = token_usage.get("turn_count")
        if isinstance(turns, int) and not isinstance(turns, bool):
            entry.turn_count += turns
        if elapsed_seconds is not None:
            entry.elapsed_seconds += elapsed_seconds
        elif start_ts and end_ts:
            try:
                entry.elapsed_seconds += max(
                    0.0,
                    (
                        datetime.fromisoformat(end_ts) - datetime.fromisoformat(start_ts)
                    ).total_seconds(),
                )
            except ValueError:
                pass

    def _filtered(self, order_id: str) -> list[TokenEntry]:
        return [
            entry
            for (oid, _step, _backend, _provider), entry in self._entries.items()
            if not order_id or oid == order_id
        ]

    def get_report(self, *, order_id: str = "") -> list[dict[str, Any]]:
        """Return per-step rows; unscoped reports retain source-pair partitions."""
        aggregated: dict[tuple[str, str, str], TokenEntry] = {}
        for entry in self._filtered(order_id):
            key = (entry.step_name, entry.backend, entry.provider_used)
            existing = aggregated.get(key)
            if existing is None:
                aggregated[key] = TokenEntry(**entry.__dict__)
            else:
                existing.merge(entry)
        return [entry.to_dict() for entry in aggregated.values()]

    def compute_total(self, *, order_id: str = "") -> list[dict[str, Any]]:
        """Return totals per source pair; no cross-provider grand total exists."""
        totals: dict[tuple[str, str], TokenEntry] = {}
        for entry in self._filtered(order_id):
            key = (entry.backend, entry.provider_used)
            total = totals.get(key)
            if total is None:
                total = TokenEntry(**entry.__dict__)
                total.step_name = ""
                totals[key] = total
            else:
                total.merge(entry)
        return [
            {**total.to_dict(), "total_elapsed_seconds": total.elapsed_seconds}
            for total in totals.values()
        ]

    def compute_model_totals(self, *, order_id: str = "") -> list[ModelTotalEntry]:
        """Aggregate model rows only inside a homogeneous reporting source pair."""
        totals: dict[tuple[str, str, str], TokenEntry] = {}
        for entry in self._filtered(order_id):
            key = (entry.backend, entry.provider_used, entry.model or "unknown")
            current = totals.get(key)
            if current is None:
                current = TokenEntry(**entry.__dict__)
                current.step_name = ""
                current.model = key[2]
                totals[key] = current
            else:
                current.merge(entry)
        model_totals: list[ModelTotalEntry] = []
        for entry in totals.values():
            model_totals.append(
                {
                    "backend": entry.backend,
                    "provider_used": entry.provider_used,
                    "model": entry.model,
                    "step_count": entry.invocation_count,
                    "input_tokens": entry.input_tokens.to_dict(),
                    "output_tokens": entry.output_tokens.to_dict(),
                    "cache_write_tokens": entry.cache_write_tokens.to_dict(),
                    "cache_read_tokens": entry.cache_read_tokens.to_dict(),
                    "elapsed_seconds": entry.elapsed_seconds,
                }
            )
        return model_totals

    def clear(self) -> None:
        self._entries.clear()

    def load_from_log_dir(
        self,
        log_root: Path,
        *,
        since: str = "",
        cwd_filter: str = "",
        kitchen_id_filter: str = "",
        campaign_id_filter: str = "",
        order_id_filter: str = "",
        dispatch_id_filter: str = "",
    ) -> int:
        """Load token artifacts, decoding legacy zero values conservatively."""
        count = 0
        for path in _iter_session_log_entries(
            log_root,
            since,
            "token_usage.json",
            cwd_filter,
            kitchen_id_filter,
            campaign_id_filter,
            order_id_filter,
            dispatch_id_filter,
        ):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.debug("token_load_skip_unreadable", path=str(path), exc_info=True)
                continue
            raw_step = data.get("session_label") or data.get("step_name")
            if not isinstance(raw_step, str) or not raw_step:
                continue
            backend, provider_used = _source_pair(data, "", "")
            key = (data.get("order_id", ""), canonical_step_name(raw_step), backend, provider_used)
            entry = self._entries.setdefault(
                key,
                TokenEntry(step_name=key[1], backend=backend, provider_used=provider_used),
            )
            model = data.get("model_identifier") or data.get("configured_model")
            if isinstance(model, str) and model and not entry.model:
                entry.model = model
            entry.add(data, legacy=data.get("schema_version", 1) < 4)
            entry.invocation_count += 1
            entry.elapsed_seconds += float(data.get("timing_seconds") or 0.0)
            entry.loc_insertions += int(data.get("loc_insertions") or 0)
            entry.loc_deletions += int(data.get("loc_deletions") or 0)
            turns = data.get("turn_count")
            if isinstance(turns, int) and not isinstance(turns, bool):
                entry.turn_count += turns
            count += 1
        return count

    def check_step_completeness(
        self, expected_steps: Sequence[str], *, order_id: str = ""
    ) -> list[str]:
        loaded = {
            step
            for oid, step, _backend, _provider in self._entries
            if not order_id or oid == order_id
        }
        return sorted({canonical_step_name(step) for step in expected_steps if step} - loaded)
