"""Asserts shared test helpers export required symbols."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_strip_markdown_code_regions_exported_from_helpers() -> None:
    """strip_markdown_code_regions must be importable from tests._helpers."""
    from tests._helpers import strip_markdown_code_regions

    assert callable(strip_markdown_code_regions)
    result = strip_markdown_code_regions("```python\n@dataclass\n```\nprose")
    assert "@dataclass" not in result
    assert "prose" in result
