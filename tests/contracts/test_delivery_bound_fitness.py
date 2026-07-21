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

import pytest

from autoskillit import __version__
from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    dump_yaml_str,
    load_yaml,
    resolve_effective_delivery_bound,
)
from autoskillit.execution import (
    CODEX_TOOL_OUTPUT_TOKEN_LIMIT,
    resolve_worst_case_delivery_bound,
)
from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.recipe import all_validated_recipe_names, load_and_validate
from autoskillit.server._response_budget import (
    RESPONSE_SPILL_METADATA_KEY,
    _canonical_json,
    build_recipe_envelope,
    enforce_response_budget,
    extract_step_routing,
)
from autoskillit.server.tools._serve_helpers import build_open_kitchen_recipe_payload

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 64-char realistic placeholder — matches the length of a real deterministic
# artifact path (tool_name + sha256[:16] + .log under a temp_dir) so envelope
# byte-budget assertions reflect production-shaped sizes.
_PLACEHOLDER_ARTIFACT_PATH = "/" + "a" * 59 + ".log"


def _recipe_names() -> list[str]:
    return sorted(all_validated_recipe_names(_PROJECT_ROOT))


def _backend_capabilities():
    return {name: cls().capabilities for name, cls in BACKEND_REGISTRY.items()}


def _effective_bound_bytes(bound_tokens: int) -> int:
    """Convert effective delivery token bound to UTF-8 byte ceiling (4 bytes/token)."""
    return bound_tokens * 4


def _full_recipe_payload(recipe_name: str, tool_name: str) -> dict[str, object]:
    """Build the production payload for an exempted recipe-serving tool."""
    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={
            "task": "test task",
            "issue_url": "https://github.com/test/test/issues/1",
            "source_dir": str(_PROJECT_ROOT),
        },
    )
    payload = dict(result)
    if tool_name == "open_kitchen":
        return build_open_kitchen_recipe_payload(payload, version="0.0.0")
    assert tool_name == "load_recipe"
    return payload


def _envelope_for_recipe(
    recipe_name: str, tool_name: str, *, bound: int
) -> tuple[dict[str, object], dict[str, object]]:
    """Build the real payload and its envelope for a bundled recipe at a given bound.

    Returns ``(payload, envelope)``.
    """
    payload = _full_recipe_payload(recipe_name, tool_name)
    step_names = payload.get("post_prune_step_names", [])
    assert isinstance(step_names, list)
    skeleton = extract_step_routing(payload["content"], step_names)
    step_index = {name: f"step:{name}" for name in step_names}
    kitchen_label = "open" if tool_name == "open_kitchen" else "loaded"
    envelope = build_recipe_envelope(
        payload,
        artifact_path=_PLACEHOLDER_ARTIFACT_PATH,
        sha256="0" * 64,
        bound=bound,
        success=True,
        kitchen=kitchen_label,
        version=__version__,
        step_index=step_index,
        step_flow_skeleton=skeleton,
    )
    return payload, envelope


