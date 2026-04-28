"""Sync tests: verify parallel stdlib-only hook scripts stay aligned with server code."""

from __future__ import annotations

from pathlib import Path


def test_hook_config_path_components_in_sync():
    """pretty_output._HOOK_CONFIG_PATH_COMPONENTS must resolve to the same path as
    server/helpers._HOOK_DIR_COMPONENTS + _HOOK_CONFIG_FILENAME.

    Both scripts must address the same config file. This test guards against
    independent evolution of the two constant sets.
    """
    from autoskillit.hooks.pretty_output_hook import _HOOK_CONFIG_PATH_COMPONENTS
    from autoskillit.server.helpers import _HOOK_CONFIG_FILENAME, _HOOK_DIR_COMPONENTS

    path_from_pretty = Path(*_HOOK_CONFIG_PATH_COMPONENTS)
    path_from_helpers = Path(*_HOOK_DIR_COMPONENTS) / _HOOK_CONFIG_FILENAME

    assert path_from_pretty == path_from_helpers, (
        f"Hook config path mismatch:\n"
        f"  pretty_output: {path_from_pretty}\n"
        f"  server/helpers: {path_from_helpers}\n"
        "Update the constants to point to the same file."
    )


def test_hook_config_path_single_source_of_truth():
    """_hook_settings must define path constants that match _fmt_primitives.

    After consolidation, quota_guard and quota_post_hook both delegate to
    _hook_settings for config path resolution. This test verifies that
    _hook_settings.HOOK_DIR_COMPONENTS + HOOK_CONFIG_FILENAME reconstructs
    _fmt_primitives._HOOK_CONFIG_PATH_COMPONENTS exactly.
    """
    from autoskillit.hooks._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS
    from autoskillit.hooks._hook_settings import HOOK_CONFIG_FILENAME, HOOK_DIR_COMPONENTS

    assert (*HOOK_DIR_COMPONENTS, HOOK_CONFIG_FILENAME) == _HOOK_CONFIG_PATH_COMPONENTS, (
        "_hook_settings path constants must match _fmt_primitives._HOOK_CONFIG_PATH_COMPONENTS"
    )
