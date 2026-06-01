"""FastMCP version floor and internal API surface guard.

Ensures the installed FastMCP version meets the project's minimum requirement
and that internal attributes used by test fixtures remain accessible.
"""

from __future__ import annotations

from importlib.metadata import version as pkg_version
from unittest.mock import MagicMock

import pytest
from packaging.version import Version

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]


def test_fastmcp_version_floor():
    """Installed FastMCP must be >= 3.3.1."""
    installed = Version(pkg_version("fastmcp"))
    assert installed >= Version("3.3.1"), f"FastMCP {installed} is below the project minimum 3.3.1"


def test_transforms_attribute_is_list():
    """mcp._transforms must be a list — test fixtures depend on .clear()."""
    from autoskillit.server import mcp

    assert isinstance(mcp._transforms, list)


def test_middleware_context_constructor_stable():
    """MiddlewareContext must accept the kwargs used in test_wire_compat.py."""
    from fastmcp.server.middleware import MiddlewareContext

    ctx = MiddlewareContext(
        message=MagicMock(),
        method="tools/list",
        type="request",
    )
    assert ctx.method == "tools/list"
    assert ctx.type == "request"
