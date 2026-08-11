"""Shared test builder utilities for tests/server/."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import (
    RECIPE_SECTION_PAGINATION_VERSION,
    RECIPE_SECTION_REGISTRY_DIGEST,
    FinalizedRecipeProjection,
    RecipeBindingProjection,
    SkillResult,
    recipe_section_digest,
    recipe_section_element_digest,
)
from autoskillit.core.types import RetryReason
from tests.fleet._helpers import _make_recipe_info as _fleet_make_recipe_info

if TYPE_CHECKING:
    from autoskillit.config.settings import AgentBackendConfig
    from autoskillit.pipeline import ToolContext

_HOOK_CONFIG_OVERLAY_RELPATH = (".autoskillit", "temp", ".hook_config_overlay.json")

BUNDLED_RECIPE_STEP_BACKEND_PIN_CASES = (
    (
        "implementation",
        "run_arch_lenses",
        "agent_backend.recipe_overrides.implementation.run_arch_lenses",
    ),
    (
        "implementation-groups",
        "run_arch_lenses",
        "agent_backend.recipe_overrides.implementation-groups.run_arch_lenses",
    ),
    (
        "remediation",
        "investigate",
        "agent_backend.recipe_overrides.remediation.investigate",
    ),
    (
        "remediation",
        "run_arch_lenses",
        "agent_backend.recipe_overrides.remediation.run_arch_lenses",
    ),
    (
        "research",
        "run_experiment_lenses",
        "agent_backend.recipe_overrides.research.run_experiment_lenses",
    ),
    (
        "research",
        "scope",
        "agent_backend.recipe_overrides.research.scope",
    ),
    (
        "research",
        "vis_apply",
        "agent_backend.recipe_overrides.research.vis_apply",
    ),
    (
        "research-design",
        "scope",
        "agent_backend.recipe_overrides.research-design.scope",
    ),
    (
        "research-design",
        "vis_apply",
        "agent_backend.recipe_overrides.research-design.vis_apply",
    ),
    (
        "research-review",
        "run_experiment_lenses",
        "agent_backend.recipe_overrides.research-review.run_experiment_lenses",
    ),
)


def _configure_admitted_recipe(ctx: Any, path: Path) -> None:
    """Admit a recipe and load it with empty steps and ingredients by default."""
    ctx.recipes.find.return_value = MagicMock(path=path)
    ctx.recipes.load.return_value = MagicMock(steps={}, ingredients={})


@dataclass
class McpCallCounter:
    """Record one session-start MCP sequence with initialization identity."""

    calls: list[tuple[str, str | None]] = field(default_factory=list)
    delivery_mode: str | None = None

    def record(self, tool_name: str, initialization_id: str | None = None) -> None:
        self.calls.append((tool_name, initialization_id))

    def __len__(self) -> int:
        return len(self.calls)


async def simulate_session_start(
    recipe_name: str,
    backend_name: str,
    *,
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> McpCallCounter:
    """Drive open plus every required page and record the exact MCP call sequence."""
    from autoskillit.execution.backends import BACKEND_REGISTRY
    from autoskillit.pipeline import closed_kitchen_open_state
    from autoskillit.pipeline.recipe_initialization import NoActiveRecipe

    monkeypatch.setattr(tool_ctx, "backend", BACKEND_REGISTRY[backend_name]())
    monkeypatch.setattr(tool_ctx, "kitchen_open_state", closed_kitchen_open_state())
    monkeypatch.setattr(tool_ctx, "recipe_initialization_state", NoActiveRecipe())
    counter = McpCallCounter()
    counter.record("open_kitchen")
    project_root = Path(__file__).resolve().parents[2]
    try:
        envelope = await _open_kitchen_patched(
            recipe_name,
            {
                "task": "test task",
                "issue_url": "https://github.com/test/test/issues/1",
                "source_dir": str(project_root),
            },
            monkeypatch,
        )
    except ValueError as exc:
        if str(exc) != "cannot prepare response from failed_ambiguous":
            raise
        pytest.skip(
            f"recipe delivery is unavailable under the active feature scope: {recipe_name}"
        )
    assert envelope.get("success") is True, envelope
    if envelope.get("delivery_bound_spill") is True:
        capabilities = BACKEND_REGISTRY[backend_name]().capabilities
        counter.delivery_mode = (
            "codex_bounded"
            if capabilities.recipe_delivery_budget is not None
            else "claude_code_bounded"
        )
        await _credit_initialization_sections(envelope, counter=counter)
    else:
        counter.delivery_mode = "claude_code_inline"
    return counter


def _bundled_backend() -> AgentBackendConfig:
    """Load the bundled backend defaults shared by backend-override tests."""
    from autoskillit.config.settings import AgentBackendConfig
    from autoskillit.core.io import load_yaml
    from autoskillit.core.paths import pkg_root

    defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
    return AgentBackendConfig(**defaults["agent_backend"])


def _mock_fmcp_ctx() -> MagicMock:
    """Return a minimal FastMCP Context mock with async component methods."""
    ctx = MagicMock()
    ctx.enable_components = AsyncMock()
    ctx.disable_components = AsyncMock()
    return ctx


async def _open_kitchen_patched(name, overrides, monkeypatch):
    """Call open_kitchen with all infrastructure side-effects patched out."""
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


async def _credit_initialization_sections(
    envelope: dict[str, Any],
    *,
    counter: McpCallCounter | None = None,
) -> None:
    """Page every required section of a bounded envelope through the real pull tool.

    Passes ``initialization_id`` and ``page_plan_sha256`` so each page is credited to the
    server-owned initialization, which ``complete_recipe_initialization`` requires.
    """
    from autoskillit.server.tools.tools_recipe import get_recipe_section

    identity = {key: value for key, value in envelope["recipe_pull"].items() if key != "pull_tool"}
    initialization_id = envelope["initialization_id"]
    for requirement in envelope["required_sections"]:
        continuation: str | None = None
        for part in range(requirement["total_parts"]):
            if counter is not None:
                counter.record("get_recipe_section", initialization_id)
            response = json.loads(
                await get_recipe_section(
                    section=requirement["section"],
                    part=part,
                    initialization_id=initialization_id,
                    page_plan_sha256=requirement["page_plan_sha256"],
                    continuation=continuation,
                    **identity,
                )
            )
            assert response.get("success") is True, f"pull failed: {response}"
            assert response.get("page_plan_sha256") == requirement["page_plan_sha256"], response
            continuation = response.get("continuation")


async def _pull_step_section(envelope: dict[str, Any], step_name: str) -> dict[str, Any]:
    """Return one step's YAML subtree, pulled through the real ``get_recipe_section`` tool."""
    from autoskillit.core.io import load_yaml

    shim = {"success": True, "recipe_pull": envelope["recipe_pull"]}
    body = await _resolve_recipe_section(shim, section=step_name)
    assert isinstance(body, str) and body, f"step section {step_name!r} came back empty"
    parsed = load_yaml(body)
    assert isinstance(parsed, dict), f"step section {step_name!r} is not a mapping"
    assert step_name in parsed, (
        f"step section {step_name!r} not present in section body; got keys {sorted(parsed)}"
    )
    step_obj = parsed[step_name]
    assert isinstance(step_obj, dict), f"step section {step_name!r} body is not a mapping"
    return step_obj


