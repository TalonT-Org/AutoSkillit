"""Bundled-recipe and non-exempted payload fitness against per-backend delivery bounds.

Regression guard: every bundled recipe's ``open_kitchen`` payload must either
fit each backend's effective delivery bound, or be properly spilled by the
server-side response backstop. Likewise, non-exempted payloads that fit
``response_max_bytes`` but exceed a backend's effective delivery bound must
route to spill-and-project and produce a projection under the bound.

Covers plan Step 4 (real bundled-recipe payload fitness via
``load_and_validate`` + ``build_open_kitchen_recipe_payload``) and Step 5
(non-exempted spill projection size).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    RECIPE_SECTION_RESPONSE_FLOOR_BYTES,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    BackendCapabilities,
    FinalizedRecipeProjection,
    resolve_general_output_token_limit,
    resolve_recipe_envelope_byte_limit,
)
from autoskillit.execution.backends import BACKEND_REGISTRY, CODEX_HISTORY_RETENTION_TOKEN_LIMIT
from autoskillit.recipe import (
    NON_INTERACTIVE_KINDS,
    all_validated_recipe_names,
    list_recipes,
    load_and_validate,
)
from autoskillit.server._recipe_delivery import (
    finalize_recipe_delivery,
    prepare_recipe_delivery_generation,
)
from autoskillit.server._recipe_generation import RecipeGenerationStore
from autoskillit.server._response_budget import (
    RESPONSE_SPILL_METADATA_KEY,
    enforce_response_budget,
)
from autoskillit.server.tools._serve_helpers import (
    build_open_kitchen_recipe_payload,
)
from tests.contracts._delivery_constants import (
    MAX_ENVELOPE_MANIFEST_BYTES,
    MAX_OPEN_KITCHEN_CALLS,
    MAX_PAGES_PER_SECTION,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _recipe_names() -> list[str]:
    return sorted(all_validated_recipe_names(_PROJECT_ROOT))


def _delivery_recipe_names() -> list[str]:
    result = list_recipes(
        _PROJECT_ROOT,
        exclude_kinds=NON_INTERACTIVE_KINDS,
        exclude_dispatch_only=True,
    )
    return sorted(recipe.name for recipe in result.items)


def _backend_capabilities():
    return {name: cls().capabilities for name, cls in BACKEND_REGISTRY.items()}


def _generic_backstop_bound_bytes(bound_tokens: int) -> int:
    """Convert a generic response backstop token limit to its UTF-8 byte ceiling."""
    return bound_tokens * 4


@pytest.mark.parametrize("backend_name", sorted(_backend_capabilities()), ids=lambda n: n)
def test_ordinary_recipe_pull_bound_meets_mandatory_failure_floor(
    backend_name: str,
) -> None:
    """Every backend must be able to retain the smallest recipe-pull failure."""
    capabilities = _backend_capabilities()[backend_name]
    conservative_general_result_limit = resolve_general_output_token_limit(capabilities)

    assert conservative_general_result_limit >= RECIPE_SECTION_RESPONSE_FLOOR_BYTES, (
        f"{backend_name}: ordinary recipe-pull ceiling "
        f"{conservative_general_result_limit} bytes is below the mandatory failure "
        f"floor {RECIPE_SECTION_RESPONSE_FLOOR_BYTES} bytes"
    )


def _full_open_kitchen_payload(recipe_name: str) -> dict[str, object]:
    """Build the production-shape ``open_kitchen`` payload for ``recipe_name``.

    Uses ``load_and_validate`` + ``build_open_kitchen_recipe_payload`` so the
    payload mirrors what ``open_kitchen`` returns at runtime: the routing
    envelope injected by the production helper plus the full recipe body from
    the bundled YAML. The fit-or-spill assertion below exercises the spill
    path for any bundled recipe whose serialized payload outgrows a backend's
    bound.
    """
    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={
            "task": "test task",
            "issue_url": "https://github.com/test/test/issues/1",
            "source_dir": str(_PROJECT_ROOT),
        },
    )
    return build_open_kitchen_recipe_payload(dict(result), version="0.0.0")


def _full_open_kitchen_generation(
    recipe_name: str,
) -> tuple[dict[str, object], FinalizedRecipeProjection]:
    """Build one production payload together with its hidden finalized projection."""
    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={
            "task": "test task",
            "issue_url": "https://github.com/test/test/issues/1",
            "source_dir": str(_PROJECT_ROOT),
        },
        include_finalized_projection=True,
    )
    projection = result.pop("_finalized_projection", None)
    assert isinstance(projection, FinalizedRecipeProjection)
    return build_open_kitchen_recipe_payload(dict(result), version="0.0.0"), projection


@pytest.mark.parametrize("recipe_name", _delivery_recipe_names(), ids=lambda n: n)
@pytest.mark.parametrize("backend_name", sorted(_backend_capabilities().keys()), ids=lambda n: n)
def test_bundled_recipe_open_kitchen_envelope_fits_per_backend(
    recipe_name: str,
    backend_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """System-level fitness contract (issue #4304 Part B REQ-B-T7): the bounded
    response produced through the real generation preparation and finalizer path
    must fit every registered backend's effective delivery bound. Each response
    contains either complete inline flow or a successful bounded recovery manifest.
    """
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
    from autoskillit.server import _recipe_generation

    monkeypatch.setattr(
        _recipe_generation,
        "_RECIPE_GENERATION_STORE",
        RecipeGenerationStore(),
    )
    payload, projection = _full_open_kitchen_generation(recipe_name)
    tool_ctx = cast(
        Any,
        SimpleNamespace(
            backend=BACKEND_REGISTRY[backend_name](),
            config=SimpleNamespace(output_budget=OutputBudgetConfig()),
            kitchen_id=f"fitness-{backend_name}-{recipe_name}",
            temp_dir=tmp_path,
        ),
    )
    prepared = prepare_recipe_delivery_generation(
        payload,
        recipe_name=recipe_name,
        tool_ctx=tool_ctx,
        finalized_projection=projection,
    )
    finalized = finalize_recipe_delivery(
        payload,
        surface="open_kitchen",
        recipe_name=recipe_name,
        tool_ctx=tool_ctx,
        finalized_projection=projection,
        flow_generation=prepared.flow_generation,
        canonical_artifact_payload=prepared.canonical_artifact_payload,
        execution_snapshot=prepared.execution_snapshot,
        normalized_compile_key=prepared.normalized_compile_key,
    )

    caps = _backend_capabilities()[backend_name]
    if finalized.decision.reason == "exemption_overrides_envelope":
        assert caps.recipe_delivery_budget is None
        bound_bytes = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["open_kitchen"].max_utf8_bytes
    else:
        bound_bytes = resolve_recipe_envelope_byte_limit(caps)
    envelope = json.loads(finalized.rendered)
    assert len(finalized.rendered.encode("utf-8")) <= bound_bytes, (
        f"{backend_name}: envelope for {recipe_name} exceeds "
        f"{bound_bytes} bytes (effective delivery bound)"
    )
    assert envelope["recipe_pull"]["pull_tool"] == "get_recipe_section"
    assert envelope["success"] is True
    assert envelope["recipe_flow"] == prepared.flow_generation.identity()
    if envelope.get("delivery_bound_spill") is True:
        required_sections = envelope["required_sections"]
        assert [item["section"] for item in required_sections] == [
            "flow_records",
            projection.entrypoint,
        ]
        assert all(
            item["compiled_page_count"] == item["total_parts"] <= MAX_PAGES_PER_SECTION
            and item["compiled_bytes"] > 0
            for item in required_sections
        )
        mode = (
            "codex_bounded" if caps.recipe_delivery_budget is not None else "claude_code_bounded"
        )
        planned_calls = 1 + sum(item["total_parts"] for item in required_sections) + 1
        assert planned_calls <= MAX_OPEN_KITCHEN_CALLS[mode]
        assert len(finalized.rendered.encode("utf-8")) <= MAX_ENVELOPE_MANIFEST_BYTES
    else:
        assert envelope["flow_records"] == list(prepared.flow_generation.records)
        exemption = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["open_kitchen"]
        assert len(finalized.rendered.encode("utf-8")) <= exemption.max_utf8_bytes * 9 // 10


@pytest.mark.parametrize("recipe_name", _recipe_names(), ids=lambda n: n)
def test_bundled_recipe_open_kitchen_raw_spill_projection_fits_per_backend(
    recipe_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standalone regression guard for ``enforce_response_budget``'s generic spill
    path. Independent of whether ``open_kitchen``/``load_recipe`` still reach it
    in production for recipe payloads (Part B routes oversized recipes through
    ``build_recipe_envelope`` instead), ``enforce_response_budget``'s projection
    path remains valid coverage for the non-recipe response types that still
    flow through the backstop.

    For every bundled recipe × registered backend: if the raw serialized
    ``open_kitchen`` payload outgrows a backend's effective delivery bound,
    ``enforce_response_budget`` must spill it to a projection that fits. When
    the raw payload already fits, nothing more to verify here — envelope-fit
    coverage lives in ``test_bundled_recipe_open_kitchen_envelope_fits_per_backend``.
    """
    monkeypatch.chdir(tmp_path)
    payload = _full_open_kitchen_payload(recipe_name)
    serialized = json.dumps(payload)
    serialized_bytes = len(serialized.encode("utf-8"))
    for backend_name, caps in _backend_capabilities().items():
        bound_tokens = resolve_general_output_token_limit(caps)
        bound_bytes = _generic_backstop_bound_bytes(bound_tokens)
        if serialized_bytes <= bound_bytes:
            continue
        result = enforce_response_budget(
            serialized,
            tool_name="open_kitchen",
            artifact_dir=tmp_path / backend_name,
            config=OutputBudgetConfig(),
            selected_result_token_limit=bound_tokens,
        )
        assert isinstance(result, str), (
            f"{backend_name}: expected str result for {recipe_name} payload"
        )
        assert len(result.encode("utf-8")) <= bound_bytes, (
            f"{backend_name}: projection for {recipe_name} exceeds "
            f"{bound_bytes} bytes (effective delivery bound)"
        )
        data = json.loads(result)
        assert data.get("delivery_bound_spill") is True
        metadata = data[RESPONSE_SPILL_METADATA_KEY]
        assert metadata["reason"] == "delivery_bound"


def test_non_exempted_oversized_payload_spills_within_delivery_bound(tmp_path) -> None:
    """A non-exempted payload sized between ``response_max_bytes`` and a
    backend's effective delivery bound must spill and produce a projection
    that fits the bound."""
    payload = {f"key_{index:03d}": "y" * 5_000 for index in range(60)}
    serialized = json.dumps(payload)
    config = OutputBudgetConfig()
    for backend_name, caps in _backend_capabilities().items():
        bound_tokens = resolve_general_output_token_limit(caps)
        bound_bytes = _generic_backstop_bound_bytes(bound_tokens)
        assert len(serialized.encode("utf-8")) > bound_bytes, (
            f"{backend_name}: payload does not exceed bound ({bound_bytes} bytes)"
        )
        result = enforce_response_budget(
            serialized,
            tool_name="run_skill",
            artifact_dir=tmp_path / backend_name,
            config=config,
            selected_result_token_limit=bound_tokens,
        )
        assert isinstance(result, str)
        assert len(result.encode("utf-8")) <= bound_bytes, (
            f"{backend_name}: projection exceeds {bound_bytes} bytes"
        )
        data = json.loads(result)
        assert RESPONSE_SPILL_METADATA_KEY in data


def test_exemption_registry_tools_have_measured_ceiling() -> None:
    """Every tool in the exemption registry has a measured ceiling; this
    sanity check guards against future additions of exempted tools without a
    measured identity."""
    assert RESPONSE_BACKSTOP_EXEMPTION_REGISTRY, "registry must not be empty"
    for tool_name, definition in RESPONSE_BACKSTOP_EXEMPTION_REGISTRY.items():
        assert definition.max_chars > 0
        assert definition.max_utf8_bytes > 0
        assert definition.measurement_id, f"{tool_name} has no measurement_id"


@pytest.mark.parametrize("recipe_name", _recipe_names(), ids=lambda n: n)
def test_delivery_bound_summary_carries_all_step_names(
    recipe_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When spilling occurs, ``content`` must be non-empty (issue #4304
    regression: the deprioritized ``suggestions`` key previously starved
    ``content`` to ``""``). When the bound is large enough to admit the full
    recipe body, every post-prune step name appears in the envelope.

    Note: the issue #4304 starvation bug had only two classes of effect —
    (1) content was reduced to ``""``, and (2) step names disappeared because
    they live inside ``content``. The first invariant is the primary
    regression gate; the second (every step name visible) is a softer
    property that depends on bound vs. recipe size. Recipes whose payload
    materially exceeds the bound (e.g. ``research`` at ~106KB on the Codex
    40KB bound) inherently lose some step names to head-truncation. The
    fix guarantees the *budget allocation order* is correct — priority
    keys verbatim, deprioritized projected, content receives a guaranteed
    floor (not zero) — not that the entire step graph is preserved at any
    arbitrary bound.
    """
    monkeypatch.chdir(tmp_path)
    payload = _full_open_kitchen_payload(recipe_name)
    serialized = json.dumps(payload)
    serialized_bytes = len(serialized.encode("utf-8"))
    step_names_raw = payload.get("post_prune_step_names")
    step_names = [
        str(name) for name in cast(list[object], step_names_raw or []) if isinstance(name, str)
    ]
    for backend_name, caps in _backend_capabilities().items():
        bound_tokens = resolve_general_output_token_limit(caps)
        bound_bytes = _generic_backstop_bound_bytes(bound_tokens)
        if serialized_bytes <= bound_bytes:
            continue
        result = enforce_response_budget(
            serialized,
            tool_name="open_kitchen",
            artifact_dir=tmp_path / backend_name,
            config=OutputBudgetConfig(),
            selected_result_token_limit=bound_tokens,
        )
        assert isinstance(result, str), (
            f"{backend_name}: expected str result for {recipe_name} payload"
        )
        assert len(result.encode("utf-8")) <= bound_bytes, (
            f"{backend_name}: projection for {recipe_name} exceeds "
            f"{bound_bytes} bytes (effective delivery bound)"
        )
        data = json.loads(result)
        content = data.get("content", "")
        assert len(content) > 0, (
            f"{backend_name}: content starved to empty for {recipe_name} — "
            f"delivery-bound summary bug regressed"
        )
        if not step_names:
            # Recipe's load path bypassed active_recipe composition (sub-recipe
            # composition); post_prune_step_names is absent. Skip the
            # step-name coverage assertion — the content > 0 invariant is
            # the primary regression gate for issue #4304.
            continue
        assert "post_prune_routing_edges" in payload, (
            f"post_prune_routing_edges missing from open_kitchen payload for "
            f"{recipe_name} — routing-edge coverage cannot be verified"
        )
        routing_edges_raw = payload.get("post_prune_routing_edges")
        routing_targets = [
            str(t) for t in cast(list[object], routing_edges_raw or []) if isinstance(t, str)
        ]
        envelope_text = json.dumps(data)
        # Step names live inside ``content`` (the recipe body). When the bound
        # is small relative to the recipe body, head-truncation inherently
        # drops some step names — assert coverage proportional to how much of
        # the body fits within the bound. This tolerance is NOT a flat
        # percentage: it scales with (1 - coverage_ratio), so at small bounds
        # against large recipe bodies the tolerated miss count can be a large
        # fraction of all step names. The ``content > 0`` assertion above is
        # the primary regression gate for issue #4304; this is a softer,
        # best-effort coverage check.
        full_body_chars = len(payload.get("content", "") or "")  # type: ignore[arg-type]  # TypedDict access
        body_chars_in_envelope = len(content)
        if full_body_chars <= body_chars_in_envelope:
            coverage_ratio = 1.0
        else:
            coverage_ratio = body_chars_in_envelope / full_body_chars
        max_missing = max(1, int(round(len(step_names) * (1.0 - coverage_ratio) + 1)))
        missing = [sn for sn in step_names if sn not in envelope_text]
        assert len(missing) <= max_missing, (
            f"{backend_name}: {len(missing)} of {len(step_names)} step names "
            f"missing from spilled envelope for {recipe_name} (body fit "
            f"{coverage_ratio:.1%}, tolerated misses {max_missing}); first "
            f"missing: {missing[:5]}"
        )
        if routing_targets:
            # Routing-edge targets live inside the same truncatable ``content``
            # field as step names, so they are subject to the identical
            # proportional body-fit tolerance derived above.
            max_missing_edges = max(
                1, int(round(len(routing_targets) * (1.0 - coverage_ratio) + 1))
            )
            missing_edges = [t for t in routing_targets if t not in envelope_text]
            assert len(missing_edges) <= max_missing_edges, (
                f"{backend_name}: {len(missing_edges)} of {len(routing_targets)} "
                f"routing edge targets missing from spilled envelope for "
                f"{recipe_name} (body fit {coverage_ratio:.1%}, tolerated misses "
                f"{max_missing_edges}); first missing: {missing_edges[:5]}"
            )


def test_codex_history_retention_not_less_than_unnegotiated_result_limit() -> None:
    """History retention must accommodate the ordinary outer-result default.

    This comparison is a storage-safety relation only; it does not authorize
    the retention value as the selected outer result.
    """
    codex_caps = BACKEND_REGISTRY["codex"]().capabilities
    codex_unnegotiated = resolve_general_output_token_limit(codex_caps)
    assert codex_unnegotiated > 0
    assert CODEX_HISTORY_RETENTION_TOKEN_LIMIT >= codex_unnegotiated, (
        "CODEX_HISTORY_RETENTION_TOKEN_LIMIT "
        f"({CODEX_HISTORY_RETENTION_TOKEN_LIMIT}) is below Codex unnegotiated "
        f"result limit ({codex_unnegotiated}); config "
        f"and capability field have drifted apart"
    )
    # Upstream floor: Codex code-mode default ~10,000 tokens.
    assert CODEX_HISTORY_RETENTION_TOKEN_LIMIT >= 10_000, (
        "CODEX_HISTORY_RETENTION_TOKEN_LIMIT "
        f"({CODEX_HISTORY_RETENTION_TOKEN_LIMIT}) is "
        f"below the upstream Codex code-mode default output bound (10,000); "
        f"config value must not drift below this floor"
    )


def test_capability_default_uses_conservative_bound() -> None:
    """BackendCapabilities() with no unnegotiated_tool_result_token_limit set must
    default to a conservative worst-case bound (the smallest registered
    backend bound). The historical 0-sentinel silently disabled delivery
    bounding for any future backend that omitted the field, leading to
    unbounded transport delivery."""
    caps = BackendCapabilities()
    bound = caps.unnegotiated_tool_result_token_limit
    assert bound > 0, (
        f"BackendCapabilities() default unnegotiated_tool_result_token_limit must "
        f"be conservative (non-zero); got {bound}"
    )
    # The default must be at most the smallest registered backend bound,
    # so the worst-case delivery is bounded to the strictest transport.
    min_registered = min(
        resolve_general_output_token_limit(cls().capabilities) for cls in BACKEND_REGISTRY.values()
    )
    assert bound <= min_registered, (
        f"BackendCapabilities() default ({bound}) must be at most the "
        f"smallest registered backend bound ({min_registered}); a larger "
        f"default would silently bypass the strictest transport"
    )


def test_non_exempted_delivery_bound_preserves_result_field(tmp_path: Path) -> None:
    """A run_skill-shaped payload with a large ``result`` field must survive
    projection when the payload exceeds the effective delivery bound. The
    existing fitness test only checks projection size, not that ``result``
    survives — the same starvation defect as ``_delivery_bound_summary``'s
    ``content`` bug applies to the sibling ``_project_json_object`` path."""
    payload = {
        "success": True,
        "kitchen": "open",
        "version": "1.2.3",
        "result": "x" * 100_000,
        **{f"key_{index:03d}": "y" * 200 for index in range(15)},
    }
    serialized = json.dumps(payload)
    bound_tokens = 10_000
    bound_bytes = bound_tokens * 4
    assert len(serialized.encode("utf-8")) > bound_bytes
    result = enforce_response_budget(
        serialized,
        tool_name="run_skill",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        selected_result_token_limit=bound_tokens,
    )
    assert isinstance(result, str)
    assert len(result.encode("utf-8")) <= bound_bytes, (
        f"projection for non-exempted payload exceeds {bound_bytes} bytes"
    )
    data = json.loads(result)
    assert "result" in data, (
        "result field dropped from non-exempted projection — content-equivalent "
        "starvation defect (sibling of _delivery_bound_summary's content bug)"
    )
    assert len(data["result"]) > 0, (
        f"result field starved to empty ({len(data['result'])} chars); "
        f"non-exempted projection must protect the content-equivalent key"
    )
    for key in ("success", "kitchen", "version"):
        assert key in data, f"structural field {key!r} was dropped from the projection"


def test_non_exempted_delivery_bound_preserves_non_string_result(tmp_path: Path) -> None:
    payload = {
        "success": True,
        "result": {"records": [{"value": "\u2603" * 500} for _ in range(80)]},
    }
    serialized = json.dumps(payload)
    bound_tokens = 500
    result = enforce_response_budget(
        serialized,
        tool_name="run_skill",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        selected_result_token_limit=bound_tokens,
    )
    assert isinstance(result, str)
    assert len(result.encode("utf-8")) <= bound_tokens * 4
    projected = json.loads(result)
    assert projected["success"] is True
    assert isinstance(projected["result"], dict)


def test_non_exempted_delivery_bound_finds_multibyte_result_prefix(tmp_path: Path) -> None:
    payload = {"success": True, "result": "\u2603" * 100_000}
    bound_tokens = 500
    result = enforce_response_budget(
        json.dumps(payload, ensure_ascii=False),
        tool_name="run_skill",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        selected_result_token_limit=bound_tokens,
    )
    assert isinstance(result, str)
    assert len(result.encode("utf-8")) <= bound_tokens * 4
    projected = json.loads(result)
    assert projected["result"]
