from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _isolate_hook_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> Iterator[None]:
    """Run every hook test from an isolated root with secure directory modes."""
    previous_umask = os.umask(0o022)
    try:
        monkeypatch.chdir(tmp_path)
        yield
    finally:
        os.umask(previous_umask)
