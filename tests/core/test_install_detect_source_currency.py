"""Focused source-currency classification coverage."""

from __future__ import annotations

import pytest

from autoskillit.core.install.install_detect import source_currency

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_source_currency_without_generation_is_unknown(tmp_path) -> None:
    result = source_currency(tmp_path, generation_root=None)

    assert result.status == "unknown"
    assert result.generation_root is None
