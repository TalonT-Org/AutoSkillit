"""Contract tests: server helpers gate response schema."""

from __future__ import annotations

import copy
import json

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _valid_recipe_section_pages(format_family: str) -> list[dict[str, object]]:
    from autoskillit.server._recipe_delivery import RecipeArtifactGeneration
    from autoskillit.server._recipe_section_pagination import (
        build_recipe_section_page_plan,
        render_recipe_section_page,
        select_recipe_section,
    )

    payload: dict[str, object] = {
        "content": "raw-page-" * 600,
        "ingredients_table": "scalar-page-" * 600,
        "warnings": [f"array-{index:03d}-" + ("x" * 80) for index in range(40)],
    }
    section = {
        "raw": "content",
        "scalar": "ingredients_table",
        "array": "warnings",
        "fragment": "warnings",
    }[format_family]
    if format_family == "fragment":
        payload["warnings"] = ["fragment-" + ("x" * 8_000)]
    generation = RecipeArtifactGeneration(
        producer_tool="open_kitchen",
        recipe_name="remediation",
        descriptor_version=1,
        schema_version=1,
        payload_sha256="sha256:" + ("1" * 64),
        artifact_blob_sha256="sha256:" + ("2" * 64),
        artifact_blob_size_bytes=10_000,
        body_sha256="sha256:" + ("3" * 64),
        body_size_bytes=9_000,
    )
    plan = build_recipe_section_page_plan(
        kitchen_id="consumer-sequence",
        generation=generation,
        selected=select_recipe_section(payload, section),
        recipe_section_bound_bytes=1_000,
    )
    pages = [
        json.loads(render_recipe_section_page(plan, part)) for part in range(plan.total_parts)
    ]
    assert len(pages) > 1
    if format_family == "fragment":
        assert {page["content_format"] for page in pages} == {"json-element-fragment"}
    return pages


def _mutate_recipe_section_pages(
    pages: list[dict[str, object]],
    mutation: str,
) -> None:
    second = pages[1]
    if mutation == "pagination_version":
        second["pagination_version"] = -1
    elif mutation == "section_registry":
        second["section_registry_sha256"] = "sha256:" + ("a" * 64)
    elif mutation == "section":
        second["section"] = "different-section"
    elif mutation == "section_digest":
        second["section_sha256"] = "sha256:" + ("b" * 64)
    elif mutation == "plan_digest":
        second["page_plan_sha256"] = "sha256:" + ("c" * 64)
    elif mutation == "payload_identity":
        second["payload_sha256"] = "sha256:" + ("d" * 64)
    elif mutation == "body_identity":
        second["body_sha256"] = "sha256:" + ("e" * 64)
    elif mutation == "total_parts":
        second["total_parts"] = int(second["total_parts"]) + 1
    elif mutation == "duplicate_part":
        second["part"] = pages[0]["part"]
    elif mutation == "unknown_format":
        second["content_format"] = "future-format"
    elif mutation in {"gap", "overlap", "duplicate_range"}:
        fields = {
            "raw-text": ("byte_start", "byte_end"),
            "json-scalar-page": ("scalar_byte_start", "scalar_byte_end"),
            "json-array-page": ("element_start", "element_end"),
            "json-element-fragment": (
                "fragment_byte_start",
                "fragment_byte_end",
            ),
        }
        start_field, end_field = fields[str(second["content_format"])]
        if mutation == "gap":
            second[start_field] = int(second[start_field]) + 1
        elif mutation == "overlap":
            second[start_field] = int(second[start_field]) - 1
        else:
            second[start_field] = pages[0][start_field]
            second[end_field] = pages[0][end_field]
    elif mutation == "post_terminal":
        pages[0]["has_more"] = False
        pages[0].pop("next_part", None)
    elif mutation == "terminal_next_part":
        pages[-1]["next_part"] = int(pages[-1]["part"]) + 1
    else:
        raise AssertionError(f"unknown mutation: {mutation}")


