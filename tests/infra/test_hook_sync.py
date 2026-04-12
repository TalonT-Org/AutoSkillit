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
    """quota_check and quota_post_check must derive their path constants from
    _fmt_primitives, not define independent copies.

    After deduplication, HOOK_DIR_COMPONENTS + HOOK_CONFIG_FILENAME from each
    hook module must reconstruct the tuple from _fmt_primitives exactly.
    """
    from autoskillit.hooks._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS
    from autoskillit.hooks.quota_check import HOOK_CONFIG_FILENAME, HOOK_DIR_COMPONENTS
    from autoskillit.hooks.quota_post_check import (
        HOOK_CONFIG_FILENAME as QPC_FILENAME,
    )
    from autoskillit.hooks.quota_post_check import (
        HOOK_DIR_COMPONENTS as QPC_DIR,
    )

    assert (*HOOK_DIR_COMPONENTS, HOOK_CONFIG_FILENAME) == _HOOK_CONFIG_PATH_COMPONENTS, (
        "quota_check path constants must match _fmt_primitives._HOOK_CONFIG_PATH_COMPONENTS"
    )
    assert (*QPC_DIR, QPC_FILENAME) == _HOOK_CONFIG_PATH_COMPONENTS, (
        "quota_post_check path constants must match _fmt_primitives._HOOK_CONFIG_PATH_COMPONENTS"
    )
