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
from typing import cast

import pytest

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    BackendCapabilities,
    resolve_effective_delivery_bound,
)
from autoskillit.execution.backends import BACKEND_REGISTRY, CODEX_TOOL_OUTPUT_TOKEN_LIMIT
from autoskillit.recipe import all_validated_recipe_names, load_and_validate
from autoskillit.server._response_budget import (
    RESPONSE_SPILL_METADATA_KEY,
    enforce_response_budget,
)
from autoskillit.server.tools._serve_helpers import build_open_kitchen_recipe_payload

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _recipe_names() -> list[str]:
    return sorted(all_validated_recipe_names(_PROJECT_ROOT))


def _backend_capabilities():
    return {name: cls().capabilities for name, cls in BACKEND_REGISTRY.items()}


def _effective_bound_bytes(bound_tokens: int) -> int:
    """Convert effective delivery token bound to UTF-8 byte ceiling (4 bytes/token)."""
    return bound_tokens * 4


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


@pytest.mark.parametrize("recipe_name", _recipe_names(), ids=lambda n: n)
def test_bundled_recipe_open_kitchen_fits_or_spills_per_backend(
    recipe_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For each backend, the ``open_kitchen`` payload either fits the
    effective delivery bound or spills to a projection that fits."""
    monkeypatch.chdir(tmp_path)
    payload = _full_open_kitchen_payload(recipe_name)
    serialized = json.dumps(payload)
    serialized_bytes = len(serialized.encode("utf-8"))
    for backend_name, caps in _backend_capabilities().items():
        bound_tokens = resolve_effective_delivery_bound(caps)
        bound_bytes = _effective_bound_bytes(bound_tokens)
        if serialized_bytes <= bound_bytes:
            continue
        result = enforce_response_budget(
            serialized,
            tool_name="open_kitchen",
            artifact_dir=tmp_path / backend_name,
            config=OutputBudgetConfig(),
            effective_delivery_token_limit=bound_tokens,
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


@pytest.mark.parametrize("recipe_name", _recipe_names(), ids=lambda n: n)
def test_delivery_bound_summary_carries_all_step_names(
    recipe_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When spilling occurs, every post-prune step name from the recipe's
    step graph must appear in the delivered envelope, and ``content`` must be
    non-empty. Regression guard for the bug where ``suggestions`` starves
    ``content`` to ``""`` and step names disappear from the bounded summary."""
    monkeypatch.chdir(tmp_path)
    payload = _full_open_kitchen_payload(recipe_name)
    serialized = json.dumps(payload)
    serialized_bytes = len(serialized.encode("utf-8"))
    step_names_raw = payload.get("post_prune_step_names")
    step_names = [
        str(name) for name in cast(list[object], step_names_raw or []) if isinstance(name, str)
    ]
    assert step_names, (
        f"{recipe_name}: payload missing post_prune_step_names; cannot assert step coverage"
    )
    for backend_name, caps in _backend_capabilities().items():
        bound_tokens = resolve_effective_delivery_bound(caps)
        bound_bytes = _effective_bound_bytes(bound_tokens)
        if serialized_bytes <= bound_bytes:
            continue
        result = enforce_response_budget(
            serialized,
            tool_name="open_kitchen",
            artifact_dir=tmp_path / backend_name,
            config=OutputBudgetConfig(),
            effective_delivery_token_limit=bound_tokens,
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
        # Every step name must appear somewhere in the envelope (content or
        # structured field). Suggestions are the deprioritized key that
        # starved content in issue #4304.
        envelope_text = json.dumps(data)
        for step_name in step_names:
            assert step_name in envelope_text, (
                f"{backend_name}: step name {step_name!r} missing from "
                f"spilled envelope for {recipe_name}"
            )


def test_codex_configured_limit_not_less_than_effective_delivery_bound() -> None:
    """Cross-relational invariant: the configured Codex tool-output token
    limit must not drift below the operative delivery bound, and must not
    drift below the upstream Codex code-mode default output bound (~10,000
    tokens, per issue #4300)."""
    codex_caps = BACKEND_REGISTRY["codex"]().capabilities
    codex_effective = resolve_effective_delivery_bound(codex_caps)
    assert codex_effective > 0
    assert CODEX_TOOL_OUTPUT_TOKEN_LIMIT >= codex_effective, (
        f"CODEX_TOOL_OUTPUT_TOKEN_LIMIT ({CODEX_TOOL_OUTPUT_TOKEN_LIMIT}) is "
        f"below Codex effective delivery bound ({codex_effective}); config "
        f"and capability field have drifted apart"
    )
    # Upstream floor: Codex code-mode default ~10,000 tokens.
    assert CODEX_TOOL_OUTPUT_TOKEN_LIMIT >= 10_000, (
        f"CODEX_TOOL_OUTPUT_TOKEN_LIMIT ({CODEX_TOOL_OUTPUT_TOKEN_LIMIT}) is "
        f"below the upstream Codex code-mode default output bound (10,000); "
        f"config value must not drift below this floor"
    )


def test_capability_default_uses_conservative_bound() -> None:
    """BackendCapabilities() with no effective_delivery_token_limit set must
    default to a conservative worst-case bound (the smallest registered
    backend bound). The historical 0-sentinel silently disabled delivery
    bounding for any future backend that omitted the field, leading to
    unbounded transport delivery."""
    caps = BackendCapabilities()
    bound = caps.effective_delivery_token_limit
    assert bound > 0, (
        f"BackendCapabilities() default effective_delivery_token_limit must "
        f"be conservative (non-zero); got {bound}"
    )
    # The default must be at most the smallest registered backend bound,
    # so the worst-case delivery is bounded to the strictest transport.
    min_registered = min(
        resolve_effective_delivery_bound(cls().capabilities) for cls in BACKEND_REGISTRY.values()
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
        effective_delivery_token_limit=bound_tokens,
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
