"""Three-layer annotation test shield for MCP tool readOnlyHint semantics.

Layer 2 — Pre-middleware: internal registry has non-None annotations on every tool.
Layer 3 — Post-middleware: wire output preserves annotations (readOnlyHint survives).
Layer 4 — Registry cross-check: decorator values match MUTATING_TOOLS registry.

Together these make annotation regression impossible without immediate test failure.
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
    async def test_readonly_hint_values_match_mutating_registry(self, kitchen_enabled):
        """Layer 4 — Registry cross-check: wire readOnlyHint must match MUTATING_TOOLS.

        - Tools in MUTATING_TOOLS must have readOnlyHint=False on the wire.
        - All other tools must have readOnlyHint=True on the wire.

        This catches drift between the L0 registry and actual decorator values.
        """
        from fastmcp.client import Client

        from autoskillit.core._type_constants import MUTATING_TOOLS
        from autoskillit.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()

        violations: list[str] = []
        for tool in tools:
            if tool.annotations is None:
                # Covered by test_annotations_survive_middleware; skip to avoid duplicate noise
                continue
            expected_readonly = tool.name not in MUTATING_TOOLS
            actual_readonly = tool.annotations.readOnlyHint
            if actual_readonly != expected_readonly:
                registry_label = "MUTATING_TOOLS" if tool.name in MUTATING_TOOLS else "read-only"
                violations.append(
                    f"  {tool.name!r}: readOnlyHint={actual_readonly!r} "
                    f"but registry says {registry_label} "
                    f"(expected readOnlyHint={expected_readonly!r})"
                )

        assert not violations, (
            "The following tools have readOnlyHint values that disagree with MUTATING_TOOLS.\n"
            "Either fix the @mcp.tool(annotations=...) decorator or update MUTATING_TOOLS "
            "in src/autoskillit/core/_type_constants.py:\n\n" + "\n".join(violations)
        )
