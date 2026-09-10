"""Shared fixtures for tests/fleet/."""

from __future__ import annotations

from autoskillit.core import DefaultManagedWorkerCapacity


def worker_capacity_from_ctx(tool_ctx) -> DefaultManagedWorkerCapacity:
    """Return a fresh shared capacity authority for a fleet test context."""
    capacity = DefaultManagedWorkerCapacity(max_concurrent=1)
    tool_ctx.worker_capacity = capacity
    return capacity
