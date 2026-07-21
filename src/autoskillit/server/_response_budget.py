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
    dump_yaml_str,
    get_logger,
    load_yaml,
)
from autoskillit.execution import resolve_worst_case_delivery_bound

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

    Matches the ``CODEX_TOOL_OUTPUT_TOKEN_LIMIT`` derivation: a coarse
    transport-layer estimate, not a tokenizer count. Used to compare
    payload size against ``effective_delivery_token_limit``.
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


def _artifact_path(artifact_dir: Path, tool_name: str, content_sha256: str) -> Path:
    """Compute a deterministic artifact path keyed by tool_name + content hash.

    The deterministic scheme makes the artifact path stable across calls with the
    same content, so the pull tool (`get_recipe_section`) and the response
    envelope agree on a single path. Falls back to a UUID-derived slug only when
    ``content_sha256`` is empty (kept narrow on purpose).
    """
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in tool_name)
    digest = (content_sha256 or "")[:16]
    if not digest:
        digest = uuid.uuid4().hex[:16]
    return artifact_dir / f"{safe_name or 'response'}_{digest}.log"


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
) -> str | None:
    if RESPONSE_SPILL_METADATA_KEY in parsed:
        return None

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


_DELIVERY_BOUND_PRESERVED_KEYS: tuple[str, ...] = (
    "success",
    "kitchen",
    "version",
    "ingredients_table",
    "orchestration_rules",
    "stop_step_semantics",
    "errors",
    "suggestions",
    # Part B envelope fields — must survive any second-pass delivery-bound
    # projection so the pull tool can still resolve the persisted artifact.
    "step_flow_skeleton",
    "step_index",
    "pull_tool",
    "artifact_path",
    "sha256",
)
_DELIVERY_BOUND_CONTENT_KEY = "content"
_DELIVERY_BOUND_DROPPABLE_KEYS: tuple[str, ...] = ("diagram",)

_STEP_ROUTING_FIELDS: tuple[str, ...] = (
    "on_success",
    "on_failure",
    "on_result",
    "on_context_limit",
)


def extract_step_routing(content: str, step_names: list[str]) -> list[dict[str, Any]]:
    """Return per-step routing/summary metadata as a list of dicts.

    Each dict contains the step ``name``, ``summary`` (if present), and the
    routing fields (``on_success``, ``on_failure``, ``on_result``,
    ``on_context_limit``) when set. Used by both ``build_recipe_envelope``
    (Part B primary delivery shape) and ``_extract_step_skeleton`` (Part A
    backstop / byte-floor sizing) so the two paths agree on the routing set.

    Falls back to one minimal entry per step when YAML parsing fails — no
    summary, no routing fields, just the name so consumers can still locate
    each step in the bounded payload.
    """
    if not step_names:
        return []
    parsed_content: Any = None
    try:
        parsed_content = load_yaml(content)
    except Exception:
        logger.warning("step_routing_yaml_parse_failed", exc_info=True)
        parsed_content = None

    out: list[dict[str, Any]] = []
    if isinstance(parsed_content, dict):
        steps_obj = parsed_content.get("steps")
        for step_name in step_names:
            entry: dict[str, Any] = {"name": step_name}
            step_obj = steps_obj.get(step_name) if isinstance(steps_obj, dict) else None
            if isinstance(step_obj, dict):
                summary_value = step_obj.get("summary")
                if isinstance(summary_value, str):
                    entry["summary"] = summary_value
                for field in _STEP_ROUTING_FIELDS:
                    if field in step_obj:
                        entry[field] = step_obj[field]
            out.append(entry)
        return out

    # Fallback: synthetic / unparseable content. Emit one minimal entry per
    # step name so consumers can still enumerate the step set.
    return [{"name": name} for name in step_names]


