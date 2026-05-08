"""Runtime annotation test shield for MCP tool readOnlyHint semantics.

Layer 2 — Pre-middleware: internal registry has non-None annotations on every tool.
Layer 3 — Post-middleware: wire output preserves annotations (readOnlyHint survives).
Layer 4 — Universal assertion: every tool has readOnlyHint=True on the wire.

No registry cross-check — the invariant is universal: all tools are read-only.
Layers 1a/1b (AST) live in tests/arch/test_tool_annotation_completeness.py.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestPreMiddlewareAnnotations:
    """Layer 2: mcp.list_tools() (bypasses middleware) must return non-None annotations."""

    @pytest.mark.anyio
    async def test_all_tools_have_annotations_in_registry(self, kitchen_enabled):
        """Every tool in the internal FastMCP registry must have a non-None annotations object
        with a bool readOnlyHint.

        This catches tools that omit annotations= from their @mcp.tool() decorator,
        which leaves them with no readOnlyHint even after the middleware fix.
        """
        from autoskillit.server import mcp

        all_tools = await mcp.list_tools()
        violations: list[str] = []
        for tool in all_tools:
            if tool.annotations is None:
                violations.append(f"  {tool.name!r}: annotations is None")
            elif tool.annotations.readOnlyHint is None:
                violations.append(f"  {tool.name!r}: annotations.readOnlyHint is None")

        assert not violations, (
            "The following tools are missing readOnlyHint in their internal registry entry.\n"
            "Add annotations={'readOnlyHint': True/False} to the @mcp.tool() decorator:\n\n"
            + "\n".join(violations)
        )


class TestPostMiddlewareAnnotations:
    """Layer 3: Client(mcp).list_tools() (through middleware) must preserve annotations."""

    @pytest.mark.anyio
    async def test_annotations_survive_middleware(self, kitchen_enabled):
        """Wire output must preserve annotations.readOnlyHint for every tool.

        If the middleware strips annotations, this test fails — catching any future
        regression that re-introduces unconditional annotation stripping.
        """
        from fastmcp.client import Client

        from autoskillit.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()

        violations: list[str] = []
        for tool in tools:
            if tool.annotations is None:
                violations.append(f"  {tool.name!r}: annotations stripped by middleware")

        assert not violations, (
            "The following tools have annotations=None after passing through the middleware.\n"
            "The middleware must preserve annotations (only strip output_schema and title):\n\n"
            + "\n".join(violations)
        )

    @pytest.mark.anyio
    async def test_all_tools_have_readonly_hint_true(self, kitchen_enabled):
        """Layer 4 — Universal invariant: every tool must have readOnlyHint=True.

        All pipelines operate on independent branches and worktrees with zero
        cross-pipeline interference. readOnlyHint=False serializes parallel tool
        calls and causes catastrophic pipeline slowdowns (40+ minutes instead of
        5 minutes for concurrent CI watches). Zero exceptions.
        """
        from fastmcp.client import Client

        from autoskillit.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()

        violations: list[str] = []
        for tool in tools:
            if tool.annotations is None:
                continue
            if tool.annotations.readOnlyHint is not True:
                violations.append(
                    f"  {tool.name!r}: readOnlyHint={tool.annotations.readOnlyHint!r} "
                    f"(must be True)"
                )

        assert not violations, (
            "Every MCP tool must have readOnlyHint=True. All pipelines operate on "
            "independent branches/worktrees — there is no valid reason for False.\n"
            "Fix the @mcp.tool(annotations=...) decorator:\n\n" + "\n".join(violations)
        )