# Test A6
@pytest.mark.parametrize("recipe_name", _recipe_names(), ids=lambda n: n)
def test_open_kitchen_envelope_fits_smallest_backend_by_construction(recipe_name: str) -> None:
    """Every bundled recipe's open_kitchen envelope fits the worst-case backend bound
    by construction, and every post-prune step's routing edges survive into the
    step-flow skeleton."""
    bound = resolve_worst_case_delivery_bound() * 4
    payload, envelope = _envelope_for_recipe(recipe_name, "open_kitchen", bound=bound)
    rendered = _canonical_json(envelope)
    assert len(rendered.encode("utf-8")) <= bound, (
        f"{recipe_name}: envelope is {len(rendered.encode('utf-8'))} bytes, "
        f"exceeds worst-case bound {bound}"
    )

    step_names = payload.get("post_prune_step_names", [])
    assert isinstance(step_names, list)
    skeleton_by_name = {entry["name"]: entry for entry in envelope["step_flow_skeleton"]}
    parsed_content = load_yaml(payload["content"])
    steps_obj = parsed_content.get("steps", {}) if isinstance(parsed_content, dict) else {}
    routing_fields = ("on_success", "on_failure", "on_result", "on_context_limit")
    for step_name in step_names:
        assert step_name in skeleton_by_name, (
            f"{recipe_name}: step {step_name!r} missing from step_flow_skeleton"
        )
        assert step_name in envelope["step_index"], (
            f"{recipe_name}: step {step_name!r} missing from step_index"
        )
        step_obj = steps_obj.get(step_name)
        if not isinstance(step_obj, dict):
            continue
        entry = skeleton_by_name[step_name]
        for field in routing_fields:
            if field in step_obj:
                assert entry.get(field) == step_obj[field], (
                    f"{recipe_name}/{step_name}: routing edge {field!r} "
                    "missing or mismatched in skeleton"
                )


# Test A7
@pytest.mark.parametrize("recipe_name", _recipe_names(), ids=lambda n: n)
def test_envelope_step_index_covers_all_steps(recipe_name: str) -> None:
    """step_index covers exactly the post-prune step set via the step:{name} scheme."""
    bound = resolve_worst_case_delivery_bound() * 4
    payload, envelope = _envelope_for_recipe(recipe_name, "open_kitchen", bound=bound)
    step_names = payload.get("post_prune_step_names", [])
    assert isinstance(step_names, list)

    assert set(envelope["step_index"]) == set(step_names)
    for name, identifier in envelope["step_index"].items():
        assert identifier == f"step:{name}"

    parsed_content = load_yaml(payload["content"])
    steps_obj = parsed_content.get("steps", {}) if isinstance(parsed_content, dict) else {}
    for name in step_names:
        assert steps_obj.get(name), f"{recipe_name}: step {name!r} has an empty YAML block"