def _extract_step_skeleton(content: str, step_names: list[str]) -> str:
    """Build a minimal YAML skeleton containing only routing fields for the given steps.

    Thin Part-A wrapper around ``extract_step_routing``: serializes only the
    routing-field subset (dropping ``summary``) via ``dump_yaml_str`` for the
    byte-floor sizing use case in ``_delivery_bound_summary``. Both Part A
    and Part B call the shared routing extractor to keep the routing-field
    set in lock-step.

    Falls back to a substring-tagged plain-text skeleton when YAML parsing
    fails (e.g., synthetic test payloads with malformed or pseudo-YAML text).
    The resulting skeleton carries every ``step_name`` as a substring so
    downstream consumers can locate each step in the bounded payload.
    """
    if not step_names:
        return ""
    parsed_content: Any = None
    skeleton_yaml: str | None = None
    try:
        parsed_content = load_yaml(content)
    except Exception:
        logger.warning("step_skeleton_yaml_parse_failed", exc_info=True)
        parsed_content = None
    if isinstance(parsed_content, dict):
        steps = parsed_content.get("steps")
        skeleton_steps: dict[str, dict[str, Any]] = {}
        if isinstance(steps, dict):
            for step_name in step_names:
                step_obj = steps.get(step_name)
                if not isinstance(step_obj, dict):
                    skeleton_steps[step_name] = {}
                    continue
                routing = {
                    field: step_obj[field] for field in _STEP_ROUTING_FIELDS if field in step_obj
                }
                skeleton_steps[step_name] = routing
        else:
            skeleton_steps = {name: {} for name in step_names}
        if skeleton_steps:
            try:
                skeleton_yaml = dump_yaml_str({"steps": skeleton_steps}, default_flow_style=False)
            except Exception:
                logger.warning("step_skeleton_dump_failed", exc_info=True)
                skeleton_yaml = None
    if skeleton_yaml is not None:
        return skeleton_yaml
    return "\n".join(f"  {name}:" for name in step_names) + "\n"


