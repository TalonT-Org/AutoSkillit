"""Fixtures shared by smoke-utils tests."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_kitchen_marker(monkeypatch):
    """Prevent read_kitchen_id_from_marker from reading real hook config files.

    Tests that need kitchen_id pass it explicitly — the marker is never consulted.
    """
    monkeypatch.setattr(
        "autoskillit.core.read_kitchen_id_from_marker",
        lambda base=None: "",
    )
