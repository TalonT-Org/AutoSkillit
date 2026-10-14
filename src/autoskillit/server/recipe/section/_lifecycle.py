"""One-way lifecycle notifications for recipe-section supporting state."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from autoskillit.core import get_logger
from autoskillit.pipeline import KitchenActivationId

logger = get_logger(__name__)

_ActivationRetirementCallback = Callable[[KitchenActivationId], None]
_ACTIVATION_RETIREMENT_CALLBACKS: set[_ActivationRetirementCallback] = set()
_ACTIVATION_RETIREMENT_LOCK = RLock()


def register_activation_retirement_callback(callback: _ActivationRetirementCallback) -> None:
    """Register an idempotent recipe-section cleanup callback."""
    with _ACTIVATION_RETIREMENT_LOCK:
        _ACTIVATION_RETIREMENT_CALLBACKS.add(callback)


def notify_activation_retired(activation_id: KitchenActivationId) -> None:
    """Notify registered supporting state when a kitchen activation is retired."""
    with _ACTIVATION_RETIREMENT_LOCK:
        callbacks = tuple(_ACTIVATION_RETIREMENT_CALLBACKS)
    for callback in callbacks:
        try:
            callback(activation_id)
        except Exception:
            callback_module = getattr(callback, "__module__", type(callback).__module__)
            callback_name = getattr(callback, "__qualname__", type(callback).__qualname__)
            logger.warning(
                "recipe_section_retirement_callback_failed",
                activation_id=activation_id.value,
                callback=f"{callback_module}.{callback_name}",
                exc_info=True,
            )
