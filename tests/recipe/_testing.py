"""Shared helpers for recipe-discovery tests.

The discovery caches in ``recipe._io_loading`` are process-wide, so every test
that exercises ``list_recipes`` needs the same isolation and the same
instrumentation. These helpers are the single definition of both, shared by
``tests/recipe/`` and ``tests/server/test_service_wrappers.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import autoskillit.recipe.io as recipe_io


def isolate_recipe_discovery_cache() -> Iterator[None]:
    """Clear the process-wide discovery caches around one test.

    Intended to be consumed by an autouse fixture: ``yield from`` this in each
    test module that touches discovery, so a warm cache from one test cannot
    change the call counts observed by the next within an xdist worker.
    """
    recipe_io._clear_recipe_discovery_caches()
    yield
    recipe_io._clear_recipe_discovery_caches()


def write_project_recipe(project_dir: Path, name: str) -> Path:
    """Write a minimal valid project recipe and return its path."""
    path = project_dir / ".autoskillit" / "recipes" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"name: {name}\ndescription: test\nsteps: {{}}\n", encoding="utf-8")
    return path


def count_discovery_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Instrument the two discovery stages and return their live call counters.

    The returned dict is mutated in place: ``enumerate`` counts uncached
    per-source enumerations and ``collect`` counts full collection passes, so a
    test can assert exactly which stage a cache hit avoided.
    """
    calls = {"enumerate": 0, "collect": 0}
    enumerate_candidates = recipe_io._enumerate_recipe_candidates_uncached
    collect_candidates = recipe_io.collect_recipes_from_candidates

    def counting_enumeration(*args: Any, **kwargs: Any) -> Any:
        calls["enumerate"] += 1
        return enumerate_candidates(*args, **kwargs)

    def counting_collection(*args: Any, **kwargs: Any) -> Any:
        calls["collect"] += 1
        return collect_candidates(*args, **kwargs)

    monkeypatch.setattr(recipe_io, "_enumerate_recipe_candidates_uncached", counting_enumeration)
    monkeypatch.setattr(recipe_io, "collect_recipes_from_candidates", counting_collection)
    return calls
