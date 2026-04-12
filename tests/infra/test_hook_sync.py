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
