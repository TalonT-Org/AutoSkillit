"""Shared fixtures for tests/fleet/."""

from __future__ import annotations

from autoskillit.fleet import FleetSemaphore


def fleet_lock_from_ctx(tool_ctx) -> FleetSemaphore:
    """Return a fresh FleetSemaphore for a tool_ctx that needs a fleet lock."""
    lock = FleetSemaphore(max_concurrent=1)
    tool_ctx.fleet_lock = lock
    return lock
