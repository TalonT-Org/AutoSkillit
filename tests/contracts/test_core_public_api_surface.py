"""Validates that every symbol in autoskillit.core.__all__ is importable via the public gateway."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_all_public_symbols_importable() -> None:
    """Every symbol in autoskillit.core.__all__ must be importable via 'from autoskillit.core import X'."""
    import autoskillit.core as core_module

    failures: list[str] = []
    for symbol in core_module.__all__:
        try:
            result = getattr(core_module, symbol)
            assert result is not None or True  # trigger the lazy-load
        except (ImportError, AttributeError) as exc:
            failures.append(f"{symbol}: {exc}")
    assert not failures, "Public API surface broken:\n" + "\n".join(failures)
