from __future__ import annotations

import pytest

from autoskillit.core._version_snapshot import collect_version_snapshot


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    collect_version_snapshot.cache_clear()
    yield
    collect_version_snapshot.cache_clear()
