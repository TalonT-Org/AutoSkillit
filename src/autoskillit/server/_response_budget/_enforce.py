"""enforce_response_budget + checkpoint-segmented response shaping."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    CLIENT_CHARS_PER_TOKEN_POLICY,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    TokenLimit,
    atomic_write,
    get_logger,
)
from autoskillit.server._recipe_segment_delivery import (
    RECIPE_SEGMENT_MAX_BYTES,
    RecipeSegmentDeliveryError,
    build_post_effect_segment_failure,
)
from autoskillit.server._response_budget._primitives import (
    _bounded_tool_name,
    _canonical_json,
    _emit_response_budget_event,
    _estimated_tokens,
    _ProjectionNonconvergentError,
    _serialized,
)
from autoskillit.server._response_budget._projection import (
    _project_json_object,
    _spill_for_delivery_bound,
)
from autoskillit.server._response_budget._spill import (
    _artifact_path,
    _plain_spill_envelope,
    bounded_response_budget_failure,
)

if TYPE_CHECKING:
    from autoskillit.config import OutputBudgetConfig

logger = get_logger(__name__)


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
    segmented = _checkpoint_segmented_mapping(result)
    if segmented is not None:
        base, carrier = segmented
        return _enforce_checkpoint_segmented_response(
            result,
            base=base,
            carrier=carrier,
            tool_name=tool_name,
            artifact_dir=artifact_dir,
            config=config,
            force_spill=force_spill,
            selected_result_token_limit=selected_result_token_limit,
        )
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
        CLIENT_CHARS_PER_TOKEN_POLICY.to_chars(TokenLimit(selected_result_token_limit)).value
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


def _checkpoint_segmented_mapping(
    result: Any,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    parsed: Any = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (TypeError, ValueError, RecursionError):
            return None
    if not isinstance(parsed, dict):
        return None
    carrier = parsed.get("recipe_segment")
    if not isinstance(carrier, dict) or carrier.get("kind") not in {"success", "recovery"}:
        return None
    base = dict(parsed)
    base.pop("recipe_segment")
    return base, carrier


def _segment_failure_like(
    original: Any,
    carrier: dict[str, Any],
    *,
    tool_name: str,
) -> Any:
    try:
        failure = build_post_effect_segment_failure(carrier, tool_name=tool_name)
    except RecipeSegmentDeliveryError as exc:
        source_step = carrier.get("source_step", "")
        logger.warning(
            "recipe_segment_post_effect_carrier_dropped",
            tool_name=tool_name,
            source_step=source_step,
            error=str(exc),
        )
        failure = {
            "success": False,
            "subtype": "recipe_segment_post_effect_delivery_failure",
            "error": "Response shaping failed after the operation ran; do not repeat it.",
            "tool_name": tool_name,
            "step_name": source_step,
            "operation_already_ran": True,
            "do_not_repeat": True,
        }
    return _canonical_json(failure) if isinstance(original, str) else failure


def post_effect_recipe_segment_failure(result: Any, *, tool_name: str) -> Any | None:
    """Preserve the selected carrier when outer response enforcement itself fails."""
    segmented = _checkpoint_segmented_mapping(result)
    if segmented is None:
        return None
    _base, carrier = segmented
    return _segment_failure_like(result, carrier, tool_name=tool_name)


def _enforce_checkpoint_segmented_response(
    original: Any,
    *,
    base: dict[str, Any],
    carrier: dict[str, Any],
    tool_name: str,
    artifact_dir: Path | None,
    config: OutputBudgetConfig,
    force_spill: bool,
    selected_result_token_limit: int | None,
) -> Any:
    """Artifactize only the base mapping and preserve the checkpoint carrier whole."""
    combined = {**base, "recipe_segment": carrier}
    try:
        rendered = _canonical_json(combined)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return _segment_failure_like(original, carrier, tool_name=tool_name)
    rendered_size = len(rendered.encode("utf-8"))
    final_limit = RECIPE_SEGMENT_MAX_BYTES - 1
    over_delivery_bound = (
        selected_result_token_limit is not None
        and selected_result_token_limit > 0
        and _estimated_tokens(rendered_size) > selected_result_token_limit
    )
    if not force_spill and not over_delivery_bound and rendered_size <= final_limit:
        return rendered if isinstance(original, str) else combined
    if artifact_dir is None:
        return _segment_failure_like(original, carrier, tool_name=tool_name)

    base_input: Any = _canonical_json(base) if isinstance(original, str) else base
    shaped_base = enforce_response_budget(
        base_input,
        tool_name=tool_name,
        artifact_dir=artifact_dir,
        config=config,
        force_spill=True,
        selected_result_token_limit=selected_result_token_limit,
    )
    try:
        shaped_mapping = json.loads(shaped_base) if isinstance(shaped_base, str) else shaped_base
    except (TypeError, ValueError, RecursionError):
        return _segment_failure_like(original, carrier, tool_name=tool_name)
    if not isinstance(shaped_mapping, dict):
        return _segment_failure_like(original, carrier, tool_name=tool_name)
    shaped_error = shaped_mapping.get("error")
    if isinstance(shaped_error, str) and shaped_error.startswith("response_budget_"):
        return _segment_failure_like(original, carrier, tool_name=tool_name)

    recombined = {**shaped_mapping, "recipe_segment": carrier}
    try:
        final_rendered = _canonical_json(recombined)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return _segment_failure_like(original, carrier, tool_name=tool_name)
    final_size = len(final_rendered.encode("utf-8"))
    if final_size > final_limit or (
        selected_result_token_limit is not None
        and selected_result_token_limit > 0
        and _estimated_tokens(final_size) > selected_result_token_limit
    ):
        return _segment_failure_like(original, carrier, tool_name=tool_name)
    return final_rendered if isinstance(original, str) else recombined


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
