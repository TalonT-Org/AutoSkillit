from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_hook_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Run every hook test from its per-test temporary project root."""
    monkeypatch.chdir(tmp_path)