def _with_finalized_projection(
    result: dict[str, Any],
    *,
    binding_projection: RecipeBindingProjection | None = None,
) -> dict[str, Any]:
    """Attach the server-only projection required by a successful serve fixture."""
    for key, fill in (("content_hash", "a"), ("composite_hash", "b")):
        digest = result.get(key)
        if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
            result[key] = "sha256:" + (fill * 64)
    names = tuple(
        name for name in result.get("post_prune_step_names", ()) if isinstance(name, str) and name
    )
    if not names:
        names = ("fixture-entrypoint",)
    result["_finalized_projection"] = FinalizedRecipeProjection(
        binding_projection=binding_projection or RecipeBindingProjection(invocations={}),
        ordered_step_names=names,
        entrypoint=names[0],
        ordered_flow_edges=(),
    )
    return result


async def _resolve_recipe_section(result: dict[str, Any], *, section: str = "content") -> Any:
    """Reconstruct one typed recipe section from inline or paginated delivery."""
    assert result.get("success") is True, f"recipe response was not successful: {result}"
    pull = result.get("recipe_pull")
    if pull is None and section in result:
        return result[section]
    assert isinstance(pull, dict), f"recipe response has neither content nor pull: {result}"
    assert pull.get("pull_tool") == "get_recipe_section"

    from autoskillit.server.tools.tools_recipe import get_recipe_section

    identity = {key: value for key, value in pull.items() if key != "pull_tool"}
    raw_chunks: list[str] = []
    scalar_chunks: list[str] = []
    elements: list[object] = []
    fragment_chunks: list[str] = []
    fragment_count: int | None = None
    fragment_byte_total: int | None = None
    fragment_element_sha256: str | None = None
    expected_range_start = 0
    expected_fragment_index = 0
    range_identities: set[tuple[object, ...]] = set()
    shared_identity: tuple[object, ...] | None = None
    expected_format_family: str | None = None
    expected_total_parts: int | None = None
    expected_section_total: int | None = None
    part = 0
    page_plan_sha256: str | None = None
    continuation: str | None = None
    while True:
        response = json.loads(
            await get_recipe_section(
                section=section,
                part=part,
                page_plan_sha256=page_plan_sha256,
                continuation=continuation,
                **identity,
            )
        )
        assert response.get("success") is True, f"get_recipe_section returned error: {response}"
        assert response["pagination_version"] == RECIPE_SECTION_PAGINATION_VERSION
        assert response["section_registry_sha256"] == RECIPE_SECTION_REGISTRY_DIGEST
        assert response["section"] == section
        assert response["part"] == part
        assert type(response["total_parts"]) is int and response["total_parts"] > 0
        assert type(response["has_more"]) is bool
        assert isinstance(response["section_sha256"], str)
        assert isinstance(response["page_plan_sha256"], str)
        assert isinstance(response["payload_sha256"], str)
        assert isinstance(response["body_sha256"], str)
        assert response["payload_sha256"] == identity["payload_sha256"]
        assert response["body_sha256"] == identity["body_sha256"]

        page_identity = (
            response["pagination_version"],
            response["section_registry_sha256"],
            response["section_sha256"],
            response["page_plan_sha256"],
            response["payload_sha256"],
            response["body_sha256"],
        )
        if shared_identity is None:
            shared_identity = page_identity
            expected_total_parts = response["total_parts"]
            page_plan_sha256 = response["page_plan_sha256"]
        else:
            assert page_identity == shared_identity
            assert response["total_parts"] == expected_total_parts

        chunk = response.get("content")
        content_format = response.get("content_format")
        assert content_format in {
            "raw-text",
            "json-array-page",
            "json-scalar-page",
            "json-element-fragment",
        }, f"unknown recipe section format: {content_format!r}"
        if content_format == "json-array-page":
            # Flat delivery encoding: array-page content arrives pre-parsed.
            assert isinstance(chunk, list)
        else:
            assert isinstance(chunk, str)

        if content_format == "raw-text":
            assert expected_format_family in (None, "raw")
            expected_format_family = "raw"
            assert response["byte_start"] == expected_range_start
            assert response["byte_end"] == response["byte_start"] + len(chunk.encode("utf-8"))
            expected_range_start = response["byte_end"]
            if expected_section_total is None:
                expected_section_total = response["byte_total"]
            else:
                assert response["byte_total"] == expected_section_total
            range_identity = (
                content_format,
                response["byte_start"],
                response["byte_end"],
            )
            raw_chunks.append(chunk)
            forbidden = {
                "element_start",
                "element_end",
                "element_total",
                "scalar_byte_start",
                "scalar_byte_end",
                "scalar_byte_total",
                "element_index",
                "element_sha256",
                "fragment_index",
                "fragment_count",
                "fragment_byte_start",
                "fragment_byte_end",
                "fragment_byte_total",
            }
        elif content_format == "json-scalar-page":
            assert expected_format_family in (None, "scalar")
            expected_format_family = "scalar"
            decoded = json.loads(chunk)
            assert isinstance(decoded, str)
            assert response["scalar_byte_start"] == expected_range_start
            assert response["scalar_byte_end"] == response["scalar_byte_start"] + len(
                decoded.encode("utf-8")
            )
            expected_range_start = response["scalar_byte_end"]
            if expected_section_total is None:
                expected_section_total = response["scalar_byte_total"]
            else:
                assert response["scalar_byte_total"] == expected_section_total
            range_identity = (
                content_format,
                response["scalar_byte_start"],
                response["scalar_byte_end"],
            )
            scalar_chunks.append(decoded)
            forbidden = {
                "byte_start",
                "byte_end",
                "byte_total",
                "element_start",
                "element_end",
                "element_total",
                "element_index",
                "element_sha256",
                "fragment_index",
                "fragment_count",
                "fragment_byte_start",
                "fragment_byte_end",
                "fragment_byte_total",
            }
        elif content_format == "json-array-page":
            assert expected_format_family in (None, "array")
            expected_format_family = "array"
            decoded = chunk  # already parsed by flat delivery encoding
            assert isinstance(decoded, list)
            assert response["element_start"] == len(elements)
            assert response["element_end"] == response["element_start"] + len(decoded)
            assert response["element_end"] > response["element_start"] or (
                response["element_total"] == 0
                and response["total_parts"] == 1
                and response["has_more"] is False
            )
            expected_range_start = response["element_end"]
            if expected_section_total is None:
                expected_section_total = response["element_total"]
            else:
                assert response["element_total"] == expected_section_total
            range_identity = (
                content_format,
                response["element_start"],
                response["element_end"],
            )
            elements.extend(decoded)
            forbidden = {
                "byte_start",
                "byte_end",
                "byte_total",
                "scalar_byte_start",
                "scalar_byte_end",
                "scalar_byte_total",
                "element_index",
                "element_sha256",
                "fragment_index",
                "fragment_count",
                "fragment_byte_start",
                "fragment_byte_end",
                "fragment_byte_total",
            }
        else:
            assert expected_format_family in (None, "array")
            expected_format_family = "array"
            decoded = json.loads(chunk)
            assert isinstance(decoded, str)
            assert response["element_index"] == len(elements)
            assert response["fragment_index"] == expected_fragment_index
            assert response["fragment_byte_start"] == sum(
                len(value.encode("utf-8")) for value in fragment_chunks
            )
            assert response["fragment_byte_end"] == response["fragment_byte_start"] + len(
                decoded.encode("utf-8")
            )
            if fragment_count is None:
                fragment_count = response["fragment_count"]
                fragment_byte_total = response["fragment_byte_total"]
                fragment_element_sha256 = response["element_sha256"]
            else:
                assert response["fragment_count"] == fragment_count
                assert response["fragment_byte_total"] == fragment_byte_total
                assert response["element_sha256"] == fragment_element_sha256
            range_identity = (
                content_format,
                response["element_index"],
                response["fragment_index"],
                response["fragment_byte_start"],
                response["fragment_byte_end"],
            )
            fragment_chunks.append(decoded)
            expected_fragment_index += 1
            assert fragment_count is not None
            if expected_fragment_index == fragment_count:
                canonical_element = "".join(fragment_chunks)
                assert len(canonical_element.encode("utf-8")) == fragment_byte_total
                element = json.loads(canonical_element)
                assert recipe_section_element_digest(element) == fragment_element_sha256
                elements.append(element)
                expected_range_start = len(elements)
                fragment_chunks = []
                fragment_count = None
                fragment_byte_total = None
                fragment_element_sha256 = None
                expected_fragment_index = 0
            forbidden = {
                "byte_start",
                "byte_end",
                "byte_total",
                "element_start",
                "element_end",
                "element_total",
                "scalar_byte_start",
                "scalar_byte_end",
                "scalar_byte_total",
            }

        assert not (forbidden & response.keys())
        assert range_identity not in range_identities
        range_identities.add(range_identity)
        if response["has_more"]:
            assert response["next_part"] == part + 1
            assert part + 1 < response["total_parts"]
            part = response["next_part"]
            continuation = response["continuation"]
            continue

        assert "next_part" not in response
        assert part + 1 == response["total_parts"]
        assert not fragment_chunks
        if expected_section_total is not None:
            assert expected_range_start == expected_section_total
        else:
            assert expected_format_family == "array"
            assert expected_range_start == len(elements)
        if expected_format_family == "raw":
            value: object = "".join(raw_chunks)
            raw_digest = True
        elif expected_format_family == "scalar":
            value = "".join(scalar_chunks)
            raw_digest = False
        else:
            value = elements
            raw_digest = False
        assert recipe_section_digest(value, raw=raw_digest) == response["section_sha256"]
        return value


