"""C6: UV_PYTHON_INSTALL_DIR/UV_CACHE_DIR in a child env built by production_interpreter_env()
resolve outside the per-test isolated home."""

from __future__ import annotations

import os

import pytest

from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_uv_caches_resolve_outside_the_isolated_home() -> None:
    home = os.environ.get("HOME", "")
    env = production_interpreter_env()

    for var in ("UV_PYTHON_INSTALL_DIR", "UV_CACHE_DIR"):
        value = env.get(var)
        if value is None:
            pytest.skip(f"{var} not set in this environment (not running under `task`)")
        assert not value.startswith(home) or home == "", (
            f"{var}={value!r} resolves under the per-test isolated home {home!r} -- uv "
            "would re-fetch its cache/interpreter every test instead of sharing one "
            "machine-scoped copy across the run"
        )
