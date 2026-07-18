"""Sync tests: verify parallel stdlib-only hook scripts stay aligned with server code."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def test_hook_config_path_components_in_sync():
    """pretty_output._HOOK_CONFIG_PATH_COMPONENTS must resolve to the same path as
    server/_misc._HOOK_DIR_COMPONENTS + _HOOK_CONFIG_FILENAME.

    Both scripts must address the same config file. This test guards against
    independent evolution of the two constant sets.
    """
    from autoskillit.hooks.formatters.pretty_output_hook import _HOOK_CONFIG_PATH_COMPONENTS
    from autoskillit.server._misc import _HOOK_CONFIG_FILENAME, _HOOK_DIR_COMPONENTS

    path_from_pretty = Path(*_HOOK_CONFIG_PATH_COMPONENTS)
    path_from_helpers = Path(*_HOOK_DIR_COMPONENTS) / _HOOK_CONFIG_FILENAME

    assert path_from_pretty == path_from_helpers, (
        f"Hook config path mismatch:\n"
        f"  pretty_output: {path_from_pretty}\n"
        f"  server/_misc: {path_from_helpers}\n"
        "Update the constants to point to the same file."
    )


def test_hook_config_path_single_source_of_truth():
    """_hook_settings must define path constants that match _fmt_primitives.

    After consolidation, quota_guard and quota_post_hook both delegate to
    _hook_settings for config path resolution. This test verifies that
    _hook_settings.HOOK_DIR_COMPONENTS + HOOK_CONFIG_FILENAME reconstructs
    _fmt_primitives._HOOK_CONFIG_PATH_COMPONENTS exactly.
    """
    from autoskillit.hooks._hook_settings import HOOK_CONFIG_FILENAME, HOOK_DIR_COMPONENTS
    from autoskillit.hooks.formatters._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS

    assert (*HOOK_DIR_COMPONENTS, HOOK_CONFIG_FILENAME) == _HOOK_CONFIG_PATH_COMPONENTS, (
        "_hook_settings path constants must match _fmt_primitives._HOOK_CONFIG_PATH_COMPONENTS"
    )


def test_response_spill_schema_mirror_matches_server_contract():
    """The standalone formatter's trust schema must exactly mirror the producer."""
    from autoskillit.hooks.formatters.pretty_output_hook import (
        _RESPONSE_SPILL_METADATA_KEY,
        _RESPONSE_SPILL_METADATA_KEYS,
        _RESPONSE_SPILL_REASONS,
        _RESPONSE_SPILL_SCHEMA_DIGEST,
        _RESPONSE_SPILL_SCHEMA_VERSION,
    )
    from autoskillit.server._response_budget import (
        RESPONSE_SPILL_METADATA_KEY,
        RESPONSE_SPILL_METADATA_KEYS,
        RESPONSE_SPILL_REASONS,
        RESPONSE_SPILL_SCHEMA_DIGEST,
        RESPONSE_SPILL_SCHEMA_VERSION,
    )

    assert _RESPONSE_SPILL_METADATA_KEY == RESPONSE_SPILL_METADATA_KEY
    assert _RESPONSE_SPILL_METADATA_KEYS == RESPONSE_SPILL_METADATA_KEYS
    assert _RESPONSE_SPILL_REASONS == RESPONSE_SPILL_REASONS
    assert _RESPONSE_SPILL_SCHEMA_VERSION == RESPONSE_SPILL_SCHEMA_VERSION
    assert _RESPONSE_SPILL_SCHEMA_DIGEST == RESPONSE_SPILL_SCHEMA_DIGEST


def test_response_backstop_exemption_mirror_matches_core_registry():
    """The standalone formatter mirror must match the canonical measured registry."""
    from autoskillit.core import (
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST,
    )
    from autoskillit.hooks.formatters.pretty_output_hook import (
        _RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
        _RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST,
    )

    canonical = {
        tool_name: definition._asdict()
        for tool_name, definition in RESPONSE_BACKSTOP_EXEMPTION_REGISTRY.items()
    }
    assert _RESPONSE_BACKSTOP_EXEMPTION_REGISTRY == canonical
    assert (
        _RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST == RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST
    )


