"""Backward-compat shim for _active_kitchens — see core.plugins._active_kitchens."""

from autoskillit.core.plugins._active_kitchens import (
    ActiveKitchensReadResult,
    ActiveKitchensState,
    KitchenProcessIdentity,
    _active_kitchens_corrupt,
    _active_kitchens_lock,
    _active_kitchens_path,
    _check_pid_with_psutil,
    _identity_from_entry,
    _pid_alive,
    _read_active_kitchens_unlocked,
    any_kitchen_open,
    kitchen_entry_alive,
    read_active_kitchens_registry,
    register_active_kitchen,
    sample_kitchen_process_identity,
    unregister_active_kitchen,
)

__all__ = [
    "ActiveKitchensReadResult",
    "ActiveKitchensState",
    "KitchenProcessIdentity",
    "_active_kitchens_corrupt",
    "_active_kitchens_lock",
    "_active_kitchens_path",
    "_check_pid_with_psutil",
    "_identity_from_entry",
    "_pid_alive",
    "_read_active_kitchens_unlocked",
    "any_kitchen_open",
    "kitchen_entry_alive",
    "read_active_kitchens_registry",
    "register_active_kitchen",
    "sample_kitchen_process_identity",
    "unregister_active_kitchen",
]