def _write_registry(monkeypatch: Any, tmp_path: Any, entries: list[dict[str, Any]]) -> Any:
    """Write a fake active-kitchens registry for prune_stale_kitchen_state tests."""
    from autoskillit.core._plugin_cache import write_versioned_json

    registry_path = tmp_path / "active_kitchens.json"
    monkeypatch.setattr(
        "autoskillit.core._plugin_cache._active_kitchens_path",
        lambda: registry_path,
    )
    monkeypatch.setattr(
        "autoskillit.core._plugin_cache._active_kitchens_lock",
        lambda: tmp_path / "active_kitchens.lock",
    )
    write_versioned_json(registry_path, {"kitchens": entries}, schema_version=2)
    return registry_path


def _simple_prompt_builder(**kwargs) -> str:
    """Minimal prompt builder for tests — avoids CLI imports."""
    return f"prompt-for-{kwargs.get('recipe', 'unknown')}"


async def _no_sleep_quota_checker(config: Any, **kwargs) -> dict:
    """Quota checker stub: always returns no-sleep result."""
    return {
        "should_sleep": False,
        "sleep_seconds": 0,
        "utilization": None,
        "resets_at": None,
        "window_name": None,
    }


async def _noop_quota_refresher(config: Any, **kwargs) -> None:
    """Quota refresher stub: no-op."""


