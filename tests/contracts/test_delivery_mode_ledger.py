"""Delivery-mode ledger: pinned (recipe × backend) → delivery mode and size."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    CLAUDE_INJECTED_CLIENT_RESULT_TOKENS,
    FinalizedRecipeProjection,
    HostClientAttestation,
    RecipeDeliveryMode,
    client_serialized_char_len,
)
from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.pipeline.recipe_initialization import NoActiveRecipe
from autoskillit.recipe import load_and_validate
from autoskillit.server._recipe_delivery import (
    finalize_recipe_delivery,
    prepare_recipe_delivery_generation,
)
from autoskillit.server._recipe_generation import RecipeGenerationStore
from autoskillit.server.tools._serve_helpers import build_open_kitchen_recipe_payload
from tests.contracts.fixtures.recipes import BUNDLED_RECIPE_PATHS

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

# Pinned (recipe stem, backend) → delivery mode. Any change flipping a mode
# fails CI naming the exact pair — update this table only when the flip is
# intentional (e.g. a recipe grew/shrank past the delivery threshold).
_EXPECTED_MODES: dict[tuple[str, str], RecipeDeliveryMode] = {
    ("bem-wrapper", "claude-code"): RecipeDeliveryMode.ORDINARY_INLINE,
    ("bem-wrapper", "codex"): RecipeDeliveryMode.ENVELOPE,
    ("consolidate-health-reports", "claude-code"): RecipeDeliveryMode.ORDINARY_INLINE,
    ("consolidate-health-reports", "codex"): RecipeDeliveryMode.ORDINARY_INLINE,
    ("full-audit", "claude-code"): RecipeDeliveryMode.ORDINARY_INLINE,
    ("full-audit", "codex"): RecipeDeliveryMode.ENVELOPE,
    ("implement-findings", "claude-code"): RecipeDeliveryMode.ORDINARY_INLINE,
    ("implement-findings", "codex"): RecipeDeliveryMode.ENVELOPE,
    ("implementation-groups", "claude-code"): RecipeDeliveryMode.ENVELOPE,
    ("implementation-groups", "codex"): RecipeDeliveryMode.ENVELOPE,
    ("implementation", "claude-code"): RecipeDeliveryMode.ENVELOPE,
    ("implementation", "codex"): RecipeDeliveryMode.ENVELOPE,
    ("merge-prs", "claude-code"): RecipeDeliveryMode.ENVELOPE,
    ("merge-prs", "codex"): RecipeDeliveryMode.ENVELOPE,
    ("planner", "claude-code"): RecipeDeliveryMode.ORDINARY_INLINE,
    ("planner", "codex"): RecipeDeliveryMode.ENVELOPE,
    ("promote-to-main-wrapper", "claude-code"): RecipeDeliveryMode.ORDINARY_INLINE,
    ("promote-to-main-wrapper", "codex"): RecipeDeliveryMode.ENVELOPE,
    ("remediation", "claude-code"): RecipeDeliveryMode.ENVELOPE,
    ("remediation", "codex"): RecipeDeliveryMode.ENVELOPE,
    ("research-archive", "claude-code"): RecipeDeliveryMode.ORDINARY_INLINE,
    ("research-archive", "codex"): RecipeDeliveryMode.ENVELOPE,
    ("research-design", "claude-code"): RecipeDeliveryMode.ORDINARY_INLINE,
    ("research-design", "codex"): RecipeDeliveryMode.ENVELOPE,
    ("research-implement", "claude-code"): RecipeDeliveryMode.ORDINARY_INLINE,
    ("research-implement", "codex"): RecipeDeliveryMode.ENVELOPE,
    ("research-review", "claude-code"): RecipeDeliveryMode.ORDINARY_INLINE,
    ("research-review", "codex"): RecipeDeliveryMode.ENVELOPE,
    ("research", "claude-code"): RecipeDeliveryMode.ENVELOPE,
    ("research", "codex"): RecipeDeliveryMode.ENVELOPE,
}


@dataclass(frozen=True, slots=True)
class _ResolvedDelivery:
    mode: RecipeDeliveryMode
    rendered: str
    serialized_chars: int


def _resolve_delivery(
    recipe_path: Path,
    backend_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _ResolvedDelivery:
    from autoskillit.server import _recipe_generation

    monkeypatch.setattr(_recipe_generation, "_RECIPE_GENERATION_STORE", RecipeGenerationStore())
    project_root = Path(__file__).resolve().parents[2]
    loaded = load_and_validate(
        recipe_path.stem,
        project_dir=project_root,
        ingredient_overrides={
            "task": "test",
            "issue_url": "https://test/1",
            "source_dir": str(project_root),
        },
        include_finalized_projection=True,
    )
    projection = loaded.pop("_finalized_projection", None)
    assert isinstance(projection, FinalizedRecipeProjection)
    payload = build_open_kitchen_recipe_payload(dict(loaded), version="0.0.0")
    tool_ctx = cast(
        Any,
        SimpleNamespace(
            backend=BACKEND_REGISTRY[backend_name](),
            config=SimpleNamespace(output_budget=OutputBudgetConfig()),
            kitchen_id=f"ledger-{backend_name}-{recipe_path.stem}",
            recipe_execution_lock=threading.RLock(),
            recipe_initialization_state=NoActiveRecipe(),
            temp_dir=tmp_path,
        ),
    )
    prepared = prepare_recipe_delivery_generation(
        payload,
        recipe_name=recipe_path.stem,
        tool_ctx=tool_ctx,
        finalized_projection=projection,
    )
    # Claude sessions carry host attestation from the launcher; Codex does not.
    backend = BACKEND_REGISTRY[backend_name]()
    attestation = (
        HostClientAttestation(
            attested_client_gate_tokens=CLAUDE_INJECTED_CLIENT_RESULT_TOKENS,
            annotation_support=True,
        )
        if backend.capabilities.recipe_delivery_budget is None
        else None
    )
    finalized = finalize_recipe_delivery(
        payload,
        surface="open_kitchen",
        recipe_name=recipe_path.stem,
        tool_ctx=tool_ctx,
        finalized_projection=projection,
        flow_generation=prepared.flow_generation,
        canonical_artifact_payload=prepared.canonical_artifact_payload,
        execution_snapshot=prepared.execution_snapshot,
        normalized_compile_key=prepared.normalized_compile_key,
        host_client_attestation=attestation,
    )
    chars = client_serialized_char_len(finalized.rendered).value
    return _ResolvedDelivery(
        mode=finalized.decision.mode,
        rendered=finalized.rendered,
        serialized_chars=chars,
    )


@pytest.mark.parametrize("recipe_path", BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
@pytest.mark.parametrize("backend_name", sorted(BACKEND_REGISTRY), ids=lambda n: n)
def test_delivery_mode_is_pinned(
    recipe_path: Path,
    backend_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any change flipping a delivery mode must update this ledger."""
    resolved = _resolve_delivery(recipe_path, backend_name, tmp_path, monkeypatch)
    mode = resolved.mode
    assert mode in (
        RecipeDeliveryMode.ORDINARY_INLINE,
        RecipeDeliveryMode.ENVELOPE,
        RecipeDeliveryMode.ATTESTED_INLINE,
    ), f"{recipe_path.stem}/{backend_name}: unexpected mode {mode}"
    expected = _EXPECTED_MODES.get((recipe_path.stem, backend_name))
    assert expected is not None, (
        f"{recipe_path.stem}/{backend_name}: no pinned expectation in _EXPECTED_MODES — "
        "add one when introducing a new bundled recipe or backend"
    )
    assert mode == expected, (
        f"{recipe_path.stem}/{backend_name}: delivery mode changed from "
        f"{expected} to {mode} — update _EXPECTED_MODES if this flip is intentional"
    )


