"""Runtime metadata parity for response backstop exemptions.

Layer 2 — Pre-middleware: ``mcp.list_tools()`` sees ``anthropic/maxResultSizeChars``
on every tool in ``RESPONSE_BACKSTOP_EXEMPTION_REGISTRY``.

Layer 3 — Post-middleware: ``Client(mcp).list_tools()`` preserves the same meta fields.

Complements the static AST parity check in ``tests/arch/test_response_backstop_parity.py``.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestPreMiddlewareBackstopMeta:
    """Layer 2: internal registry carries the full exemption metadata."""

    @pytest.mark.anyio
    async def test_registry_tools_carry_backstop_meta(self, kitchen_enabled):
        """Every ``RESPONSE_BACKSTOP_EXEMPTION_REGISTRY`` key's live tool object
        must carry ``anthropic/maxResultSizeChars`` matching the registry value.
        """
        from autoskillit.core import ALL_VISIBILITY_TAGS, RESPONSE_BACKSTOP_EXEMPTION_REGISTRY
        from autoskillit.server import mcp

        # Enable all tags so every tool is visible (load_recipe needs kitchen-core)
        mcp._transforms.clear()
        for tag in sorted(ALL_VISIBILITY_TAGS):
            mcp.disable(tags={tag})
        for tag in ("kitchen", "kitchen-core"):
            mcp.enable(tags={tag})

        all_tools = await mcp.list_tools()
        tool_by_name = {t.name: t for t in all_tools}

        violations: list[str] = []
        for tool_name, definition in RESPONSE_BACKSTOP_EXEMPTION_REGISTRY.items():
            tool = tool_by_name.get(tool_name)
            if tool is None:
                violations.append(f"  {tool_name!r}: not found in mcp.list_tools()")
                continue
            meta = getattr(tool, "meta", None) or {}
            actual = meta.get("anthropic/maxResultSizeChars")
            if actual != definition.max_chars:
                violations.append(
                    f"  {tool_name!r}: anthropic/maxResultSizeChars={actual!r}, "
                    f"expected {definition.max_chars}"
                )
            actual_bytes = meta.get("autoskillit/responseBackstopMaxUtf8Bytes")
            if actual_bytes != definition.max_utf8_bytes:
                violations.append(
                    f"  {tool_name!r}: autoskillit/responseBackstopMaxUtf8Bytes="
                    f"{actual_bytes!r}, expected {definition.max_utf8_bytes}"
                )
            actual_measurement = meta.get("autoskillit/responseBackstopMeasurement")
            if actual_measurement != definition.measurement_id:
                violations.append(
                    f"  {tool_name!r}: autoskillit/responseBackstopMeasurement="
                    f"{actual_measurement!r}, expected {definition.measurement_id!r}"
                )

        assert not violations, (
            "The following tools have incorrect or missing response backstop metadata.\n"
            "Add meta=response_backstop_tool_meta(tool_name) to the @mcp.tool() decorator:\n\n"
            + "\n".join(violations)
        )


class TestPostMiddlewareBackstopMeta:
    """Layer 3: wire output preserves backstop meta through middleware."""

    @pytest.mark.anyio
    async def test_backstop_meta_survives_middleware(self, kitchen_enabled):
        """``Client(mcp).list_tools()`` must preserve ``anthropic/maxResultSizeChars``
        for every exempted tool.
        """
        from fastmcp.client import Client

        from autoskillit.core import ALL_VISIBILITY_TAGS, RESPONSE_BACKSTOP_EXEMPTION_REGISTRY
        from autoskillit.server import mcp

        mcp._transforms.clear()
        for tag in sorted(ALL_VISIBILITY_TAGS):
            mcp.disable(tags={tag})
        for tag in ("kitchen", "kitchen-core"):
            mcp.enable(tags={tag})

        async with Client(mcp) as client:
            tools = await client.list_tools()

        tool_by_name = {t.name: t for t in tools}

        violations: list[str] = []
        for tool_name, definition in RESPONSE_BACKSTOP_EXEMPTION_REGISTRY.items():
            tool = tool_by_name.get(tool_name)
            if tool is None:
                violations.append(f"  {tool_name!r}: not in wire output")
                continue
            meta = getattr(tool, "meta", None) or {}
            actual = meta.get("anthropic/maxResultSizeChars")
            if actual != definition.max_chars:
                violations.append(
                    f"  {tool_name!r}: wire anthropic/maxResultSizeChars={actual!r}, "
                    f"expected {definition.max_chars}"
                )

        assert not violations, "Response backstop meta stripped by middleware:\n\n" + "\n".join(
            violations
        )
