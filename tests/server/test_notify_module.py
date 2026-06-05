"""Contract tests: server._notify module."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_notify_module_exports():
    from autoskillit.server._notify import _get_ctx_or_none, _notify, track_response_size

    assert callable(_notify)
    assert callable(track_response_size)
    assert callable(_get_ctx_or_none)