# Pinned (recipe stem, backend) → max client-serialized chars for inline recipes.
# After Stage F (projection removal), inline payloads are significantly smaller.
# Each stored maximum includes 10% headroom above the measured serialized char count.
# Update when a recipe grows/shrinks past these thresholds.
_EXPECTED_MAX_SERIALIZED_CHARS: dict[tuple[str, str], int] = {
    ("bem-wrapper", "claude-code"): 24_000,
    ("consolidate-health-reports", "claude-code"): 12_000,
    ("consolidate-health-reports", "codex"): 12_000,
    ("full-audit", "claude-code"): 37_000,
    ("implement-findings", "claude-code"): 34_000,
    ("planner", "claude-code"): 80_000,
    ("promote-to-main-wrapper", "claude-code"): 19_000,
    ("research-archive", "claude-code"): 31_000,
    ("research-design", "claude-code"): 67_000,
    ("research-implement", "claude-code"): 108_000,
    ("research-review", "claude-code"): 77_000,
}


@pytest.mark.parametrize("recipe_path", BUNDLED_RECIPE_PATHS, ids=lambda p: p.stem)
@pytest.mark.parametrize("backend_name", sorted(BACKEND_REGISTRY), ids=lambda n: n)
def test_inline_payload_serialized_size_is_pinned(
    recipe_path: Path,
    backend_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inline recipes must have client-serialized chars within pinned headroom.

    Stage F removed ~200K of finalized_recipe_projection from the wire,
    producing a measurable reduction in client-serialized payload size.
    This test pins the post-reduction sizes with headroom to detect
    unexpected growth (recipe bloat) or reduction (missing content).
    """
    resolved = _resolve_delivery(recipe_path, backend_name, tmp_path, monkeypatch)
    if resolved.mode != RecipeDeliveryMode.ORDINARY_INLINE:
        pytest.skip(f"{recipe_path.stem}/{backend_name} is ENVELOPE, no inline size pin")
    expected_max = _EXPECTED_MAX_SERIALIZED_CHARS.get((recipe_path.stem, backend_name))
    assert expected_max is not None, (
        f"{recipe_path.stem}/{backend_name}: no pinned size in _EXPECTED_MAX_SERIALIZED_CHARS — "
        "add one when introducing a new inline-delivered recipe or backend"
    )
    assert resolved.serialized_chars <= expected_max, (
        f"{recipe_path.stem}/{backend_name}: inline payload serialized chars "
        f"({resolved.serialized_chars:,}) exceeds pinned max ({expected_max:,}) — "
        "update _EXPECTED_MAX_SERIALIZED_CHARS if this growth is intentional"
    )
    # Sanity: inline payloads must also have meaningful content (not empty/trivial)
    assert resolved.serialized_chars > 1_000, (
        f"{recipe_path.stem}/{backend_name}: inline payload suspiciously small "
        f"({resolved.serialized_chars:,} chars)"
    )
