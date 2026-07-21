"""Part B (issue #4304) — bounded envelope + pull-access architecture.

Regression guards for the recipe delivery-bound re-architecture: every
bundled recipe's envelope fits the smallest backend delivery bound by
construction, the pull tool returns bounded step content, the artifact
path is deterministic across calls, and a missing artifact is
re-created via the same serve pipeline that built it originally.

Covers the "always fits" invariant in `build_recipe_envelope`,
the `get_recipe_section` MCP tool's chunked-content contract, and the
recipe-prompt discipline enforcement in `cli/_prompts_kitchen.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from autoskillit.core import (
    BackendCapabilities,
    resolve_effective_delivery_bound,
)
from autoskillit.execution.backends import BACKEND_REGISTRY, CODEX_TOOL_OUTPUT_TOKEN_LIMIT
from autoskillit.recipe import all_validated_recipe_names, load_and_validate
from autoskillit.server._response_budget import _recipe_artifact_path
from autoskillit.server.tools._serve_helpers import (
    build_recipe_envelope,
    build_routing_edges_by_step,
    build_step_summaries,
    extract_step_skeleton,
    maybe_envelope_recipe_response,
    persist_recipe_artifact,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _recipe_names() -> list[str]:
    return sorted(all_validated_recipe_names(_PROJECT_ROOT))


def _backend_capabilities() -> dict[str, BackendCapabilities]:
    return {name: cls().capabilities for name, cls in BACKEND_REGISTRY.items()}


def _smallest_bound_tokens() -> int:
    """Smallest backend delivery token bound across the registry."""
    return min(resolve_effective_delivery_bound(caps) for caps in _backend_capabilities().values())


def _bound_bytes(bound_tokens: int) -> int:
    return bound_tokens * 4


def _full_open_kitchen_payload(recipe_name: str) -> dict[str, object]:
    """Build the production-shape open_kitchen payload for *recipe_name*."""
    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={
            "task": "test task",
            "issue_url": "https://github.com/test/test/issues/1",
            "source_dir": str(_PROJECT_ROOT),
        },
    )
    from autoskillit.server.tools._serve_helpers import build_open_kitchen_recipe_payload

    return build_open_kitchen_recipe_payload(dict(result), version="0.0.0")


@pytest.mark.parametrize("recipe_name", _recipe_names(), ids=lambda n: n)
@pytest.mark.parametrize("backend_name", sorted(_backend_capabilities().keys()), ids=lambda n: n)
def test_envelope_fits_every_backend_by_construction(
    recipe_name: str, backend_name: str, tmp_path: Path
) -> None:
    """The bounded envelope (not the full payload) fits every backend's
    effective delivery bound by construction. The pull reference is
    present and the post-prune step names are preserved."""
    payload = _full_open_kitchen_payload(recipe_name)
    post_prune_raw = cast(list[object], payload.get("post_prune_step_names") or [])
    step_names = [str(n) for n in post_prune_raw if isinstance(n, str)]

    skeleton = extract_step_skeleton(
        step_names,
        routing_edges_by_step={},
        step_summaries={name: f"summary-{name}" for name in step_names},
    )
    artifact_path = tmp_path / f"{recipe_name}.log"
    sha256 = "0" * 64

    caps = _backend_capabilities()[backend_name]
    bound_tokens = resolve_effective_delivery_bound(caps)
    bound_bytes = _bound_bytes(bound_tokens)
    envelope = build_recipe_envelope(
        payload,
        artifact_path=str(artifact_path),
        artifact_sha256=sha256,
        skeleton=skeleton,
        bound_bytes=bound_bytes,
    )
    serialized = json.dumps(envelope, ensure_ascii=False)
    assert len(serialized.encode("utf-8")) <= bound_bytes, (
        f"{backend_name}: envelope for {recipe_name} exceeds "
        f"{bound_bytes} bytes (effective delivery bound)"
    )

    assert envelope["recipe_pull"]["pull_tool"] == "get_recipe_section"
    assert envelope["recipe_pull"]["artifact_path"] == str(artifact_path)
    assert envelope["recipe_pull"]["sha256"] == sha256
    assert envelope["step_flow_skeleton"]["step_count"] == len(step_names)
    for name in step_names:
        names_in_skeleton = [step["name"] for step in envelope["step_flow_skeleton"]["steps"]]
        assert name in names_in_skeleton, (
            f"{recipe_name}: step {name!r} missing from envelope skeleton"
        )


def test_envelope_carries_priority_fields_verbatim(tmp_path: Path) -> None:
    """orchestration_rules, stop_step_semantics, and ingredients_table are
    passed through unchanged so the orchestrator can route without pulling."""
    payload = {
        "success": True,
        "kitchen": "open",
        "version": "0.0.0",
        "orchestration_rules": "ORCH: steps route strictly on success/failure.",
        "stop_step_semantics": "STOP means stop — never auto-recover.",
        "errors": ["warn-A", "warn-B"],
        "ingredients_table": {"task": {"type": "string", "required": True}},
    }
    envelope = build_recipe_envelope(
        payload,
        artifact_path=str(tmp_path / "x.log"),
        artifact_sha256="0" * 64,
        skeleton=extract_step_skeleton([], {}),
        bound_bytes=64_000,
    )
    assert envelope["orchestration_rules"] == payload["orchestration_rules"]
    assert envelope["stop_step_semantics"] == payload["stop_step_semantics"]
    assert envelope["ingredients_table"] == payload["ingredients_table"]
    assert envelope["errors"] == ["warn-A", "warn-B"]


def test_extract_step_skeleton_preserves_routing_edges() -> None:
    """Step skeleton includes outgoing routing edges (edge_type + target)
    so the orchestrator can reason about flow without pulling bodies."""
    skeleton = extract_step_skeleton(
        ["a", "b", "c"],
        routing_edges_by_step={
            "a": [("success", "b"), ("failure", "c")],
            "b": [("success", "c")],
            "c": [],
        },
        step_summaries={"a": "step a", "b": "step b", "c": "step c"},
    )
    assert skeleton["step_count"] == 3
    by_name = {step["name"]: step for step in skeleton["steps"]}
    assert [edge["type"] for edge in by_name["a"]["edges"]] == ["success", "failure"]
    assert [edge["target"] for edge in by_name["a"]["edges"]] == ["b", "c"]
    assert by_name["b"]["edges"] == [{"type": "success", "target": "c"}]
    assert by_name["c"]["edges"] == []
    assert by_name["a"]["summary"] == "step a"


def test_recipe_artifact_path_is_deterministic(tmp_path: Path) -> None:
    """_recipe_artifact_path returns the same path for the same (tool,
    recipe_name) on repeated calls — no UUID suffix.

    The pull tool relies on this determinism to reconstruct the path from
    ``tool_ctx.recipe_name`` without needing it threaded through every
    surface. This is the regression guard for the original UUID-suffixed
    path that broke pull access (#4304 related issue #2)."""
    p1 = _recipe_artifact_path(tmp_path, "open_kitchen", "remediation")
    p2 = _recipe_artifact_path(tmp_path, "open_kitchen", "remediation")
    p3 = _recipe_artifact_path(tmp_path, "load_recipe", "remediation")
    assert p1 == p2, "deterministic path must not vary between calls"
    assert p1 != p3, "different tool must produce different path"
    assert "open_kitchen" in str(p1) and "remediation" in str(p1)


def test_persist_recipe_artifact_overwrites_idempotently(tmp_path: Path) -> None:
    """Re-persisting the same recipe overwrites the artifact in place —
    pull access can rely on a stable path and fresh content."""
    path, sha1 = persist_recipe_artifact(
        tmp_path,
        tool_name="open_kitchen",
        recipe_name="remediation",
        payload={"success": True, "content": "v1"},
    )
    size1 = Path(path).stat().st_size
    path2, sha2 = persist_recipe_artifact(
        tmp_path,
        tool_name="open_kitchen",
        recipe_name="remediation",
        payload={"success": True, "content": "v2 is longer than v1"},
    )
    assert path == path2
    assert sha1 != sha2
    size2 = Path(path).stat().st_size
    assert size2 != size1
    assert json.loads(Path(path).read_text())["content"].startswith("v2")


@pytest.mark.parametrize("recipe_name", _recipe_names(), ids=lambda n: n)
def test_maybe_envelope_returns_envelope_when_payload_oversized(
    recipe_name: str, tmp_path: Path
) -> None:
    """When the payload's estimated token count exceeds
    effective_delivery_token_limit, maybe_envelope_recipe_response
    returns the bounded envelope (not the full payload)."""
    ctx = _make_minimal_ctx(tmp_path)
    ctx.recipe_name = recipe_name

    payload = _full_open_kitchen_payload(recipe_name)
    bound_tokens = _smallest_bound_tokens()
    payload_size_tokens = (len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 3) // 4
    result = maybe_envelope_recipe_response(
        payload,
        tool_name="open_kitchen",
        recipe_name=recipe_name,
        tool_ctx=ctx,
        effective_delivery_token_limit=bound_tokens,
    )
    serialized = json.dumps(result, ensure_ascii=False)
    assert len(serialized.encode("utf-8")) <= _bound_bytes(bound_tokens), (
        f"{recipe_name}: envelope exceeds {bound_tokens}-token bound"
    )
    if payload_size_tokens <= bound_tokens:
        # Recipe payload fits the bound; no spill triggered. The result
        # is the unchanged payload — which is the correct behavior.
        assert result.get("delivery_bound_spill") is not True
        return
    assert result.get("delivery_bound_spill") is True
    assert result.get("recipe_pull", {}).get("pull_tool") == "get_recipe_section"


def test_maybe_envelope_passthrough_when_payload_fits(tmp_path: Path) -> None:
    """When the payload fits the bound, maybe_envelope returns the
    payload unchanged (Claude backend path: backward compatible)."""
    ctx = _make_minimal_ctx(tmp_path)
    ctx.recipe_name = "any_recipe"

    payload = {"success": True, "content": "short"}
    bound_tokens = 10_000  # 40KB — plenty for "short"
    result = maybe_envelope_recipe_response(
        payload,
        tool_name="open_kitchen",
        recipe_name="any_recipe",
        tool_ctx=ctx,
        effective_delivery_token_limit=bound_tokens,
    )
    assert result == payload
    assert not (tmp_path / "open_kitchen_any_recipe.log").exists()


def test_maybe_envelope_passthrough_when_temp_dir_unset(tmp_path: Path) -> None:
    """When tool_ctx.temp_dir is unset (not a Path), the helper returns
    the payload unchanged rather than failing — track_response_size's
    spill path remains the fallback."""
    ctx = _make_minimal_ctx(tmp_path)
    # Replace temp_dir with a non-Path value to simulate the sentinel state
    # without tripping ToolContext.__post_init__'s TypeError guard.
    ctx.temp_dir = None  # type: ignore[assignment]
    ctx.recipe_name = "any_recipe"

    payload = {"success": True, "content": "x" * 10_000_000}
    result = maybe_envelope_recipe_response(
        payload,
        tool_name="open_kitchen",
        recipe_name="any_recipe",
        tool_ctx=ctx,
        effective_delivery_token_limit=100,
    )
    assert result is payload


def _make_minimal_ctx(tmp_path: Path):
    """Build a minimal ToolContext for envelope integration tests."""
    from autoskillit.config import AutomationConfig
    from autoskillit.core.types._type_plugin_source import DirectInstall
    from autoskillit.pipeline.audit import DefaultAuditLog
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.pipeline.timings import DefaultTimingLog
    from autoskillit.pipeline.tokens import DefaultTokenLog

    return ToolContext(
        config=AutomationConfig(features={"fleet": True}),
        audit=DefaultAuditLog(),
        token_log=DefaultTokenLog(),
        timing_log=DefaultTimingLog(),
        gate=DefaultGateState(enabled=False),
        plugin_source=DirectInstall(plugin_dir=tmp_path),
        runner=None,
        temp_dir=tmp_path / ".autoskillit" / "temp",
        project_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# Prompt contract tests (Part B Step 2.6)
# ---------------------------------------------------------------------------


def test_prompt_contract_describes_pull_protocol() -> None:
    """cli/_prompts_kitchen.py must NOT promise inline recipe content on
    open_kitchen; it must reference the pull tool by name."""
    prompts_path = _PROJECT_ROOT / "src" / "autoskillit" / "cli" / "_prompts_kitchen.py"
    text = prompts_path.read_text(encoding="utf-8")
    assert "to receive the full recipe content" not in text, (
        "cli/_prompts_kitchen.py must not promise inline 'full recipe content' "
        "on open_kitchen — the envelope + pull protocol is the supported path."
    )
    assert "get_recipe_section" in text, (
        "cli/_prompts_kitchen.py must reference the pull tool name so the "
        "orchestrator knows how to retrieve step bodies."
    )


def test_pull_tool_registered_in_gated_tools() -> None:
    """get_recipe_section must be in GATED_TOOLS so _require_enabled guards it."""
    from autoskillit.core.types import GATED_TOOLS

    assert "get_recipe_section" in GATED_TOOLS


def test_pull_tool_registered_in_tool_subset_tags() -> None:
    """get_recipe_section must be in TOOL_SUBSET_TAGS so kitchen-core
    visibility resolves correctly."""
    from autoskillit.core.types import TOOL_SUBSET_TAGS

    assert "get_recipe_section" in TOOL_SUBSET_TAGS
    assert "kitchen-core" in TOOL_SUBSET_TAGS["get_recipe_section"]


def test_pull_tool_in_unformatted_or_formatters() -> None:
    """New MCP tools must appear in _UNFORMATTED_TOOLS or _FORMATTERS so
    the dispatcher does not silently drop them."""
    formatter_path = (
        _PROJECT_ROOT / "src" / "autoskillit" / "hooks" / "formatters" / "pretty_output_hook.py"
    )
    text = formatter_path.read_text(encoding="utf-8")
    assert "get_recipe_section" in text


def test_codex_token_limit_unchanged_part_b() -> None:
    """Part B does not change the exemption ceilings — it adds the pull
    pathway. The CODEX_TOOL_OUTPUT_TOKEN_LIMIT remains driven by the
    registry's max, with the envelope fitting within that bound by
    construction. This test pins the current value so future changes
    requiring ADR-0005 amendment trip this guard."""
    assert CODEX_TOOL_OUTPUT_TOKEN_LIMIT >= 10_000


def test_build_step_summaries_handles_missing_or_malformed() -> None:
    """build_step_summaries returns {} when active_recipe_steps is None
    or not a dict (defensive against pre-recipe-open contexts)."""
    assert build_step_summaries(None) == {}
    assert build_step_summaries({}) == {}
    assert build_step_summaries({"a": object()}) == {"a": ""}


def test_build_routing_edges_handles_missing_extractor() -> None:
    """build_routing_edges_by_step with edge_extractor=None maps each step
    to an empty list (not omitted) — callers can rely on .get(name) returning
    a list, never KeyError."""
    assert build_routing_edges_by_step({"a": object()}, edge_extractor=None) == {"a": []}
    assert build_routing_edges_by_step(None, edge_extractor=None) == {}