@pytest.mark.anyio
async def test_recipe_content_helper_rejects_failed_inline_response() -> None:
    from tests.server._helpers import _resolve_recipe_section

    with pytest.raises(AssertionError, match="recipe response was not successful"):
        await _resolve_recipe_section({"success": False, "content": "stale"})


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("pagination_version", -1, None),
        ("content_format", "future-format", "unknown recipe section format"),
    ],
)
async def test_recipe_section_helper_rejects_unknown_wire_contract(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    match: str | None,
) -> None:
    from autoskillit.core import (
        RECIPE_SECTION_PAGINATION_VERSION,
        RECIPE_SECTION_REGISTRY_DIGEST,
    )
    from tests.server._helpers import _resolve_recipe_section

    response: dict[str, object] = {
        "success": True,
        "pagination_version": RECIPE_SECTION_PAGINATION_VERSION,
        "section_registry_sha256": RECIPE_SECTION_REGISTRY_DIGEST,
        "section": "content",
        "content_format": "raw-text",
        "content": "",
        "part": 0,
        "total_parts": 1,
        "has_more": False,
        "section_sha256": "sha256:" + ("1" * 64),
        "page_plan_sha256": "sha256:" + ("2" * 64),
        "payload_sha256": "sha256:" + ("3" * 64),
        "body_sha256": "sha256:" + ("4" * 64),
        "byte_start": 0,
        "byte_end": 0,
        "byte_total": 0,
    }
    response[field] = value

    async def _page(**_kwargs: object) -> str:
        return json.dumps(response)

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_recipe.get_recipe_section",
        _page,
    )
    result = {
        "success": True,
        "recipe_pull": {
            "pull_tool": "get_recipe_section",
            "payload_sha256": response["payload_sha256"],
            "body_sha256": response["body_sha256"],
        },
    }

    with pytest.raises(AssertionError, match=match):
        await _resolve_recipe_section(result)


@pytest.mark.anyio
@pytest.mark.parametrize("format_family", ["raw", "scalar", "array", "fragment"])
@pytest.mark.parametrize(
    "mutation",
    [
        "pagination_version",
        "section_registry",
        "section",
        "section_digest",
        "plan_digest",
        "payload_identity",
        "body_identity",
        "total_parts",
        "duplicate_part",
        "gap",
        "overlap",
        "duplicate_range",
        "unknown_format",
        "post_terminal",
        "terminal_next_part",
    ],
)
async def test_recipe_section_helper_rejects_invalid_page_sequences(
    monkeypatch: pytest.MonkeyPatch,
    format_family: str,
    mutation: str,
) -> None:
    from tests.server._helpers import _resolve_recipe_section

    pages = copy.deepcopy(_valid_recipe_section_pages(format_family))
    _mutate_recipe_section_pages(pages, mutation)

    async def _page(*, part: int, **_kwargs: object) -> str:
        return json.dumps(pages[part])

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_recipe.get_recipe_section",
        _page,
    )
    result = {
        "success": True,
        "recipe_pull": {
            "pull_tool": "get_recipe_section",
            "payload_sha256": pages[0]["payload_sha256"],
            "body_sha256": pages[0]["body_sha256"],
        },
    }

    with pytest.raises(AssertionError):
        await _resolve_recipe_section(result, section=str(pages[0]["section"]))


class TestGateDisabledSchema:
    """Gate-disabled response schema matches the expected skill result keys."""

    EXPECTED_SKILL_KEYS = {
        "success",
        "result",
        "session_id",
        "subtype",
        "cli_subtype",
        "is_error",
        "exit_code",
        "kill_reason",
        "needs_retry",
        "retry_reason",
        "stderr",
        "token_usage",
        "write_path_warnings",
        "write_call_count",
    }

    def test_gate_disabled_schema(self, tool_ctx):
        """Gate-disabled response has standard keys."""
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server._guards import _require_enabled

        tool_ctx.gate = DefaultGateState(enabled=False)
        response = json.loads(_require_enabled())
        assert set(response.keys()) == self.EXPECTED_SKILL_KEYS