def _delivery_bound_summary(
    parsed: dict[str, Any],
    *,
    metadata: dict[str, Any],
    bound: int,
) -> str | None:
    """Build a bounded inline summary honoring the delivery bound.

    Preserves ``success``, ``kitchen``, ``version``, ``ingredients_table``,
    ``orchestration_rules``, ``stop_step_semantics``, ``errors``,
    ``suggestions`` verbatim (when present and fitting); truncates ``content``;
    drops ``diagram``; nests spill metadata under ``RESPONSE_SPILL_METADATA_KEY``
    with ``reason="delivery_bound"``; sets top-level ``delivery_bound_spill=True``.

    Returns the rendered envelope string when it fits ``bound``; otherwise
    ``None`` so the caller fails closed.
    """
    content_text = parsed.get(_DELIVERY_BOUND_CONTENT_KEY)
    content_is_str = isinstance(content_text, str)

    precomputed_base_chars = 0
    precomputed_base_items = 0
    for key, value in parsed.items():
        if key in _DELIVERY_BOUND_PRESERVED_KEYS or key == _DELIVERY_BOUND_CONTENT_KEY:
            continue
        chars, items = _total_omissions(value)
        precomputed_base_chars += chars
        precomputed_base_items += items

    if not content_is_str and _DELIVERY_BOUND_CONTENT_KEY in parsed:
        chars, items = _total_omissions(parsed[_DELIVERY_BOUND_CONTENT_KEY])
        precomputed_base_chars += chars
        precomputed_base_items += items

    present_preserved = [key for key in _DELIVERY_BOUND_PRESERVED_KEYS if key in parsed]

    def _build(head_len: int, include_droppable: bool, value_projector=None) -> dict[str, Any]:
        envelope: dict[str, Any] = {RESPONSE_SPILL_METADATA_KEY: dict(metadata)}
        envelope["delivery_bound_spill"] = True

        base_chars = precomputed_base_chars
        base_items = precomputed_base_items

        for key in present_preserved:
            if value_projector is None:
                envelope[key] = parsed[key]
            else:
                envelope[key] = value_projector(parsed[key])

        if include_droppable:
            for key in _DELIVERY_BOUND_DROPPABLE_KEYS:
                if key in parsed:
                    envelope[key] = parsed[key]
        else:
            for key in _DELIVERY_BOUND_DROPPABLE_KEYS:
                if key in parsed:
                    chars, items = _total_omissions(parsed[key])
                    base_chars += chars
                    base_items += items

        if content_is_str:
            text = content_text or ""
            skeleton_len = len(skeleton_text)
            if skeleton_text:
                truncated = text[: max(0, head_len - skeleton_len)] + skeleton_text
            else:
                truncated = text[:head_len]
            envelope[_DELIVERY_BOUND_CONTENT_KEY] = truncated
            base_chars += max(0, len(text) - head_len)
        elif _DELIVERY_BOUND_CONTENT_KEY in parsed:
            envelope[_DELIVERY_BOUND_CONTENT_KEY] = parsed[_DELIVERY_BOUND_CONTENT_KEY]

        if value_projector is not None:
            for key in present_preserved:
                projected_value = envelope[key]
                if (
                    isinstance(projected_value, dict)
                    and RESPONSE_SPILL_METADATA_KEY in projected_value
                ):
                    continue
                chars, items = _total_omissions(parsed[key])
                base_chars += chars
                base_items += items

        envelope[RESPONSE_SPILL_METADATA_KEY] = _spill_metadata(
            envelope[RESPONSE_SPILL_METADATA_KEY],
            reason="delivery_bound",
            omitted_chars=base_chars,
            omitted_items=base_items,
        )
        return envelope

    def _fits(envelope: dict[str, Any]) -> str | None:
        try:
            rendered = _finalize_envelope(envelope, max_bytes=bound)
        except _ProjectionNonconvergentError:
            return None
        if len(rendered.encode("utf-8")) <= bound:
            return rendered
        return None

    post_prune_step_names_raw = parsed.get("post_prune_step_names", [])
    step_names_for_floor: list[str] = (
        list(post_prune_step_names_raw) if isinstance(post_prune_step_names_raw, list) else []
    )
    if content_is_str and step_names_for_floor:
        skeleton_text = _extract_step_skeleton(content_text or "", step_names_for_floor)
        content_floor = len(skeleton_text.encode("utf-8"))
    else:
        skeleton_text = ""
        content_floor = 0

    def _compute_head_limit(value_projector: Any = None) -> int:
        probe_envelope = _build(0, True, value_projector=value_projector)
        probe_rendered = _finalize_envelope(probe_envelope, max_bytes=bound)
        probe_bytes = len(probe_rendered.encode("utf-8"))
        if not content_is_str:
            return 0
        return max(content_floor, bound - probe_bytes - 64)

    def _attempt_with_projector(value_projector: Any) -> str | None:
        if not content_is_str:
            rendered = _fits(_build(0, False, value_projector=value_projector))
            if rendered is not None:
                return rendered
            return None
        head_limit = _compute_head_limit(value_projector)
        if head_limit < content_floor:
            return None
        candidate = head_limit
        while candidate >= content_floor:
            rendered = _fits(_build(candidate, True, value_projector=value_projector))
            if rendered is not None:
                return rendered
            rendered = _fits(_build(candidate, False, value_projector=value_projector))
            if rendered is not None:
                return rendered
            if candidate == content_floor:
                break
            candidate = max(content_floor, candidate // 2)
        return None

    rendered = _attempt_with_projector(None)
    if rendered is not None:
        return rendered

    if not present_preserved:
        return None

    value_limit = max(16, bound // (len(present_preserved) + 2))
    while value_limit >= 16:

        def _project_with_limit(value: Any, _limit: int = value_limit) -> Any:
            projected, _chars, _items = _project_value(value, _limit)
            return projected

        rendered = _attempt_with_projector(_project_with_limit)
        if rendered is not None:
            return rendered
        value_limit //= 2

    minimal_envelope = _build(0, False, value_projector=_minimal_same_type)
    rendered = _fits(minimal_envelope)
    if rendered is not None:
        return rendered

    return None


def _spill_for_delivery_bound(
    result: Any,
    *,
    tool_name: str,
    config: OutputBudgetConfig,
    artifact_dir: Path | None,
    original: str,
    original_size: int,
    effective_delivery_token_limit: int,
    pre_published_artifact_path: str | None = None,
    pre_published_sha256: str | None = None,
) -> Any:
    """Persist ``original`` and return a bounded projection honoring the delivery bound.

    Used when an exempted or under-byte-budget payload still exceeds the
    downstream backend's effective delivery token limit. Mirrors the
    non-exempted spill machinery (atomic_write, _artifact_path,
    _project_json_object) so the caller sees the same envelope shape, with
    ``reason="delivery_bound"`` so downstream formatters distinguish it.

    When ``pre_published_artifact_path`` and ``pre_published_sha256`` are
    provided (recipe tool envelope path), the artifact is already on disk
    from the tool layer's pre-return persistence and this function only
    needs to build the projection.
    """
    if pre_published_artifact_path is not None and pre_published_sha256 is not None:
        published = pre_published_artifact_path
        content_sha256 = pre_published_sha256
    else:
        if artifact_dir is None:
            return bounded_response_budget_failure(
                result,
                cause="context_unavailable",
                tool_name=tool_name,
                max_bytes=config.response_max_bytes,
                original_utf8_bytes=original_size,
            )
        content_sha256 = hashlib.sha256(original.encode("utf-8")).hexdigest()
        path = _artifact_path(artifact_dir, tool_name, content_sha256)
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
        "sha256": content_sha256,
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
    bound = effective_delivery_token_limit * 4
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


def build_recipe_envelope(
    payload: dict[str, Any],
    *,
    artifact_path: str,
    sha256: str,
    bound: int,
    success: bool,
    kitchen: str,
    version: str,
    pull_tool: str = "get_recipe_section",
    step_index: dict[str, str] | None = None,
    step_flow_skeleton: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the compact recipe envelope returned by open_kitchen / load_recipe.

    The envelope is designed to fit the smallest registered backend delivery
    bound by construction (verified by CI fitness test). It carries:

    - Routing metadata (success, kitchen, version)
    - Orchestrator-required control-plane fields (ingredients_table,
      orchestration_rules, stop_step_semantics, errors, suggestions)
    - A step-flow skeleton with every post-prune step's routing edges
      (on_success, on_failure, on_result, on_context_limit) — the recipe's
      execution graph with no step body content
    - A step_index mapping each step name to its pull-tool identifier
    - artifact_path + sha256 for the pull tool to verify integrity
    - The pull_tool name documenting the retrieval surface

    The full recipe content and diagram are NOT in the envelope — they are
    retrieved on demand via ``get_recipe_section``. This is the structural
    fix that makes the smallest backend delivery bound a non-issue for
    recipe delivery.

    ``success``, ``kitchen``, ``version`` are caller-supplied because the
    upstream payload differs: ``open_kitchen`` uses ``build_open_kitchen_recipe_payload``
    to inject these; ``load_recipe`` returns the raw serve_recipe() result
    without them.
    """
    envelope: dict[str, Any] = {
        "success": success,
        "kitchen": kitchen,
        "version": version,
        "ingredients_table": payload.get("ingredients_table"),
        "orchestration_rules": payload.get("orchestration_rules"),
        "stop_step_semantics": payload.get("stop_step_semantics"),
        "errors": payload.get("errors", []),
        "suggestions": payload.get("suggestions", []),
        "step_flow_skeleton": step_flow_skeleton if step_flow_skeleton is not None else [],
        "step_index": step_index if step_index is not None else {},
        "artifact_path": artifact_path,
        "sha256": sha256,
        "pull_tool": pull_tool,
    }
    # Forward diagram field when explicitly present and not None, so callers that
    # rely on it (e.g. open_kitchen "diagram" key) still get the diagram — but the
    # canonical path for diagram retrieval is get_recipe_section(section="diagram").
    diagram_value = payload.get("diagram")
    if diagram_value is not None:
        envelope["diagram"] = diagram_value

    rendered = _canonical_json(envelope)
    if len(rendered.encode("utf-8")) > bound:
        raise _ProjectionNonconvergentError(
            f"envelope exceeds delivery bound: "
            f"bytes={len(rendered.encode('utf-8'))} bound={bound}. "
            "Envelope construction must keep the step_flow_skeleton small enough "
            "to fit by construction — extend the CI fitness guard, not this assert."
        )
    return envelope


def enforce_response_budget(
    result: Any,
    *,
    tool_name: str,
    artifact_dir: Path | None,
    config: OutputBudgetConfig,
    force_spill: bool = False,
    effective_delivery_token_limit: int | None = None,
) -> Any:
    """Return a bounded response of the same handler type.

    Oversized content is atomically persisted before a projection is returned.
    Artifact failure and missing-context cases fail closed without echoing the
    original payload.

    ``effective_delivery_token_limit`` is the worst-case operative bound on the
    downstream transport (e.g. Codex code-mode default ~10K). When set, payloads
    whose estimated token count exceeds it are spilled even if they pass the
    server-side exemption or response-byte ceilings, because the transport
    cannot deliver them at full size.
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
    if effective_delivery_token_limit == 0:
        effective_delivery_token_limit = resolve_worst_case_delivery_bound() or None
    over_delivery_bound = (
        effective_delivery_token_limit is not None
        and effective_delivery_token_limit > 0
        and _estimated_tokens(original_size) > effective_delivery_token_limit
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
        # Part B Step 2.1: recipe exempted tools (open_kitchen / load_recipe)
        # persist the full artifact at the tool layer BEFORE returning, so the
        # envelope already carries artifact_path + sha256. This layer does NOT
        # add unconditional persistence here — that broke tests where ctx has
        # no temp_dir. Only the spill path persists the artifact, matching
        # Part A's pre-remediation behavior.
        if over_delivery_bound:
            assert effective_delivery_token_limit is not None  # narrowed by over_delivery_bound
            return _spill_for_delivery_bound(
                result,
                tool_name=tool_name,
                config=config,
                artifact_dir=artifact_dir,
                original=original,
                original_size=original_size,
                effective_delivery_token_limit=effective_delivery_token_limit,
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

    content_sha256 = hashlib.sha256(original_bytes).hexdigest()
    path = _artifact_path(artifact_dir, tool_name, content_sha256)
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
        "sha256": content_sha256,
        "original_utf8_bytes": len(original_bytes),
    }

    delivery_bound_bytes = (
        effective_delivery_token_limit * 4
        if effective_delivery_token_limit is not None and effective_delivery_token_limit > 0
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
    effective_delivery_token_limit: int | None = None,
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
        effective_delivery_token_limit=effective_delivery_token_limit,
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
    "build_recipe_envelope",
    "emit_response_budget_failure",
    "enforce_response_budget",
    "extract_step_routing",
    "shape_json_response",
]
