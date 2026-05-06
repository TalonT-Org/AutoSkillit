"""Pipeline token usage tracking for autoskillit.

Accumulates token counts keyed by YAML step name. The get_token_summary MCP
tool retrieves the accumulated data grouped by step.

This module is intentionally simple: a dataclass entry and a dict-backed store
with a defensive copy getter.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from autoskillit.core import get_logger
from autoskillit.pipeline.audit import _iter_session_log_entries

logger = get_logger(__name__)


def canonical_step_name(step_name: str) -> str:
    """Strip trailing '-N' numeric disambiguation suffixes from step names.

    Orchestrators may append clone instance numbers (e.g. 'plan-30') to
    disambiguate parallel runs. For telemetry aggregation, these must collapse
    to the canonical YAML step key ('plan').

    Only a trailing hyphen followed by one or more digits is stripped.
    'open-pr' (ends in non-digit) is preserved unchanged.

    Assumption: YAML step keys never end with a hyphen-digit pattern (e.g.
    'phase-2', 'retry-3'). This contract is enforced by the load_recipe
    docstring, which prohibits orchestrators from appending disambiguation
    suffixes to step_name. A step key that coincidentally ends with -N is
    indistinguishable from an orchestrator-appended suffix and will be
    collapsed to the base name.
    """
    if not step_name or ":" in step_name or step_name.startswith("("):
        return step_name
    return re.sub(r"-\d+$", "", step_name)


def _primary_model(token_usage: dict[str, Any]) -> str:
    """Return the model name with the most total tokens from model_breakdown."""
    mb = token_usage.get("model_breakdown", {})
    if not isinstance(mb, dict) or not mb:
        return ""
    return max(mb, key=lambda m: sum(mb[m].values()) if isinstance(mb[m], dict) else 0)


@dataclass
class TokenEntry:
    """Accumulated token usage for a single YAML step name."""

    step_name: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    invocation_count: int = 0
    elapsed_seconds: float = 0.0
    loc_insertions: int = 0
    loc_deletions: int = 0
    peak_context: int = 0
    turn_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["TokenEntry", "DefaultTokenLog", "canonical_step_name"]


class DefaultTokenLog:
    """In-memory store for per-step token usage.

    Entries are keyed by (order_id, canonical_step_name). An empty string
    order_id represents an unscoped (legacy) entry. This allows per-issue
    isolation when order_id is supplied, while preserving backward-compatible
    aggregation across all orders when order_id is omitted.

    Thread-safety: the MCP server is async (single-threaded event loop),
    so dict operations are safe without locks.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], TokenEntry] = {}

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
    ) -> None:
        """Accumulate token usage for a step.

        No-op if step_name is empty or token_usage is None.
        order_id scopes the entry to a specific issue/order; empty means unscoped.
        """
        if not step_name or not token_usage:
            return
        canonical = canonical_step_name(step_name)
        key = (order_id, canonical)
        if key not in self._entries:
            self._entries[key] = TokenEntry(step_name=canonical)
        e = self._entries[key]
        _model = _primary_model(token_usage)
        if _model and not e.model:
            e.model = _model
        e.input_tokens += token_usage.get("input_tokens", 0)
        e.output_tokens += token_usage.get("output_tokens", 0)
        e.cache_creation_input_tokens += token_usage.get("cache_creation_input_tokens", 0)
        e.cache_read_input_tokens += token_usage.get("cache_read_input_tokens", 0)
        e.invocation_count += 1
        e.loc_insertions += loc_insertions
        e.loc_deletions += loc_deletions
        _peak = token_usage.get("peak_context", 0)
        if isinstance(_peak, int) and _peak > e.peak_context:
            e.peak_context = _peak
        _turns = token_usage.get("turn_count", 0)
        if isinstance(_turns, int):
            e.turn_count += _turns
        if elapsed_seconds is not None:
            e.elapsed_seconds += elapsed_seconds
        elif start_ts and end_ts:
            try:
                delta = (
                    datetime.fromisoformat(end_ts) - datetime.fromisoformat(start_ts)
                ).total_seconds()
                e.elapsed_seconds += max(0.0, delta)
            except ValueError:
                pass
        logger.debug(
            "token_usage_recorded",
            step_name=canonical,
            order_id=order_id,
            invocation_count=e.invocation_count,
        )

    def get_report(self, *, order_id: str = "") -> list[dict[str, Any]]:
        """Return entries as dicts, optionally filtered by order_id.

        If order_id is empty, aggregate ALL entries by step_name across all
        order buckets (backward-compatible behavior). If order_id is non-empty,
        return only entries for that order.
        """
        if order_id:
            return [e.to_dict() for (oid, _step), e in self._entries.items() if oid == order_id]
        # Aggregate all orders by step_name (sum tokens per step)
        aggregated: dict[str, TokenEntry] = {}
        for (_oid, step), e in self._entries.items():
            if step not in aggregated:
                aggregated[step] = TokenEntry(step_name=step)
            agg = aggregated[step]
            if e.model and not agg.model:
                agg.model = e.model
            agg.input_tokens += e.input_tokens
            agg.output_tokens += e.output_tokens
            agg.cache_creation_input_tokens += e.cache_creation_input_tokens
            agg.cache_read_input_tokens += e.cache_read_input_tokens
            agg.elapsed_seconds += e.elapsed_seconds
            agg.invocation_count += e.invocation_count
            agg.loc_insertions += e.loc_insertions
            agg.loc_deletions += e.loc_deletions
            if e.peak_context > agg.peak_context:
                agg.peak_context = e.peak_context
            agg.turn_count += e.turn_count
        return [e.to_dict() for e in aggregated.values()]

    def compute_total(self, *, order_id: str = "") -> dict[str, Any]:
        """Compute aggregate token counts and elapsed time.

        If order_id is empty, sum all entries. If order_id is non-empty, sum
        only entries for that order.
        """
        total: dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "total_elapsed_seconds": 0.0,
            "loc_insertions": 0,
            "loc_deletions": 0,
            "peak_context": 0,
            "turn_count": 0,
        }
        for (oid, _step), entry in self._entries.items():
            if order_id and oid != order_id:
                continue
            total["input_tokens"] += entry.input_tokens
            total["output_tokens"] += entry.output_tokens
            total["cache_creation_input_tokens"] += entry.cache_creation_input_tokens
            total["cache_read_input_tokens"] += entry.cache_read_input_tokens
            total["total_elapsed_seconds"] += entry.elapsed_seconds
            total["loc_insertions"] += entry.loc_insertions
            total["loc_deletions"] += entry.loc_deletions
            if entry.peak_context > total["peak_context"]:
                total["peak_context"] = entry.peak_context
            total["turn_count"] += entry.turn_count
        return total

    def compute_model_totals(self, *, order_id: str = "") -> list[dict[str, Any]]:
        """Compute per-model aggregate token counts across all steps."""
        model_data: dict[str, dict[str, Any]] = {}
        for (oid, _step), entry in self._entries.items():
            if order_id and oid != order_id:
                continue
            model = entry.model or "unknown"
            if model not in model_data:
                model_data[model] = {
                    "model": model,
                    "_steps": set(),
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "elapsed_seconds": 0.0,
                }
            md = model_data[model]
            md["_steps"].add(entry.step_name)
            md["input_tokens"] += entry.input_tokens
            md["output_tokens"] += entry.output_tokens
            md["cache_creation_input_tokens"] += entry.cache_creation_input_tokens
            md["cache_read_input_tokens"] += entry.cache_read_input_tokens
            md["elapsed_seconds"] += entry.elapsed_seconds
        result = []
        for md in model_data.values():
            md["step_count"] = len(md.pop("_steps"))
            result.append(md)
        return result

    def clear(self) -> None:
        """Reset the store."""
        self._entries = {}

    def load_from_log_dir(
        self,
        log_root: Path,
        *,
        since: str = "",
        cwd_filter: str = "",
        kitchen_id_filter: str = "",
        campaign_id_filter: str = "",
        order_id_filter: str = "",
    ) -> int:
        """Reconstruct token entries from persisted session logs.

        Reads the sessions.jsonl index at log_root, filters entries by since
        (ISO timestamp), reads token_usage.json from each matching session
        directory, and accumulates into self._entries (merging by (order_id,
        step_name) with the existing in-memory state).

        cwd_filter: if non-empty, only sessions whose cwd matches are loaded.
        kitchen_id_filter: if non-empty, only sessions whose kitchen_id matches are loaded.
            Falls back to pipeline_id for sessions written before the rename.
        campaign_id_filter: if non-empty, only sessions whose campaign_id matches are loaded.
        order_id_filter: if non-empty, only sessions whose order_id matches are loaded.

        Returns the count of session directories successfully loaded.
        """
        count = 0
        for tu_path in _iter_session_log_entries(
            log_root,
            since,
            "token_usage.json",
            cwd_filter,
            kitchen_id_filter,
            campaign_id_filter,
            order_id_filter,
        ):
            try:
                data = json.loads(tu_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            raw_step = data.get("session_label") or data.get("step_name", "")
            if not raw_step:
                continue

            step_name = canonical_step_name(raw_step)
            entry_order_id = data.get("order_id", "")
            key = (entry_order_id, step_name)
            if key not in self._entries:
                self._entries[key] = TokenEntry(step_name=step_name)
            e = self._entries[key]
            _model = data.get("model_identifier", "")
            if _model and not e.model:
                e.model = _model
            e.input_tokens += data.get("input_tokens", 0)
            e.output_tokens += data.get("output_tokens", 0)
            e.cache_creation_input_tokens += data.get("cache_creation_input_tokens", 0)
            e.cache_read_input_tokens += data.get("cache_read_input_tokens", 0)
            # timing_seconds is the on-disk key name written by session_log.py;
            # elapsed_seconds is the in-memory field name on TokenEntry.
            _raw_timing = data.get("timing_seconds")
            e.elapsed_seconds += float(_raw_timing) if _raw_timing is not None else 0.0
            e.loc_insertions += data.get("loc_insertions", 0)
            e.loc_deletions += data.get("loc_deletions", 0)
            _raw_peak = data.get("peak_context", 0)
            if isinstance(_raw_peak, int) and _raw_peak > e.peak_context:
                e.peak_context = _raw_peak
            _raw_turns = data.get("turn_count", 0)
            if isinstance(_raw_turns, int):
                e.turn_count += _raw_turns
            # Each token_usage.json file represents a single run_skill invocation
            # (one file = one invocation). Incrementing here reconstructs the
            # invocation count that was accumulated live via record().
            e.invocation_count += 1
            count += 1

        return count
