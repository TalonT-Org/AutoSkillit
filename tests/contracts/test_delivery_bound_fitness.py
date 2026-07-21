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

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import RESPONSE_BACKSTOP_EXEMPTION_REGISTRY, resolve_effective_delivery_bound
from autoskillit.execution import (
    CODEX_TOOL_OUTPUT_TOKEN_LIMIT,
    resolve_worst_case_delivery_bound,
)
from autoskillit.execution.backends import BACKEND_REGISTRY
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


@pytest.mark.parametrize("tool_name", ["open_kitchen", "load_recipe"])
@pytest.mark.parametrize("recipe_name", _recipe_names(), ids=lambda n: n)
def test_bundled_recipe_open_kitchen_fits_or_spills_per_backend(
    tool_name: str,
    recipe_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every recipe payload fits or spills with all surviving step names."""
    monkeypatch.chdir(tmp_path)
    payload = _full_recipe_payload(recipe_name, tool_name)
    serialized = json.dumps(payload)
    serialized_bytes = len(serialized.encode("utf-8"))
    for backend_name, caps in _backend_capabilities().items():
        bound_tokens = resolve_effective_delivery_bound(caps)
        bound_bytes = _effective_bound_bytes(bound_tokens)
        if serialized_bytes <= bound_bytes:
            data = payload
        else:
            result = enforce_response_budget(
                serialized,
                tool_name=tool_name,
                artifact_dir=tmp_path / tool_name / backend_name,
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

        delivered_content = data.get("content", "")
        assert isinstance(delivered_content, str)
        for step_name in payload.get("post_prune_step_names", []):
            assert step_name in delivered_content, (
                f"{tool_name}/{backend_name}/{recipe_name}: "
                f"post-prune step {step_name!r} missing from delivered content"
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
