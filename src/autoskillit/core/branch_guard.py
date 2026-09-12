"""Backward-compat shim for branch_guard — see core.git.branch_guard."""

from autoskillit.core.git.branch_guard import is_protected_branch

__all__ = ["is_protected_branch"]