# Test A8
@pytest.mark.parametrize("recipe_name", _recipe_names(), ids=lambda n: n)
def test_largest_step_fits_pull_bound(recipe_name: str) -> None:
    """Every bundled recipe's largest single-step YAML block fits the pull-tool bound,
    or is losslessly reconstructible via chunked retrieval when it doesn't."""
    payload = _full_recipe_payload(recipe_name, "open_kitchen")
    parsed_content = load_yaml(payload["content"])
    steps_obj = parsed_content.get("steps", {}) if isinstance(parsed_content, dict) else {}
    bound = resolve_worst_case_delivery_bound() * 4

    serialized_steps: dict[str, str] = {}
    sizes: dict[str, int] = {}
    for name, step_obj in steps_obj.items():
        serialized = dump_yaml_str({"steps": {name: step_obj}}, default_flow_style=False)
        serialized_steps[name] = serialized
        sizes[name] = len(serialized.encode("utf-8"))

    if not sizes:
        return

    largest_name = max(sizes, key=lambda name: sizes[name])
    largest_size = sizes[largest_name]
    assert largest_size <= bound, (
        f"{recipe_name}: step {largest_name!r} is {largest_size} bytes, exceeds pull bound {bound}"
    )

    if largest_size > bound:
        # Defensive: only exercised if a bundled recipe's step ever exceeds the
        # worst-case pull bound. Mirrors get_recipe_section's chunking scheme
        # (tools_recipe._chunk_response_if_oversized) and asserts lossless
        # reconstruction via concatenation.
        content_text = serialized_steps[largest_name]
        chunk_size = max(1024, bound - 512)
        total = len(content_text)
        chunks = max(1, (total + chunk_size - 1) // chunk_size)
        reconstructed = "".join(
            content_text[part * chunk_size : (part + 1) * chunk_size] for part in range(chunks)
        )
        assert reconstructed == content_text


# Test A9
@pytest.mark.parametrize("tool_name", ["open_kitchen", "load_recipe"])
@pytest.mark.parametrize("recipe_name", _recipe_names(), ids=lambda n: n)
def test_bundled_recipe_envelope_fits_per_backend(
    tool_name: str,
    recipe_name: str,
) -> None:
    """Every recipe's envelope fits every registered backend's positive delivery bound,
    with every post-prune step name covered by the step-flow skeleton and step_index."""
    for backend_name, caps in _backend_capabilities().items():
        bound_tokens = resolve_effective_delivery_bound(caps)
        if bound_tokens <= 0:
            continue
        bound_bytes = _effective_bound_bytes(bound_tokens)
        payload, envelope = _envelope_for_recipe(recipe_name, tool_name, bound=bound_bytes)
        rendered = _canonical_json(envelope)
        assert len(rendered.encode("utf-8")) <= bound_bytes, (
            f"{backend_name}: envelope for {tool_name}/{recipe_name} exceeds {bound_bytes} bytes"
        )

        step_names = payload.get("post_prune_step_names", [])
        assert isinstance(step_names, list)
        skeleton_names = {entry["name"] for entry in envelope["step_flow_skeleton"]}
        for step_name in step_names:
            assert step_name in skeleton_names, (
                f"{tool_name}/{backend_name}/{recipe_name}: "
                f"post-prune step {step_name!r} missing from step_flow_skeleton"
            )
            assert step_name in envelope["step_index"], (
                f"{tool_name}/{backend_name}/{recipe_name}: "
                f"post-prune step {step_name!r} missing from step_index"
            )


def test_non_exempted_oversized_payload_spills_within_delivery_bound(tmp_path) -> None:
    """A non-exempted payload sized between ``response_max_bytes`` and a
    backend's effective delivery bound must spill and produce a projection
    that fits the bound."""
    payload = {f"key_{index:03d}": "y" * 5_000 for index in range(60)}
    serialized = json.dumps(payload)
    config = OutputBudgetConfig()
    for backend_name, caps in _backend_capabilities().items():
        bound_tokens = resolve_effective_delivery_bound(caps)
        bound_bytes = _effective_bound_bytes(bound_tokens)
        assert len(serialized.encode("utf-8")) > bound_bytes, (
            f"{backend_name}: payload does not exceed bound ({bound_bytes} bytes)"
        )
        result = enforce_response_budget(
            serialized,
            tool_name="run_skill",
            artifact_dir=tmp_path / backend_name,
            config=config,
            effective_delivery_token_limit=bound_tokens,
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


def test_cross_threshold_relationships() -> None:
    worst_case = resolve_worst_case_delivery_bound()
    assert worst_case > 0
    for backend_name, caps in _backend_capabilities().items():
        enforcement_bound = resolve_effective_delivery_bound(caps)
        if enforcement_bound > 0:
            assert CODEX_TOOL_OUTPUT_TOKEN_LIMIT >= enforcement_bound, (
                f"{backend_name}: configured tool-output capacity is below "
                f"the runtime enforcement bound"
            )

    max_exemption_bytes = max(
        definition.max_utf8_bytes for definition in RESPONSE_BACKSTOP_EXEMPTION_REGISTRY.values()
    )
    assert CODEX_TOOL_OUTPUT_TOKEN_LIMIT == ((max_exemption_bytes + 3) // 4) + 8_000


@pytest.mark.parametrize("tool_name", sorted(RESPONSE_BACKSTOP_EXEMPTION_REGISTRY))
def test_exemption_ceiling_covers_all_bundled_recipe_payloads(tool_name: str) -> None:
    exemption = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY[tool_name]
    measured = [
        json.dumps(_full_recipe_payload(recipe_name, tool_name)) for recipe_name in _recipe_names()
    ]
    assert max(len(payload) for payload in measured) <= exemption.max_chars
    assert max(len(payload.encode("utf-8")) for payload in measured) <= exemption.max_utf8_bytes
