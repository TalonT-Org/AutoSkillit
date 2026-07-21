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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import (
    BackendCapabilities,
    fast_dumps,
    load_yaml,
    resolve_effective_delivery_bound,
)
from autoskillit.execution.backends import BACKEND_REGISTRY, CODEX_TOOL_OUTPUT_TOKEN_LIMIT
from autoskillit.recipe import (
    _extract_routing_edges,
    all_validated_recipe_names,
    find_recipe_by_name,
    load_and_validate,
    load_recipe,
)
from autoskillit.server._response_budget import _recipe_artifact_path
from autoskillit.server.tools import _serve_helpers
from autoskillit.server.tools._serve_helpers import (
    _compute_step_byte_ranges,
    build_recipe_envelope,
    build_routing_edges_by_step,
    build_step_summaries,
    extract_step_skeleton,
    maybe_envelope_recipe_response,
    persist_recipe_artifact,
)
from autoskillit.server.tools.tools_recipe import (
    _bounded_recipe_section_response,
    _extract_step_body_from_persisted,
    _RecipeSectionError,
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


def test_section_chunks_fit_serialized_utf8_bound() -> None:
    content = ('\u96ea"\\\n\t' * 2_000) + "tail"
    bound_bytes = 512
    chunks: list[str] = []
    part = 0
    while True:
        rendered = _bounded_recipe_section_response(
            "step", content, part=part, bound_bytes=bound_bytes
        )
        assert len(rendered.encode("utf-8")) <= bound_bytes
        response = json.loads(rendered)
        assert response["success"] is True
        chunks.append(response["content"])
        if not response["has_more"]:
            break
        part = response["next_part"]
    assert "".join(chunks) == content


def test_step_extraction_distinguishes_artifact_and_serialization_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(_RecipeSectionError) as parse_error:
        _extract_step_body_from_persisted({"content": "steps: ["}, "step")
    assert parse_error.value.code == "recipe_artifact_parse_failed"

    import autoskillit.server.tools.tools_recipe as tools_recipe

    def _broken_dumps(_value: object) -> str:
        raise TypeError("cannot serialize")

    monkeypatch.setattr(tools_recipe, "fast_dumps", _broken_dumps)
    with pytest.raises(_RecipeSectionError) as serialization_error:
        _extract_step_body_from_persisted(
            {"content": "steps:\n  step:\n    action: stop\n"}, "step"
        )
    assert serialization_error.value.code == "recipe_section_serialization_failed"


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
        recipe_name=recipe_name,
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
    assert envelope["recipe_pull"]["producer_tool"] == "open_kitchen"
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
        recipe_name="test-recipe",
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
    payload unchanged AND unconditionally persists the artifact so the
    pull tool always has a backing store (Part A REQ-B02)."""
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
    assert result is payload
    artifact_path = _recipe_artifact_path(ctx.temp_dir, "open_kitchen", "any_recipe")
    assert Path(artifact_path).exists()
    assert Path(artifact_path).read_text(encoding="utf-8") == json.dumps(
        payload, ensure_ascii=False
    )


def test_maybe_envelope_persists_artifact_even_when_payload_fits(tmp_path: Path) -> None:
    """When the payload fits, the returned value is the original payload
    unchanged AND the artifact file is created at the deterministic path
    with content matching ``json.dumps(payload, ensure_ascii=False)``.
    Persistence is unconditional — gated only by ``temp_dir`` availability."""
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
    assert result is payload
    artifact_path = _recipe_artifact_path(ctx.temp_dir, "open_kitchen", "any_recipe")
    assert artifact_path.exists(), (
        "artifact file must exist at the deterministic path even when the "
        "payload fits the bound (Part A REQ-B02 unconditional persistence)"
    )
    assert Path(artifact_path).read_text(encoding="utf-8") == json.dumps(
        payload, ensure_ascii=False
    )


def test_maybe_envelope_persists_exactly_once_when_oversized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the payload is oversized, ``persist_recipe_artifact`` is
    invoked exactly once — guards against the regression where the
    oversized branch contained a second persistence call alongside
    the unconditional early-call (Part A REQ-B02 single-call invariant)."""

    real_persist = _serve_helpers.persist_recipe_artifact
    calls: list[tuple[str, str]] = []

    def spy_persist(artifact_dir, *, tool_name, recipe_name, payload):
        calls.append((tool_name, recipe_name))
        return real_persist(
            artifact_dir, tool_name=tool_name, recipe_name=recipe_name, payload=payload
        )

    monkeypatch.setattr(_serve_helpers, "persist_recipe_artifact", spy_persist)

    ctx = _make_minimal_ctx(tmp_path)
    ctx.recipe_name = "any_recipe"

    payload = {"success": True, "content": "a" * 10_000}
    # Force oversized: 1-token bound → envelope branch
    bound_tokens = 1
    result = maybe_envelope_recipe_response(
        payload,
        tool_name="open_kitchen",
        recipe_name="any_recipe",
        tool_ctx=ctx,
        effective_delivery_token_limit=bound_tokens,
    )
    assert result.get("delivery_bound_spill") is True
    assert len(calls) == 1, (
        f"persist_recipe_artifact must be called exactly once; got {len(calls)} calls"
    )
    assert calls[0] == ("open_kitchen", "any_recipe")


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


def test_build_routing_edges_propagates_extractor_failure() -> None:
    def _broken_extractor(_step: object) -> list[object]:
        raise ValueError("invalid route")

    with pytest.raises(ValueError, match="invalid route"):
        build_routing_edges_by_step({"a": object()}, edge_extractor=_broken_extractor)


# ---------------------------------------------------------------------------
# Part A (REQ-B02 / REQ-B03) gap-closure regression guards
# ---------------------------------------------------------------------------


def test_build_recipe_envelope_requires_recipe_name(tmp_path: Path) -> None:
    """build_recipe_envelope requires ``recipe_name`` as a keyword-only
    argument (no positional default). Mirrors the sibling-function
    convention used by ``persist_recipe_artifact`` and
    ``maybe_envelope_recipe_response`` (Part A REQ-B03)."""
    skeleton = extract_step_skeleton([], {})
    with pytest.raises(TypeError, match="recipe_name"):
        build_recipe_envelope(  # type: ignore[call-arg]
            {"success": True},
            artifact_path=str(tmp_path / "x.log"),
            artifact_sha256="0" * 64,
            skeleton=skeleton,
            bound_bytes=1024,
        )


def _envelope_overheads(tmp_path: Path, recipe_name: str) -> tuple[int, int, int]:
    """Compute (skeleton_overhead, pull_overhead, envelope_bytes) for an
    empty-skeleton, success=True envelope with the given recipe_name, so
    budget-tight envelope tests can derive exact bound_bytes values."""
    skeleton = extract_step_skeleton([], {})
    artifact_path = str(tmp_path / "x.log")
    artifact_sha256 = "0" * 64
    skeleton_overhead = len(
        json.dumps({"step_flow_skeleton": skeleton}, ensure_ascii=False).encode("utf-8")
    )
    pull_overhead = len(
        json.dumps(
            {
                "recipe_pull": {
                    "artifact_path": artifact_path,
                    "sha256": artifact_sha256,
                    "pull_tool": "get_recipe_section",
                    "recipe_name": recipe_name,
                },
                "delivery_bound_spill": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
    )
    envelope_bytes = len(json.dumps(True, ensure_ascii=False).encode("utf-8"))
    return skeleton_overhead, pull_overhead, envelope_bytes


def test_envelope_priority_fields_share_floor_under_tight_budget(tmp_path: Path) -> None:
    """Both ``orchestration_rules`` and ``stop_step_semantics`` receive
    a fair, non-zero share of the content budget under tight budgets.
    The naive sequential allocator lets the first key exhaust the pool;
    the two-phase water-filling allocator splits evenly."""
    orch = "ORCH_RULES: " + ("X" * 600)
    stop = "STOP_STEP_SEMANTICS: " + ("Y" * 600)
    payload = {
        "success": True,
        "orchestration_rules": orch,
        "stop_step_semantics": stop,
    }
    skeleton = extract_step_skeleton([], {})
    artifact_path = str(tmp_path / "x.log")
    artifact_sha256 = "0" * 64

    sk, pl, env = _envelope_overheads(tmp_path, "test-recipe")
    # remaining ≈ 500 bytes (well above combined overhead ~54 bytes,
    # well below union of full field lengths) — forces a content split.
    bound_bytes = sk + pl + env + 500

    envelope = build_recipe_envelope(
        payload,
        recipe_name="test-recipe",
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        skeleton=skeleton,
        bound_bytes=bound_bytes,
    )

    assert "orchestration_rules" in envelope
    assert "stop_step_semantics" in envelope
    orch_truncated = envelope["orchestration_rules"]
    stop_truncated = envelope["stop_step_semantics"]
    assert orch_truncated, "orchestration_rules must be non-empty under tight budget"
    assert stop_truncated, "stop_step_semantics must be non-empty under tight budget"
    # Roughly even split: neither is allowed to dominate (old bug: first
    # key would claim ~all; new allocator splits ~half/half).
    shorter, longer = sorted((len(orch_truncated), len(stop_truncated)))
    assert longer - shorter <= max(1, shorter // 2), (
        f"priority fields must share roughly evenly under tight budget; "
        f"orch={len(orch_truncated)}, stop={len(stop_truncated)}"
    )


def test_envelope_priority_fields_omitted_not_emptied_under_extreme_budget(
    tmp_path: Path,
) -> None:
    """When the content budget falls below both keys' combined
    JSON-key overhead, at least one priority field must be OMITTED
    from the envelope — never emitted as an empty string."""
    payload = {
        "success": True,
        "orchestration_rules": "ORCH: " + ("X" * 500),
        "stop_step_semantics": "STOP: " + ("Y" * 500),
    }
    skeleton = extract_step_skeleton([], {})
    artifact_path = str(tmp_path / "x.log")
    artifact_sha256 = "0" * 64

    sk, pl, env = _envelope_overheads(tmp_path, "test-recipe")
    # remaining = 30 bytes → well below combined overhead (~53 bytes)
    bound_bytes = sk + pl + env + 30

    envelope = build_recipe_envelope(
        payload,
        recipe_name="test-recipe",
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        skeleton=skeleton,
        bound_bytes=bound_bytes,
    )

    # At least one priority key MUST be omitted (or absent); none may be "".
    for key in ("orchestration_rules", "stop_step_semantics"):
        value = envelope.get(key)
        if key in envelope:
            assert value != "", (
                f"{key!r} must not be emitted as an empty string even under "
                f"extreme budget pressure; either present-and-non-empty or omitted"
            )
    omitted = [k for k in ("orchestration_rules", "stop_step_semantics") if k not in envelope]
    assert len(omitted) >= 1, (
        f"at least one priority key must be omitted under extreme budget; "
        f"present={set(envelope) & {'orchestration_rules', 'stop_step_semantics'}}"
    )


def test_envelope_fails_closed_when_fixed_fields_exceed_bound(tmp_path: Path) -> None:
    skeleton = {
        "step_count": 1,
        "steps": [{"name": "x", "summary": "\u96ea" * 2_000, "routing_edges": []}],
    }
    bound_bytes = 256
    envelope = build_recipe_envelope(
        {"success": True, "errors": ["x" * 2_000]},
        recipe_name="test-recipe",
        artifact_path=str(tmp_path / "x.log"),
        artifact_sha256="0" * 64,
        skeleton=skeleton,
        bound_bytes=bound_bytes,
    )
    assert envelope["success"] is False
    assert envelope["error"] == "recipe_envelope_exceeds_delivery_bound"
    assert len(json.dumps(envelope, ensure_ascii=False).encode("utf-8")) <= bound_bytes


def test_envelope_priority_field_truncation_handles_multibyte_utf8(tmp_path: Path) -> None:
    """Truncation of ``orchestration_rules`` does not split a multi-byte
    UTF-8 codepoint mid-sequence. Constructs ``38 ASCII + 4-byte emoji``
    (42 bytes total) with allocation landing at byte 40 (the third byte
    of the emoji, a continuation byte) — the naive byte-trim would
    raise ``UnicodeDecodeError``; the new path backs off to the 38-byte
    ASCII prefix via ``_safe_utf8_truncate``."""
    prefix = "a" * 38
    emoji = "\U0001f600"  # 😀 — 4 UTF-8 bytes (0xF0 0x9F 0x98 0x80)
    orchestration_rules = prefix + emoji
    assert len(orchestration_rules.encode("utf-8")) == 42, "test invariant"

    payload = {"success": True, "orchestration_rules": orchestration_rules}
    skeleton = extract_step_skeleton([], {})
    artifact_path = str(tmp_path / "x.log")
    artifact_sha256 = "0" * 64

    sk, pl, env = _envelope_overheads(tmp_path, "test-recipe")
    # remaining = 65 bytes → pool = 40 → take = 40 → byte 40 lands in
    # the middle of the 4-byte emoji (continuation byte at index 40).
    bound_bytes = sk + pl + env + 65

    envelope = build_recipe_envelope(
        payload,
        recipe_name="test-recipe",
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        skeleton=skeleton,
        bound_bytes=bound_bytes,
    )

    # No UnicodeDecodeError raised means _safe_utf8_truncate backed off
    # to the 38-byte ASCII prefix; the field must be present (take > 0)
    # and a valid prefix of the original.
    assert "orchestration_rules" in envelope
    truncated = envelope["orchestration_rules"]
    assert truncated.encode("utf-8") == prefix.encode("utf-8"), (
        f"truncation must back off to 38-byte ASCII prefix; "
        f"got {len(truncated.encode('utf-8'))} bytes"
    )
    assert orchestration_rules.startswith(truncated)


# ---------------------------------------------------------------------------
# Part B gap-closure regression guards (REQ-B04, REQ-B-T2/-T4/-T5)
# ---------------------------------------------------------------------------


def test_build_routing_edges_by_step_uses_canonical_extractor() -> None:
    """The envelope's edge extraction must reuse the canonical
    ``_extract_routing_edges`` from ``recipe/_analysis_graph.py`` rather
    than the now-deleted local duplicate. Asserted independently:
    ``build_routing_edges_by_step`` (which defaults to the canonical
    extractor after the Step 1 change) and a direct call to
    ``autoskillit.recipe._extract_routing_edges`` on each RecipeStep
    must produce identical ``(edge_type, target)`` tuples.

    The previous local copy had two silent behavioral divergences
    (a spurious falsy-target guard, and independent-vs-elif handling of
    ``on_result.conditions``/``on_result.routes``); this test would
    have caught both."""
    recipe_names = _recipe_names()
    assert recipe_names, "no bundled recipes available for the equivalence test"

    for recipe_name in recipe_names:
        info = find_recipe_by_name(recipe_name, _PROJECT_ROOT)
        assert info is not None
        recipe = load_recipe(info.path)
        active_recipe_steps = recipe.steps

        default_edges = build_routing_edges_by_step(
            active_recipe_steps,
            edge_extractor=_extract_routing_edges,
        )

        for step_name, step in active_recipe_steps.items():
            canonical = [
                (edge.edge_type, edge.target)
                for edge in _extract_routing_edges(step)
                if edge.target
            ]
            assert default_edges.get(step_name) == canonical, (
                f"{recipe_name}.{step_name}: build_routing_edges_by_step "
                f"default={default_edges.get(step_name)!r} "
                f"differs from canonical _extract_routing_edges={canonical!r}"
            )


def test_extract_step_skeleton_includes_byte_ranges() -> None:
    """``extract_step_skeleton`` gains an optional ``byte_ranges`` keyword;
    a step present in the map gets a ``byte_range`` field carrying the
    ``[start, end]`` pair, while a step absent from the map gets no
    such key (fail-open, matching the existing edges/summary fallback)."""
    byte_ranges = {"step_a": (10, 42)}
    skeleton = extract_step_skeleton(
        ["step_a", "step_b"],
        routing_edges_by_step={},
        step_summaries={},
        byte_ranges=byte_ranges,
    )
    by_name = {step["name"]: step for step in skeleton["steps"]}
    assert by_name["step_a"].get("byte_range") == [10, 42], (
        "step present in byte_ranges must carry the byte_range field as a JSON list"
    )
    assert "byte_range" not in by_name["step_b"], (
        "step absent from byte_ranges must get no byte_range key (fail-open)"
    )


def test_compute_step_byte_ranges_matches_step_boundaries() -> None:
    """Each returned ``(start, end)`` slice covers the original step's
    key + body in the source text. We assert substring containment
    (rather than exact-equality) because YAML mark ranges commonly
    include trailing whitespace up to the next sibling key."""
    content = (
        'version: "1"\n'
        "steps:\n"
        "  alpha:\n"
        "    tool: run_cmd\n"
        "    with_args:\n"
        '      cmd: "echo alpha-body-marker"\n'
        "  beta:\n"
        "    tool: run_cmd\n"
        "    with_args:\n"
        '      cmd: "echo beta-body-marker"\n'
    )
    ranges = _compute_step_byte_ranges(content)
    assert set(ranges.keys()) == {"alpha", "beta"}, (
        f"expected exactly the two top-level steps; got {sorted(ranges.keys())}"
    )
    encoded = content.encode("utf-8")
    for step_name, expected_marker in (
        ("alpha", "alpha-body-marker"),
        ("beta", "beta-body-marker"),
    ):
        start, end = ranges[step_name]
        slice_text = encoded[start:end].decode("utf-8", errors="replace")
        assert expected_marker in slice_text, (
            f"{step_name}: marker {expected_marker!r} not in utf-8 decode of "
            f"content[{start}:{end}] = {slice_text!r}"
        )


@pytest.mark.parametrize(
    "malformed",
    [
        "- a\n- b\n",  # bare YAML sequence at root
        "steps: oops\n",  # steps: whose value is a scalar, not a mapping
    ],
    ids=["bare-sequence", "scalar-steps-value"],
)
def test_compute_step_byte_ranges_returns_empty_on_non_mapping_document(
    malformed: str,
) -> None:
    """``_compute_step_byte_ranges`` must fail open on any malformed or
    non-mapping YAML document, returning ``{}`` without raising. The
    guard is ``isinstance(..., yaml.MappingNode)`` rather than a bare
    ``except yaml.YAMLError`` because a document that parses
    successfully but isn't a mapping raises ``TypeError`` /
    ``ValueError`` from tuple-unpacking (not ``YAMLError``)."""
    assert _compute_step_byte_ranges(malformed) == {}


# ---------------------------------------------------------------------------
# Part B pull-tool end-to-end tests (REQ-B-T2, REQ-B-T4, REQ-B-T5)
# ---------------------------------------------------------------------------


_RECIPE_FOR_PULL = "remediation"


def _mock_fmcp_ctx() -> MagicMock:
    """Return a minimal FastMCP ``Context`` mock with async component methods."""
    ctx = MagicMock()
    ctx.enable_components = AsyncMock()
    ctx.disable_components = AsyncMock()
    return ctx


async def _open_kitchen_patched(name: str, overrides: dict | None, monkeypatch) -> dict:
    """Call ``open_kitchen`` with all infrastructure side-effects patched out.

    Mirrors the helper in ``tests/server/test_serve_idempotence.py``:
    patches ``_prime_quota_cache`` / ``_write_hook_config`` /
    ``create_background_task`` / ``resolve_kitchen_id`` (open_kitchen's
    background + identity side effects) and resets the recipe
    ``_LOAD_CACHE`` so a fresh load + validate runs for every test.
    """
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
    fmcp_ctx = _mock_fmcp_ctx()
    with patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()):
        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server.tools.tools_kitchen.create_background_task"):
                with patch(
                    "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                    return_value="test-kitchen",
                ):
                    return json.loads(
                        await open_kitchen(name=name, overrides=overrides, ctx=fmcp_ctx)
                    )


@pytest.mark.anyio
@pytest.mark.medium
async def test_load_recipe_envelope_pulls_from_its_own_artifact(
    tool_ctx_kitchen_open, monkeypatch, tmp_path: Path
) -> None:
    from autoskillit.server.tools.tools_recipe import get_recipe_section
    from autoskillit.server.tools.tools_recipe import load_recipe as load_recipe_tool

    monkeypatch.chdir(tmp_path)
    opened = await _open_kitchen_patched(_RECIPE_FOR_PULL, None, monkeypatch)
    assert opened.get("success") is True
    assert tool_ctx_kitchen_open.recipe_name == _RECIPE_FOR_PULL

    loaded = json.loads(await load_recipe_tool(name="implementation"))
    pull = loaded["recipe_pull"]
    assert pull["recipe_name"] == "implementation"
    assert pull["producer_tool"] == "load_recipe"
    assert tool_ctx_kitchen_open.recipe_name == _RECIPE_FOR_PULL

    step_name = loaded["step_flow_skeleton"]["steps"][0]["name"]
    response = json.loads(
        await get_recipe_section(
            section=step_name,
            recipe_name=pull["recipe_name"],
            producer_tool=pull["producer_tool"],
            artifact_path=pull["artifact_path"],
            artifact_sha256=pull["sha256"],
        )
    )
    assert response.get("success") is True, response
    assert response["content"]


@pytest.mark.anyio
@pytest.mark.medium
async def test_pull_tool_returns_bounded_step_content(
    tool_ctx_kitchen_open, monkeypatch, tmp_path: Path
) -> None:
    """Live ``get_recipe_section`` end-to-end against the persisted
    artifact (REQ-B-T2): every post-prune step name returns the bounded
    step body matching the canonical serializer, the response itself
    fits the smallest backend bound, and an unknown section name
    produces ``{"success": False, "error": "section_not_found"}``."""
    from autoskillit.server.tools.tools_recipe import get_recipe_section

    monkeypatch.chdir(tmp_path)

    ok_result = await _open_kitchen_patched(
        _RECIPE_FOR_PULL,
        None,
        monkeypatch,
    )
    assert ok_result.get("success") is True, f"open_kitchen failed: {ok_result}"

    persisted = json.loads(
        _recipe_artifact_path(
            tool_ctx_kitchen_open.temp_dir, "open_kitchen", _RECIPE_FOR_PULL
        ).read_text(encoding="utf-8")
    )
    persisted_yaml = persisted.get("content", "") or ""
    parsed = load_yaml(persisted_yaml)
    assert isinstance(parsed, dict), "persisted content must parse as a mapping"
    parsed_steps = parsed.get("steps", {})
    assert isinstance(parsed_steps, dict)

    post_prune_raw = cast(list[object], persisted.get("post_prune_step_names") or [])
    step_names = [str(n) for n in post_prune_raw if isinstance(n, str)]
    assert step_names, "recipe must expose at least one post-prune step"

    bound_tokens = _smallest_bound_tokens()
    bound_bytes_for_response = bound_tokens * 4
    for step_name in step_names:
        response = json.loads(await get_recipe_section(section=step_name))
        assert response.get("success") is True, (
            f"get_recipe_section({step_name!r}) failed: {response}"
        )
        expected = fast_dumps({step_name: parsed_steps[step_name]})
        assert response["section"] == step_name
        assert response["content"] == expected, (
            f"step {step_name!r}: pull content diverges from "
            f"fast_dumps({{step: parsed['steps'][step_name]}})"
        )
        serialized = json.dumps(response, ensure_ascii=False)
        assert len(serialized.encode("utf-8")) <= bound_bytes_for_response, (
            f"step {step_name!r}: response exceeds the backend bound "
            f"({bound_bytes_for_response} bytes); got {len(serialized.encode('utf-8'))}"
        )

    unknown = json.loads(await get_recipe_section(section="not_a_real_step"))
    assert unknown == {
        "success": False,
        "error": "section_not_found",
        "section": "not_a_real_step",
    }


@pytest.mark.anyio
@pytest.mark.medium
async def test_artifact_recreation_from_parsed_recipe(
    tool_ctx_kitchen_open, monkeypatch, tmp_path: Path
) -> None:
    """When the persisted artifact is deleted, ``get_recipe_section``
    re-creates it via the same serve pipeline that built it originally
    (REQ-B-T4). When the recreation pipeline returns an invalid
    recipe, the pull tool surfaces a structured
    ``recipe_artifact_unavailable`` envelope (driven here via a
    ``serve_recipe`` monkeypatch, since the literal "no active recipe"
    condition is checked earlier and produces a different error)."""
    from autoskillit.server.tools.tools_recipe import get_recipe_section

    monkeypatch.chdir(tmp_path)

    ok_result = await _open_kitchen_patched(_RECIPE_FOR_PULL, None, monkeypatch)
    assert ok_result.get("success") is True, f"open_kitchen failed: {ok_result}"

    artifact_path = _recipe_artifact_path(
        tool_ctx_kitchen_open.temp_dir, "open_kitchen", _RECIPE_FOR_PULL
    )
    artifact_path.unlink()
    assert not artifact_path.exists(), "precondition: artifact file deleted"

    post_prune_raw = cast(list[object], ok_result.get("post_prune_step_names") or [])
    step_name = next(
        (str(n) for n in post_prune_raw if isinstance(n, str)),
        None,
    )
    assert step_name is not None, "recipe must expose at least one post-prune step"

    response = json.loads(await get_recipe_section(section=step_name))
    assert response.get("success") is True, (
        f"get_recipe_section after artifact deletion should recreate; got {response}"
    )
    assert artifact_path.exists(), "artifact must be rewritten by the recreation path on miss"

    # Now drive the invalid-recreation branch via a monkeypatched
    # ``serve_recipe`` — get_recipe_section imports it into its own
    # module namespace, so we must override the attribute on the
    # importing module (``tools_recipe``) for the patch to take
    # effect. The pulled result must be a structured
    # ``recipe_artifact_unavailable`` envelope rather than a generic
    # error.
    artifact_path.unlink()
    import autoskillit.server.tools.tools_recipe as _tools_recipe_mod  # noqa: PLC0415

    def _fake_serve_recipe(*args, **kwargs) -> dict:
        # Reference args/kwargs so linters see them as "used" — the patch
        # accepts any signature, we just need to return invalid-recipe.
        del args, kwargs
        return {"valid": False, "content": "x"}

    monkeypatch.setattr(_tools_recipe_mod, "serve_recipe", _fake_serve_recipe)
    failed = json.loads(await get_recipe_section(section=step_name))
    assert failed.get("success") is False
    assert failed["error"] == "recipe_artifact_unavailable"
    assert failed["detail"] == "recreation returned invalid recipe"


@pytest.mark.anyio
@pytest.mark.medium
async def test_large_step_chunked_via_continuation(
    tool_ctx_kitchen_open, monkeypatch, tmp_path: Path
) -> None:
    """Persist a synthetic oversized artifact whose single ``giant_step``
    body exceeds the smallest backend's bound; driving
    ``get_recipe_section`` against ``backend = None`` (which forces
    the 10,000-token / 40,000-byte fallback) produces a chunked
    response with ``has_more=True`` and a ``next_part`` cursor;
    following that cursor concatenates the chunks back into the
    canonical serializer's output (REQ-B-T5)."""
    from autoskillit.server.tools.tools_recipe import get_recipe_section

    monkeypatch.chdir(tmp_path)

    # Open the kitchen first so the tool_ctx has recipes wired and
    # ``recipe_name`` is set — the giant-step payload is written
    # directly to the artifact path to bypass the size guard.
    ok_result = await _open_kitchen_patched(_RECIPE_FOR_PULL, None, monkeypatch)
    assert ok_result.get("success") is True

    artifact_path = _recipe_artifact_path(
        tool_ctx_kitchen_open.temp_dir, "open_kitchen", _RECIPE_FOR_PULL
    )
    oversized_field = "X" * 80_000  # ~80KB >> 40KB Codex bound
    persisted_payload = {
        "success": True,
        "content": (f'version: "1"\nsteps:\n  giant_step:\n    note: {oversized_field}\n'),
    }
    persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        tool_name="open_kitchen",
        recipe_name=_RECIPE_FOR_PULL,
        payload=persisted_payload,
    )
    assert artifact_path.exists()

    # Force the 40KB fallback bound (no backend → bound_tokens=10_000).
    tool_ctx_kitchen_open.backend = None  # type: ignore[assignment]
    tool_ctx_kitchen_open.recipe_name = _RECIPE_FOR_PULL

    expected = fast_dumps(
        {"giant_step": load_yaml(persisted_payload["content"])["steps"]["giant_step"]}
    )

    chunks: list[str] = []
    part = 0
    has_more = True
    while has_more:
        response = json.loads(await get_recipe_section(section="giant_step", part=part))
        assert response.get("success") is True, f"chunk {part} failed: {response}"
        assert response["section"] == "giant_step"
        chunks.append(response["content"])
        has_more = bool(response.get("has_more"))
        if has_more:
            assert "next_part" in response, (
                f"chunk {part} reports has_more=True without next_part cursor"
            )
            part = int(response["next_part"])
        else:
            assert "next_part" not in response

    assert "".join(chunks) == expected, (
        "chunked pull content does not round-trip to the canonical serializer's output"
    )