def _patch_dispatch_quota_no_sleep(monkeypatch: Any) -> None:
    """Patch dispatch_food_truck's quota dependencies for non-quota tests."""
    monkeypatch.setattr(
        "autoskillit.server._misc.check_and_sleep_if_needed",
        _no_sleep_quota_checker,
    )
    monkeypatch.setattr(
        "autoskillit.server._misc._refresh_quota_cache",
        _noop_quota_refresher,
    )


def _make_recipe_info(name: str = "test-recipe"):
    return _fleet_make_recipe_info(name, path_prefix="/fake/recipes/")


def _make_standard_recipe(name: str = "test-recipe", ingredient_keys: list[str] | None = None):
    """Return a minimal Recipe with kind=STANDARD for use as load_recipe mock return value."""
    from autoskillit.recipe.schema import Recipe, RecipeIngredient, RecipeKind

    ingredients = {k: RecipeIngredient(description=k) for k in (ingredient_keys or [])}
    return Recipe(name=name, description="test", ingredients=ingredients, kind=RecipeKind.STANDARD)


def _skill_ok(report_text: str = "## Bug Report\ndetails") -> SkillResult:
    return SkillResult(
        success=True,
        result=report_text,
        session_id="sid",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )


def _skill_fail() -> SkillResult:
    return SkillResult(
        success=False,
        result="",
        session_id="",
        subtype="error",
        is_error=True,
        exit_code=1,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="something went wrong",
    )


_PATCHED_DEFAULTS = {
    "base_branch": "develop",
    "local_review_rounds": "7",
    "adversarial_review_level": "aggressive",
    "is_fleet_dispatch": "true",
    "dispatch_id": "test-dispatch-999",
    "pipeline_health": "true",
}

_SERVER_ONLY_KEYS = frozenset({"kitchen_id", "diagnostics_log_dir"})

_MINIMAL_SCRIPT_YAML = """\
name: test-script
description: Test
summary: test
ingredients:
  task:
    description: What to do
    required: true
steps:
  do-thing:
    tool: run_skill
    with:
      skill_command: "/autoskillit:investigate ${{ inputs.task }}"
      cwd: "."
    on_success: done
    on_failure: escalate
  done:
    action: stop
    message: "Done."
  escalate:
    action: stop
    message: "Failed."
kitchen_rules:
  - "Follow routing rules"
"""
