"""One-way lifecycle notifications for recipe-section supporting state."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock

_KitchenRetirementCallback = Callable[[str], None]
_KITCHEN_RETIREMENT_CALLBACKS: set[_KitchenRetirementCallback] = set()
_KITCHEN_RETIREMENT_LOCK = RLock()


def register_kitchen_retirement_callback(callback: _KitchenRetirementCallback) -> None:
    """Register an idempotent recipe-section cleanup callback."""
    with _KITCHEN_RETIREMENT_LOCK:
        _KITCHEN_RETIREMENT_CALLBACKS.add(callback)


def notify_kitchen_retired(kitchen_id: str) -> None:
    """Notify registered supporting state after artifact retirement succeeds."""
    with _KITCHEN_RETIREMENT_LOCK:
        callbacks = tuple(_KITCHEN_RETIREMENT_CALLBACKS)
    for callback in callbacks:
        callback(kitchen_id)
