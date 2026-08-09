"""Lossless, shape-preserving enforcement for MCP handler responses."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    atomic_write,
    get_logger,
)

if TYPE_CHECKING:
    from autoskillit.config import OutputBudgetConfig

logger = get_logger(__name__)

RESPONSE_SPILL_METADATA_KEY = "_autoskillit_response_spill"
RESPONSE_SPILL_SCHEMA_VERSION = 1
RESPONSE_SPILL_METADATA_KEYS = frozenset(
    {
        "schema_version",
        "artifact_path",
        "sha256",
        "original_utf8_bytes",
        "projected_utf8_bytes",
        "omitted_chars",
        "omitted_items",
        "reason",
    }
)
RESPONSE_SPILL_REASONS = frozenset(
    {"oversized_values", "minimal_projection", "plain_text", "delivery_bound"}
)
RESPONSE_SPILL_SCHEMA_DIGEST = hashlib.sha256(
    json.dumps(
        {
            "metadata_key": RESPONSE_SPILL_METADATA_KEY,
            "metadata_keys": sorted(RESPONSE_SPILL_METADATA_KEYS),
            "reasons": sorted(RESPONSE_SPILL_REASONS),
            "schema_version": RESPONSE_SPILL_SCHEMA_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()

RESPONSE_BUDGET_FAILURE_CAUSES = frozenset(
    {
        "context_unavailable",
        "artifact_publication_failed",
        "serialization_failed",
        "projection_nonconvergent",
        "irreducible_shape",
        "schema_nonconforming",
        "exemption_ceiling_exceeded",
        "internal_invariant_failed",
    }
)


class _ProjectionNonconvergentError(RuntimeError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _estimated_tokens(original_size: int) -> int:
    """Estimate tokens via the four-UTF-8-byte ceiling-division heuristic.

    Uses the general output token limit as a coarse
    transport-layer estimate, not a tokenizer count. Used to compare
    payload size against ``selected_result_token_limit``.
    """
    return (original_size + 3) // 4


def _serialized(value: Any) -> str:
    if isinstance(value, str):
        return value
    return _canonical_json(value)


def _bounded_tool_name(tool_name: str) -> str:
    return tool_name.encode("ascii", "replace").decode("ascii")[:64]


def _emit_response_budget_event(event: str, **payload: Any) -> None:
    with suppress(Exception):
        logger.info(event, **payload)


def emit_response_budget_failure(tool_name: str, cause: str, original_utf8_bytes: int) -> None:
    if cause not in RESPONSE_BUDGET_FAILURE_CAUSES:
        cause = "internal_invariant_failed"
    _emit_response_budget_event(
        "response_budget_failure",
        tool_name=_bounded_tool_name(tool_name),
        cause=cause,
        original_utf8_bytes=original_utf8_bytes,
    )


def _preview_string(value: str, limit: int) -> tuple[str, int]:
    if len(value) <= limit:
        return value, 0
    if limit <= 16:
        return value[:limit], len(value) - limit
    marker = "…[omitted]…"
    available = max(0, limit - len(marker))
    head = available // 2
    tail = available - head
    return value[:head] + marker + (value[-tail:] if tail else ""), len(value) - available


def _project_value(value: Any, limit: int) -> tuple[Any, int, int]:
    """Return a bounded same-category value and aggregate omission counts."""
    if isinstance(value, str):
        projected, omitted = _preview_string(value, limit)
        return projected, omitted, 0
    if value is None or isinstance(value, (bool, int, float)):
        return value, 0, 0
    if isinstance(value, list):
        if not value:
            return [], 0, 0
        item_limit = max(16, limit // min(len(value), 4))
        candidates = value if len(value) <= 4 else [value[0], value[1], value[-2], value[-1]]
        projected_items: list[Any] = []
        omitted_chars = 0
        omitted_items = max(0, len(value) - len(candidates))
        for item in candidates:
            projected, chars, items = _project_value(item, item_limit)
            projected_items.append(projected)
            omitted_chars += chars
            omitted_items += items
        return projected_items, omitted_chars, omitted_items
    if isinstance(value, dict):
        if not value:
            return {}, 0, 0
        item_limit = max(16, limit // max(1, min(len(value), 8)))
        projected_dict: dict[str, Any] = {}
        omitted_chars = 0
        omitted_items = 0
        for key, item in value.items():
            projected, chars, items = _project_value(item, item_limit)
            projected_dict[str(key)] = projected
            omitted_chars += chars
            omitted_items += items
        return projected_dict, omitted_chars, omitted_items
    text, omitted = _preview_string(str(value), limit)
    return text, omitted, 0


def _minimal_same_type(value: Any) -> Any:
    if isinstance(value, str):
        return ""
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return ""


def _bounded_failure(
    *,
    reason: str,
    tool_name: str,
    max_bytes: int,
    artifact_path: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "success": False,
        "error": reason,
        "tool_name": tool_name,
    }
    if artifact_path is not None:
        payload["artifact_path"] = artifact_path
    candidates = (
        _canonical_json(payload),
        '{"success":false,"error":"response_budget_failure"}',
        '{"success":false}',
        "{}",
        "",
    )
    return next(
        candidate for candidate in candidates if len(candidate.encode("utf-8")) <= max_bytes
    )


def bounded_response_budget_failure(
    result: Any,
    *,
    cause: str,
    tool_name: str,
    max_bytes: int,
    original_utf8_bytes: int,
    artifact_path: str | None = None,
) -> Any:
    emit_response_budget_failure(tool_name, cause, original_utf8_bytes)
    rendered = _bounded_failure(
        reason=f"response_budget_{cause}",
        tool_name=_bounded_tool_name(tool_name),
        max_bytes=max_bytes,
        artifact_path=artifact_path,
    )
    if isinstance(result, str):
        return rendered
    try:
        return json.loads(rendered)
    except ValueError:
        return {"success": False, "error": "response_budget_failure"}


def _artifact_path(artifact_dir: Path, tool_name: str) -> Path:
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in tool_name)
    return artifact_dir / f"{safe_name or 'response'}_{uuid.uuid4().hex[:8]}.log"


def _finalize_envelope(envelope: dict[str, Any], *, max_bytes: int) -> str:
    metadata = envelope.get(RESPONSE_SPILL_METADATA_KEY)
    if not isinstance(metadata, dict) or "projected_utf8_bytes" not in metadata:
        raise _ProjectionNonconvergentError("spill metadata missing projected byte field")
    max_decimal_width = len(str(max(0, max_bytes)))
    seen: set[tuple[int, int]] = set()
    for _ in range(max_decimal_width + 2):
        rendered = _canonical_json(envelope)
        measured = len(rendered.encode("utf-8"))
        current = metadata.get("projected_utf8_bytes")
        if current == measured:
            return rendered
        state = (current if isinstance(current, int) else -1, measured)
        if state in seen:
            break
        seen.add(state)
        metadata["projected_utf8_bytes"] = measured
    raise _ProjectionNonconvergentError(
        "projected byte fixed point did not converge: "
        f"measured={len(_canonical_json(envelope).encode('utf-8'))} "
        f"projected={metadata.get('projected_utf8_bytes')!r} "
        f"max_bytes={max_bytes} attempted_states={len(seen)}"
    )


def _spill_metadata(
    metadata: dict[str, Any],
    *,
    reason: str,
    omitted_chars: int,
    omitted_items: int,
) -> dict[str, Any]:
    return {
        "schema_version": RESPONSE_SPILL_SCHEMA_VERSION,
        **metadata,
        "projected_utf8_bytes": 0,
        "omitted_chars": omitted_chars,
        "omitted_items": omitted_items,
        "reason": reason,
    }


def _total_omissions(value: Any) -> tuple[int, int]:
    if isinstance(value, str):
        return len(value), 0
    if isinstance(value, list):
        nested = [_total_omissions(item) for item in value]
        return sum(chars for chars, _ in nested), len(value) + sum(items for _, items in nested)
    if isinstance(value, dict):
        nested = [_total_omissions(item) for item in value.values()]
        return sum(chars for chars, _ in nested), len(value) + sum(items for _, items in nested)
    return 0, 0


def _project_json_object(
    parsed: dict[str, Any],
    *,
    metadata: dict[str, Any],
    max_bytes: int,
    inline_chars: int,
    delivery_bound_triggered: bool = False,
) -> str | None:
    """Project a non-exempted JSON object into a bounded envelope.

    When ``delivery_bound_triggered`` is True, the caller is in the
    delivery-bound spill path; the function routes to ``_tiered_projection``
    to protect a content-equivalent key (``"result"`` for run_skill-shaped
    SkillResult payloads) from the identical starvation defect that affects
    ``_delivery_bound_summary``'s ``content`` field.

    When ``delivery_bound_triggered`` is False (the default), the existing
    uniform per-key ``_project_value`` algorithm runs unchanged — preserving
    the behavior pinned by ``test_minimal_projection_has_exact_bytes_and_omission_aggregates``.
    """
    if RESPONSE_SPILL_METADATA_KEY in parsed:
        return None

    if delivery_bound_triggered and "result" in parsed:
        priority_keys = tuple(
            key
            for key in (
                "success",
                "kitchen",
                "version",
                "errors",
                "kill_reason",
                "audit_status",
                "audit_verdict",
                "audit_cycle_path",
                "audit_attempt_id",
            )
            if key in parsed
        )
        deprioritized_keys = tuple(key for key in parsed if key not in {*priority_keys, "result"})
        return _tiered_projection(
            parsed,
            metadata=metadata,
            bound=max_bytes,
            content_key="result",
            deprioritized_keys=deprioritized_keys,
            priority_keys=priority_keys,
            droppable_keys=(),
            reason="delivery_bound",
            top_level_flag=None,
        )

    key_count = max(1, len(parsed))
    value_limit = max(16, min(inline_chars, max_bytes // (key_count + 1)))
    while value_limit >= 16:
        projected: dict[str, Any] = {}
        omitted_chars = 0
        omitted_items = 0
        for key, value in parsed.items():
            item, chars, items = _project_value(value, value_limit)
            projected[key] = item
            omitted_chars += chars
            omitted_items += items
        projected[RESPONSE_SPILL_METADATA_KEY] = {
            **_spill_metadata(
                metadata,
                reason="oversized_values",
                omitted_chars=omitted_chars,
                omitted_items=omitted_items,
            )
        }
        rendered = _finalize_envelope(projected, max_bytes=max_bytes)
        if len(rendered.encode("utf-8")) <= max_bytes:
            return rendered
        value_limit //= 2

    minimal = {key: _minimal_same_type(value) for key, value in parsed.items()}
    omitted_chars = 0
    omitted_items = 0
    for value in parsed.values():
        chars, items = _total_omissions(value)
        omitted_chars += chars
        omitted_items += items
    minimal[RESPONSE_SPILL_METADATA_KEY] = _spill_metadata(
        metadata,
        reason="minimal_projection",
        omitted_chars=omitted_chars,
        omitted_items=omitted_items,
    )
    rendered = _finalize_envelope(minimal, max_bytes=max_bytes)
    return rendered if len(rendered.encode("utf-8")) <= max_bytes else None


def _plain_spill_envelope(
    original: str,
    *,
    metadata: dict[str, Any],
    max_bytes: int,
    inline_chars: int,
    reason: str = "plain_text",
) -> str | None:
    preview_limit = max(0, min(inline_chars, max_bytes // 3))
    while True:
        preview, omitted_chars = _preview_string(original, preview_limit)
        envelope: dict[str, Any] = {
            RESPONSE_SPILL_METADATA_KEY: _spill_metadata(
                metadata,
                reason=reason,
                omitted_chars=omitted_chars,
                omitted_items=0,
            ),
            "preview": preview,
        }
        rendered = _finalize_envelope(envelope, max_bytes=max_bytes)
        if len(rendered.encode("utf-8")) <= max_bytes:
            return rendered
        if preview_limit == 0:
            return None
        preview_limit //= 2


_DELIVERY_BOUND_PRIORITY_KEYS: tuple[str, ...] = (
    "success",
    "receipt_id",
    "kitchen",
    "version",
    "orchestration_rules",
    "stop_step_semantics",
    "errors",
)
_DELIVERY_BOUND_DEPRIORITIZED_KEYS: tuple[str, ...] = (
    "suggestions",
    "ingredients_table",
)
_DELIVERY_BOUND_CONTENT_KEY = "content"
_DELIVERY_BOUND_DROPPABLE_KEYS: tuple[str, ...] = ("diagram",)

# Floor (bytes) reserved per present deprioritized key. Covers the smallest
# plausible serialized ``"key": value`` pair once a value is projected down
# toward empty (``_minimal_same_type`` reduces list/dict to ``[]``/``{}``,
# the floor covers the surrounding key/quote/colon overhead). Guarantees
# deprioritized keys remain *present* in the envelope — projected but
# never dropped entirely.
_DEPRIORITIZED_KEY_FLOOR_BYTES = 16


def _serialized_string_prefix_length(value: str, max_bytes: int) -> int:
    """Return the largest prefix whose serialized string body fits ``max_bytes``."""
    low, high = 0, len(value)
    best = 0
    while low <= high:
        midpoint = (low + high) // 2
        serialized_bytes = len(_canonical_json(value[:midpoint]).encode("utf-8")) - 2
        if serialized_bytes <= max_bytes:
            best = midpoint
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


def _tiered_projection(
    parsed: dict[str, Any],
    *,
    metadata: dict[str, Any],
    bound: int,
    content_key: str,
    deprioritized_keys: tuple[str, ...],
    priority_keys: tuple[str, ...] = (),
    droppable_keys: tuple[str, ...] = (),
    reason: str,
    top_level_flag: str | None = "delivery_bound_spill",
) -> str | None:
    """Build a tiered projection honoring a byte ``bound``.

    Budget allocation order:
    1. Priority keys (caller-supplied ``priority_keys``) are preserved
       verbatim first — these are small and structurally necessary.
    2. Deprioritized keys (``deprioritized_keys``) are reserved a per-key
       floor (``_DEPRIORITIZED_KEY_FLOOR_BYTES``) so they remain *present*
       in the envelope even after projection.
    3. ``content_key`` is allocated a guaranteed floor from the remaining
       budget (at least half the post-floor remainder); Tier 1 then
       binary-searches upward from that floor for the largest
       ``content_head`` that still fits alongside the un-projected
       deprioritized/droppable keys — reallocating any budget left unused
       because those keys serialize smaller than their assumed share.
       This binary search is the sole reallocation mechanism: no separate
       "measure freed bytes, then rebuild with a computed head length"
       pass exists, because the search already finds the true maximum
       against the exact rendered size.
    4. Any remaining budget flows to the deprioritized keys (which are
       projected to fit).
    5. ``droppable_keys`` are included verbatim if they fit within the
       remaining budget; otherwise they are dropped (counted as omission).
       They are not part of the priority verbatim set.
    6. Keys not in any tier are always dropped (counted as omission).

    Returns the rendered envelope string when it fits ``bound``; otherwise
    ``None`` so the caller fails closed.
    """
    present_priority = [k for k in priority_keys if k in parsed]
    present_deprioritized = [k for k in deprioritized_keys if k in parsed]
    present_droppable = [k for k in droppable_keys if k in parsed]
    tier_all = set(priority_keys) | set(deprioritized_keys) | set(droppable_keys) | {content_key}
    other_keys = [k for k in parsed if k not in tier_all]

    content_text = parsed.get(content_key) if content_key in parsed else None
    content_is_str = isinstance(content_text, str)

    # Step 1: Measure the priority-only envelope (content excluded, deprioritized
    # excluded, droppable excluded) to size the remaining budget for content +
    # deprioritized + droppable.
    def _build_priority_only() -> dict[str, Any]:
        env: dict[str, Any] = {RESPONSE_SPILL_METADATA_KEY: dict(metadata)}
        if top_level_flag is not None:
            env[top_level_flag] = True
        for k in present_priority:
            env[k] = parsed[k]
        # Account for omitted "other" keys up front (always dropped).
        base_chars = 0
        base_items = 0
        for k in other_keys:
            chars, items = _total_omissions(parsed[k])
            base_chars += chars
            base_items += items
        env[RESPONSE_SPILL_METADATA_KEY] = _spill_metadata(
            env[RESPONSE_SPILL_METADATA_KEY],
            reason=reason,
            omitted_chars=base_chars,
            omitted_items=base_items,
        )
        return env

    priority_envelope = _build_priority_only()
    try:
        priority_rendered = _finalize_envelope(priority_envelope, max_bytes=bound)
    except _ProjectionNonconvergentError:
        return None
    priority_bytes = len(priority_rendered.encode("utf-8"))

    # Step 2: Deprioritized key floor (reserved bytes per present deprioritized key).
    deprioritized_floor = _DEPRIORITIZED_KEY_FLOOR_BYTES * len(present_deprioritized)

    # Step 3: Content budget = remaining after priority + deprioritized floor.
    content_budget = max(0, bound - priority_bytes - deprioritized_floor - 64)

    # Step 4: Reserve at least half the remaining byte budget for string
    # content. Convert that serialized-byte floor to a character count so
    # multibyte and escaped content remain byte-safe. Non-empty content always
    # retains at least one character or the projection fails closed.
    if content_is_str:
        text = content_text or ""
        floor_bytes = max(1, content_budget // 2)
        content_floor = max(1, _serialized_string_prefix_length(text, floor_bytes)) if text else 0
    else:
        text = ""
        content_floor = 0

    # Deprioritized keys get their floor plus any share of the remaining
    # budget after the content floor.
    deprioritized_budget = deprioritized_floor + max(0, content_budget - content_floor)

    def _build_with_lengths(
        content_head: int,
        deprioritized_projector: Any = None,
        content_projector: Any = None,
        include_content: bool = True,
        include_deprioritized: bool = True,
        include_droppable: bool = True,
    ) -> dict[str, Any]:
        env: dict[str, Any] = {RESPONSE_SPILL_METADATA_KEY: dict(metadata)}
        if top_level_flag is not None:
            env[top_level_flag] = True
        base_chars = 0
        base_items = 0

        for k in present_priority:
            env[k] = parsed[k]

        # Always-omitted "other" keys (counted as omissions).
        for k in other_keys:
            chars, items = _total_omissions(parsed[k])
            base_chars += chars
            base_items += items

        if include_deprioritized and deprioritized_projector is not None:
            for k in present_deprioritized:
                projected = deprioritized_projector(parsed[k])
                env[k] = projected
                if not (
                    isinstance(projected, (list, dict))
                    and RESPONSE_SPILL_METADATA_KEY in projected
                ):
                    chars, items = _total_omissions(parsed[k])
                    base_chars += chars
                    base_items += items
        elif include_deprioritized:
            for k in present_deprioritized:
                env[k] = parsed[k]

        if include_droppable:
            for k in present_droppable:
                env[k] = parsed[k]
        else:
            for k in present_droppable:
                chars, items = _total_omissions(parsed[k])
                base_chars += chars
                base_items += items

        if include_content and content_is_str:
            env[content_key] = text[:content_head]
            base_chars += max(0, len(text) - content_head)
        elif content_key in parsed and include_content:
            if content_projector is None:
                env[content_key] = parsed[content_key]
            else:
                projected, chars, items = content_projector(parsed[content_key])
                env[content_key] = projected
                base_chars += chars
                base_items += items
        elif content_key in parsed and not include_content:
            chars, items = _total_omissions(parsed[content_key])
            base_chars += chars
            base_items += items

        env[RESPONSE_SPILL_METADATA_KEY] = _spill_metadata(
            env[RESPONSE_SPILL_METADATA_KEY],
            reason=reason,
            omitted_chars=base_chars,
            omitted_items=base_items,
        )
        return env

    def _fits(env: dict[str, Any]) -> str | None:
        try:
            rendered = _finalize_envelope(env, max_bytes=bound)
        except _ProjectionNonconvergentError:
            return None
        if len(rendered.encode("utf-8")) <= bound:
            return rendered
        return None

    def _maximize_content_head(
        *, deprioritized_projector: Any = None, include_droppable: bool = True
    ) -> str | None:
        """Binary-search the largest ``content_head`` (in ``[content_floor, len(text)]``)
        whose rendered envelope still fits ``bound``.

        Used by Tier 1 and Tier 2 to reallocate any budget left unused because
        deprioritized/droppable keys serialize smaller than their allotted
        share — the search finds the true maximum against the exact rendered
        size, with no separate measure-then-reallocate pass required.
        """
        low, high = content_floor, len(text)
        best_rendered: str | None = None
        while low <= high:
            mid = (low + high) // 2
            candidate = _fits(
                _build_with_lengths(
                    content_head=mid,
                    deprioritized_projector=deprioritized_projector,
                    include_droppable=include_droppable,
                )
            )
            if candidate is not None:
                best_rendered = candidate
                low = mid + 1
            else:
                high = mid - 1
        return best_rendered

    if content_key in parsed and not content_is_str:
        rendered = _fits(_build_with_lengths(content_head=0))
        if rendered is not None:
            return rendered

        value_limit = max(16, content_budget)
        while value_limit >= 16:

            def _project_content(value: Any, _limit: int = value_limit) -> tuple[Any, int, int]:
                return _project_value(value, _limit)

            rendered = _fits(
                _build_with_lengths(
                    content_head=0,
                    content_projector=_project_content,
                    include_droppable=False,
                )
            )
            if rendered is not None:
                return rendered
            if present_deprioritized:
                rendered = _fits(
                    _build_with_lengths(
                        content_head=0,
                        content_projector=_project_content,
                        deprioritized_projector=lambda value: _minimal_same_type(value),
                        include_droppable=False,
                    )
                )
                if rendered is not None:
                    return rendered
            value_limit //= 2

        rendered = _fits(
            _build_with_lengths(
                content_head=0,
                content_projector=lambda value: (
                    _minimal_same_type(value),
                    *_total_omissions(value),
                ),
                deprioritized_projector=lambda value: _minimal_same_type(value),
                include_droppable=False,
            )
        )
        if rendered is not None:
            return rendered

    # Tier 1: priority verbatim + deprioritized verbatim + droppable; binary-search the
    # largest content_head (from content_floor up to the full text) that still fits, so
    # any budget left unused because deprioritized/droppable keys serialize smaller than
    # their allotted share is reallocated back to content rather than stranded at the floor.
    if content_is_str:
        rendered = _maximize_content_head()
        if rendered is not None:
            return rendered

    # Tier 2: deprioritized keys projected to fit deprioritized_budget; binary-search
    # for the largest content_head that fits. Droppable keys are dropped to
    # maximize budget for content.
    if content_is_str and present_deprioritized:
        value_limit = max(16, deprioritized_budget // (len(present_deprioritized) + 1))
        while value_limit >= _DEPRIORITIZED_KEY_FLOOR_BYTES:

            def _project_with_limit(value: Any, _limit: int = value_limit) -> Any:
                projected = _project_value(value, _limit)[0]
                return projected

            rendered = _maximize_content_head(
                deprioritized_projector=_project_with_limit, include_droppable=False
            )
            if rendered is not None:
                return rendered
            value_limit //= 2

        # Fallback: deprioritized at minimum (still present, projected to floor).
        rendered = _fits(
            _build_with_lengths(
                content_head=content_floor,
                deprioritized_projector=lambda v: _minimal_same_type(v),
                include_droppable=False,
            )
        )
        if rendered is not None:
            return rendered

    # Tier 3: drop droppable + deprioritized at floor; content already at floor.
    if present_deprioritized:
        rendered = _fits(
            _build_with_lengths(
                content_head=content_floor,
                deprioritized_projector=lambda v: _minimal_same_type(v),
                include_droppable=False,
            )
        )
        if rendered is not None:
            return rendered

    # A non-empty string content field must retain its positive floor. Returning
    # the priority-only fallback would silently starve the promised payload.
    if content_is_str and text:
        return None

    # Tier 4 (terminal fallback): priority verbatim only — content excluded
    # entirely; droppable excluded; deprioritized excluded. Used when payload
    # has no identifiable content_key (e.g. ``{"success": True, "data": ...}``)
    # so the envelope still surfaces priority keys rather than failing closed.
    rendered = _fits(
        _build_with_lengths(
            content_head=0,
            deprioritized_projector=lambda v: _minimal_same_type(v),
            include_content=False,
            include_deprioritized=False,
            include_droppable=False,
        )
    )
    if rendered is not None:
        return rendered

    return None


def _delivery_bound_summary(
    parsed: dict[str, Any],
    *,
    metadata: dict[str, Any],
    bound: int,
) -> str | None:
    """Build a bounded inline summary honoring the delivery bound.

    Preserves the priority keys (``success``, ``kitchen``, ``version``,
    ``orchestration_rules``, ``stop_step_semantics``, ``errors``) verbatim;
    allocates a guaranteed floor to ``content``; reserves a per-key floor
    for deprioritized keys (``suggestions``, ``ingredients_table``) so they
    remain present but projected; drops ``diagram``; nests spill metadata
    under ``RESPONSE_SPILL_METADATA_KEY`` with ``reason="delivery_bound"``;
    sets top-level ``delivery_bound_spill=True``.

    Returns the rendered envelope string when it fits ``bound``; otherwise
    ``None`` so the caller fails closed.

    Regression guard for issue #4304: the historical algorithm computed
    ``head_limit = max(0, bound - base_bytes - 64)`` from the unshrunk
    preserved-key envelope, found ``base_bytes > bound`` when ``suggestions``
    was at the real-world 48KB+ size regime, and starved ``content`` to
    ``""``. This implementation allocates the budget in priority order:
    priority keys verbatim → deprioritized floor → content floor, maximized
    via binary search so freed budget flows back to content → any remaining
    to deprioritized keys. Droppable keys (diagram) are included only if
    budget allows.
    """
    return _tiered_projection(
        parsed,
        metadata=metadata,
        bound=bound,
        content_key=_DELIVERY_BOUND_CONTENT_KEY,
        deprioritized_keys=_DELIVERY_BOUND_DEPRIORITIZED_KEYS,
        priority_keys=_DELIVERY_BOUND_PRIORITY_KEYS,
        droppable_keys=_DELIVERY_BOUND_DROPPABLE_KEYS,
        reason="delivery_bound",
        top_level_flag="delivery_bound_spill",
    )


def _spill_for_delivery_bound(
    result: Any,
    *,
    tool_name: str,
    config: OutputBudgetConfig,
    artifact_dir: Path | None,
    original: str,
    original_size: int,
    selected_result_token_limit: int,
) -> Any:
    """Persist ``original`` and return a bounded projection honoring the delivery bound.

    Used when an exempted or under-byte-budget payload still exceeds the
    downstream backend's effective delivery token limit. Mirrors the
    non-exempted spill machinery (atomic_write, _artifact_path,
    _project_json_object) so the caller sees the same envelope shape, with
    ``reason="delivery_bound"`` so downstream formatters distinguish it.
    """
    if artifact_dir is None:
        return bounded_response_budget_failure(
            result,
            cause="context_unavailable",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
            original_utf8_bytes=original_size,
        )
    path = _artifact_path(artifact_dir, tool_name)
    try:
        atomic_write(path, original)
    except OSError:
        return bounded_response_budget_failure(
            result,
            cause="artifact_publication_failed",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
            original_utf8_bytes=original_size,
        )
    published = str(path.resolve())
    metadata: dict[str, Any] = {
        "artifact_path": published,
        "sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
        "original_utf8_bytes": original_size,
        "reason": "delivery_bound",
    }
    parsed: Any
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (TypeError, ValueError, RecursionError):
            parsed = None
    else:
        parsed = result
    bound = selected_result_token_limit * 4
    floor_bytes = min(bound, config.response_max_bytes)
    rendered: str | None
    try:
        if isinstance(parsed, dict):
            rendered = _delivery_bound_summary(parsed, metadata=metadata, bound=bound)
        else:
            rendered = _plain_spill_envelope(
                original,
                metadata=metadata,
                max_bytes=floor_bytes,
                inline_chars=config.inline_max_chars,
                reason="delivery_bound",
            )
    except _ProjectionNonconvergentError as exc:
        _emit_response_budget_event(
            "response_budget_projection_nonconvergent",
            tool_name=_bounded_tool_name(tool_name),
            detail=str(exc),
        )
        return bounded_response_budget_failure(
            "",
            cause="projection_nonconvergent",
            tool_name=tool_name,
            max_bytes=floor_bytes,
            original_utf8_bytes=original_size,
            artifact_path=published,
        )
    except RecursionError:
        return bounded_response_budget_failure(
            "",
            cause="irreducible_shape",
            tool_name=tool_name,
            max_bytes=floor_bytes,
            original_utf8_bytes=original_size,
            artifact_path=published,
        )
    if rendered is None:
        return bounded_response_budget_failure(
            "",
            cause="irreducible_shape",
            tool_name=tool_name,
            max_bytes=floor_bytes,
            original_utf8_bytes=original_size,
            artifact_path=published,
        )
    _emit_response_budget_event(
        "response_budget_spill",
        tool_name=_bounded_tool_name(tool_name),
        original_utf8_bytes=original_size,
        projected_utf8_bytes=len(rendered.encode("utf-8")),
    )
    if isinstance(result, str):
        return rendered
    try:
        return json.loads(rendered)
    except (ValueError, RecursionError):
        return {"success": False, "error": "response_budget_projection_invalid"}


def enforce_response_budget(
    result: Any,
    *,
    tool_name: str,
    artifact_dir: Path | None,
    config: OutputBudgetConfig,
    force_spill: bool = False,
    selected_result_token_limit: int | None = None,
) -> Any:
    """Return a bounded response of the same handler type.

    Oversized content is atomically persisted before a projection is returned.
    Artifact failure and missing-context cases fail closed without echoing the
    original payload.

    ``selected_result_token_limit`` is the authoritative bound selected for the
    current downstream transport. For ordinary calls this is the backend's
    unnegotiated limit; protected recipe calls may supply an attested limit.
    Payloads whose estimated token count exceeds it are spilled even if they
    pass the server-side exemption or response-byte ceilings.
    """
    try:
        original = _serialized(result)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return bounded_response_budget_failure(
            result,
            cause="serialization_failed",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
            original_utf8_bytes=0,
        )
    original_bytes = original.encode("utf-8")
    original_size = len(original_bytes)
    over_delivery_bound = (
        selected_result_token_limit is not None
        and selected_result_token_limit > 0
        and _estimated_tokens(original_size) > selected_result_token_limit
    )
    exemption = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY.get(tool_name)
    if exemption is not None:
        within_ceiling = (
            len(original) <= exemption.max_chars and original_size <= exemption.max_utf8_bytes
        )
        if not within_ceiling:
            return bounded_response_budget_failure(
                result,
                cause="exemption_ceiling_exceeded",
                tool_name=tool_name,
                max_bytes=config.response_max_bytes,
                original_utf8_bytes=original_size,
            )
        if over_delivery_bound:
            assert selected_result_token_limit is not None
            return _spill_for_delivery_bound(
                result,
                tool_name=tool_name,
                config=config,
                artifact_dir=artifact_dir,
                original=original,
                original_size=original_size,
                selected_result_token_limit=selected_result_token_limit,
            )
        _emit_response_budget_event(
            "response_budget_exemption",
            tool_name=_bounded_tool_name(tool_name),
            measurement_id=exemption.measurement_id,
            original_chars=len(original),
            original_utf8_bytes=original_size,
            max_chars=exemption.max_chars,
            max_utf8_bytes=exemption.max_utf8_bytes,
        )
        return result
    if (
        not force_spill
        and not over_delivery_bound
        and len(original_bytes) <= config.response_max_bytes
    ):
        return result
    if artifact_dir is None:
        return bounded_response_budget_failure(
            result,
            cause="context_unavailable",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
            original_utf8_bytes=original_size,
        )

    path = _artifact_path(artifact_dir, tool_name)
    try:
        atomic_write(path, original)
    except OSError:
        return bounded_response_budget_failure(
            result,
            cause="artifact_publication_failed",
            tool_name=tool_name,
            max_bytes=config.response_max_bytes,
            original_utf8_bytes=original_size,
        )

    published = str(path.resolve())
    metadata = {
        "artifact_path": published,
        "sha256": hashlib.sha256(original_bytes).hexdigest(),
        "original_utf8_bytes": len(original_bytes),
    }

    delivery_bound_bytes = (
        selected_result_token_limit * 4
        if selected_result_token_limit is not None and selected_result_token_limit > 0
        else None
    )
    projection_max_bytes = (
        min(config.response_max_bytes, delivery_bound_bytes)
        if delivery_bound_bytes is not None
        else config.response_max_bytes
    )
    projection_inline_chars = min(config.inline_max_chars, projection_max_bytes)

    parsed: Any = None
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (TypeError, ValueError, RecursionError):
            parsed = None
    else:
        parsed = result

    rendered: str | None = None
    try:
        if isinstance(parsed, dict):
            rendered = _project_json_object(
                parsed,
                metadata=metadata,
                max_bytes=projection_max_bytes,
                inline_chars=projection_inline_chars,
                delivery_bound_triggered=over_delivery_bound,
            )
        if rendered is None and not isinstance(parsed, dict):
            rendered = _plain_spill_envelope(
                original,
                metadata=metadata,
                max_bytes=projection_max_bytes,
                inline_chars=projection_inline_chars,
            )
    except _ProjectionNonconvergentError as exc:
        _emit_response_budget_event(
            "response_budget_projection_nonconvergent",
            tool_name=_bounded_tool_name(tool_name),
            detail=str(exc),
        )
        rendered = bounded_response_budget_failure(
            "",
            cause="projection_nonconvergent",
            tool_name=tool_name,
            max_bytes=projection_max_bytes,
            original_utf8_bytes=original_size,
            artifact_path=published,
        )
    except RecursionError:
        # Projection recurses per nesting level; the artifact is already
        # persisted, so the recovery pointer must survive the stack failure.
        rendered = bounded_response_budget_failure(
            "",
            cause="irreducible_shape",
            tool_name=tool_name,
            max_bytes=projection_max_bytes,
            original_utf8_bytes=original_size,
            artifact_path=published,
        )
    if rendered is None:
        rendered = bounded_response_budget_failure(
            "",
            cause="irreducible_shape",
            tool_name=tool_name,
            max_bytes=projection_max_bytes,
            original_utf8_bytes=original_size,
            artifact_path=published,
        )

    assert isinstance(rendered, str)
    _emit_response_budget_event(
        "response_budget_spill",
        tool_name=_bounded_tool_name(tool_name),
        original_utf8_bytes=original_size,
        projected_utf8_bytes=len(rendered.encode("utf-8")),
    )
    if isinstance(result, str):
        return rendered
    try:
        return json.loads(rendered)
    except (ValueError, RecursionError):
        return {"success": False, "error": "response_budget_projection_invalid"}


def shape_json_response(
    payload: dict[str, Any],
    *,
    tool_name: str,
    artifact_dir: Path,
    config: OutputBudgetConfig,
    selected_result_token_limit: int | None = None,
) -> str:
    """Serialize a tool dict, spilling once it crosses the source threshold."""
    rendered = json.dumps(payload)
    if len(rendered) <= config.inline_max_chars:
        return rendered
    shaped = enforce_response_budget(
        rendered,
        tool_name=tool_name,
        artifact_dir=artifact_dir,
        config=config,
        force_spill=True,
        selected_result_token_limit=selected_result_token_limit,
    )
    return shaped if isinstance(shaped, str) else json.dumps(shaped)


__all__ = [
    "RESPONSE_BUDGET_FAILURE_CAUSES",
    "RESPONSE_SPILL_METADATA_KEY",
    "RESPONSE_SPILL_METADATA_KEYS",
    "RESPONSE_SPILL_REASONS",
    "RESPONSE_SPILL_SCHEMA_DIGEST",
    "RESPONSE_SPILL_SCHEMA_VERSION",
    "bounded_response_budget_failure",
    "emit_response_budget_failure",
    "enforce_response_budget",
    "shape_json_response",
]
