"""
Tests for REQ-DEP-001 through REQ-DEP-004.
Verifies anyio is declared as a direct dependency and the test
infrastructure supports anyio's asyncio backend.
"""

import importlib
import importlib.metadata

import pytest


# REQ-DEP-001: anyio is importable and meets the version constraint
def test_anyio_importable():
    """anyio can be imported — confirms it is installed."""
    import anyio  # noqa: F401


def test_anyio_version_meets_minimum():
    """anyio version satisfies >=4.0 requirement."""
    from packaging.version import Version

    dist_version = importlib.metadata.version("anyio")
    assert Version(dist_version) >= Version("4.0"), f"anyio {dist_version} does not satisfy >=4.0"


# REQ-DEP-002: anyio pytest plugin is active and asyncio backend is configured
def test_anyio_backend_fixture_returns_asyncio(anyio_backend):
    """anyio_backend fixture resolves to 'asyncio'."""
    assert anyio_backend == "asyncio"


@pytest.mark.anyio
async def test_anyio_mark_works():
    """@pytest.mark.anyio runs a coroutine under anyio's asyncio backend."""
    import anyio

    await anyio.sleep(0)  # trivial round-trip through anyio event loop


@pytest.mark.anyio
async def test_anyio_task_group():
    """anyio.create_task_group() operates correctly under asyncio backend."""
    import anyio

    results = []

    async def append(value):
        results.append(value)

    async with anyio.create_task_group() as tg:
        tg.start_soon(append, 1)
        tg.start_soon(append, 2)

    assert sorted(results) == [1, 2]


# REQ-DEP-004: Python version constraint unchanged
def test_python_version_constraint_unchanged():
    """pyproject.toml requires-python remains '>=3.11'."""
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    assert data["project"]["requires-python"] == ">=3.11"


# REQ-DEP-001: anyio is a declared direct dependency (not just transitive)
def test_anyio_is_direct_dependency():
    """pyproject.toml lists anyio as a direct runtime dependency."""
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)

    deps = data["project"]["dependencies"]
    anyio_deps = [d for d in deps if d.startswith("anyio")]
    assert anyio_deps, "anyio is not listed in project.dependencies"
    assert any("4.0" in d or ">=" in d for d in anyio_deps), (
        f"anyio dependency {anyio_deps} does not satisfy >=4.0"
    )