def test_quota_guard_deny_trigger_sync():
    """QUOTA_GUARD_DENY_TRIGGER must be identical in core and quota_guard."""
    from autoskillit.core import QUOTA_GUARD_DENY_TRIGGER
    from autoskillit.hooks.guards.quota_guard import QUOTA_GUARD_DENY_TRIGGER as _HOOK

    assert QUOTA_GUARD_DENY_TRIGGER == _HOOK, (
        f"QUOTA_GUARD_DENY_TRIGGER mismatch: "
        f"core={QUOTA_GUARD_DENY_TRIGGER!r} vs quota_guard={_HOOK!r}"
    )


def test_quota_budget_exceeded_trigger_sync():
    """QUOTA_BUDGET_EXCEEDED_TRIGGER must be identical in core and quota_guard."""
    from autoskillit.core import QUOTA_BUDGET_EXCEEDED_TRIGGER
    from autoskillit.hooks.guards.quota_guard import QUOTA_BUDGET_EXCEEDED_TRIGGER as _HOOK

    assert QUOTA_BUDGET_EXCEEDED_TRIGGER == _HOOK, (
        f"QUOTA_BUDGET_EXCEEDED_TRIGGER mismatch: "
        f"core={QUOTA_BUDGET_EXCEEDED_TRIGGER!r} vs quota_guard={_HOOK!r}"
    )


def test_quota_post_warning_trigger_sync():
    """QUOTA_POST_WARNING_TRIGGER must be identical in core and quota_post_hook."""
    from autoskillit.core import QUOTA_POST_WARNING_TRIGGER
    from autoskillit.hooks.quota_post_hook import QUOTA_POST_WARNING_TRIGGER as _HOOK

    assert QUOTA_POST_WARNING_TRIGGER == _HOOK, (
        f"QUOTA_POST_WARNING_TRIGGER mismatch: "
        f"core={QUOTA_POST_WARNING_TRIGGER!r} vs quota_post_hook={_HOOK!r}"
    )


def test_quota_post_budget_exceeded_trigger_sync():
    """QUOTA_POST_BUDGET_EXCEEDED_TRIGGER must be identical in core and quota_post_hook."""
    from autoskillit.core import QUOTA_POST_BUDGET_EXCEEDED_TRIGGER
    from autoskillit.hooks.quota_post_hook import QUOTA_POST_BUDGET_EXCEEDED_TRIGGER as _HOOK

    assert QUOTA_POST_BUDGET_EXCEEDED_TRIGGER == _HOOK, (
        f"QUOTA_POST_BUDGET_EXCEEDED_TRIGGER mismatch: "
        f"core={QUOTA_POST_BUDGET_EXCEEDED_TRIGGER!r} vs quota_post_hook={_HOOK!r}"
    )


def test_ingredient_lock_deny_trigger_sync():
    """INGREDIENT_LOCK_DENY_TRIGGER must be identical in server and hook guard."""
    from autoskillit.hooks.guards.ingredient_lock_guard import INGREDIENT_LOCK_DENY_TRIGGER
    from autoskillit.server.tools.tools_execution import INGREDIENT_LOCK_DENY_PREFIX

    assert INGREDIENT_LOCK_DENY_PREFIX == INGREDIENT_LOCK_DENY_TRIGGER, (
        f"Ingredient lock deny trigger mismatch: "
        f"server={INGREDIENT_LOCK_DENY_PREFIX!r} vs "
        f"hook={INGREDIENT_LOCK_DENY_TRIGGER!r}"
    )


def test_recipe_read_deny_trigger_sync():
    """RECIPE_READ_DENY_TRIGGER must be identical in server guards and hook guard."""
    from autoskillit.hooks.guards.recipe_read_guard import RECIPE_READ_DENY_TRIGGER as _HOOK
    from autoskillit.server._guards import RECIPE_READ_DENY_TRIGGER as _SERVER

    assert _SERVER == _HOOK, (
        f"Recipe read deny trigger mismatch: server={_SERVER!r} vs hook={_HOOK!r}"
    )
