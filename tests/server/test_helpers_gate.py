"""Contract tests: server helpers gate response schema."""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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
