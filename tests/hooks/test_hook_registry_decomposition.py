"""Tests for the hook_registry package decomposition (issue #4853).

Asserts the decomposition landed correctly:
- The package exists at src/autoskillit/hook_registry/ with __init__.py
- Every extracted module is at most 750 lines (acceptance criterion)
- HOOK_REGISTRY re-exports the full public API
- Every module stays under the REQ-CNST-010 1000-line hard cap
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import autoskillit.hook_registry

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hook_registry"


def test_hook_registry_is_a_package() -> None:
    """autoskillit.hook_registry must resolve to a package (the __init__.py)."""
    module_file = inspect.getfile(autoskillit.hook_registry)
    assert module_file.endswith("hook_registry/__init__.py"), (
        f"hook_registry resolved to {module_file}; expected the package __init__.py"
    )


def test_every_extracted_module_is_at_most_750_lines() -> None:
    """Acceptance criterion: every module under hook_registry/ ≤ 750 lines."""
    for path in sorted(_SRC.glob("*.py")):
        line_count = sum(1 for _ in path.open())
        assert line_count <= 750, (
            f"{path.name} is {line_count} lines; must be ≤750 per acceptance criterion"
        )


def test_no_module_in_hook_registry_package_exceeds_1000_lines() -> None:
    """REQ-CNST-010 hard cap: every module ≤ 1000 lines."""
    for path in sorted(_SRC.glob("*.py")):
        line_count = sum(1 for _ in path.open())
        assert line_count <= 1000, (
            f"{path.name} is {line_count} lines; must be ≤1000 per REQ-CNST-010"
        )


def test_hook_registry_init_reexports_public_api() -> None:
    """Every public name (and the load-bearing ``_canonical_registry_payload``)
    must remain importable from autoskillit.hook_registry after decomposition.
    """
    public_api_names = [
        "FAIL_CLOSED_GUARD_BASENAMES",
        "HOOKS_DIR",
        "HOOK_REGISTRY",
        "HOOK_REGISTRY_HASH",
        "HookDef",
        "HookDriftResult",
        "LifecycleContractDef",
        "LIFECYCLE_CONTRACTS",
        "NEW_SUBDIR_BASENAMES",
        "PLUGIN_ROOT_TOKEN",
        "RETIRED_SCRIPT_BASENAMES",
        "RISKY_GH_SUBCOMMANDS",
        "RISKY_GIT_OPERATIONS",
        "_LOGICAL_HOOK_COMPONENT",
        "_MATCHERLESS_EVENT_TYPES",
        "_build_hook_command",
        "_build_hook_entry",
        "_canonical_registry_payload",
        "_claude_settings_path",
        "_contract_session_scopes",
        "_count_hook_registry_drift",
        "_extract_script_basenames",
        "_is_own_hook",
        "_load_settings_data",
        "canonical_script_basenames",
        "compute_registry_hash",
        "find_broken_hook_scripts",
        "generate_hooks_json",
        "hook_applies_to_backend",
        "iter_all_scope_paths",
        "render_hooks_json_text",
        "render_relocatable_hook_command",
        "validate_lifecycle_contracts",
        "validate_plugin_cache_hooks",
    ]
    module = autoskillit.hook_registry
    for name in public_api_names:
        assert hasattr(module, name), f"autoskillit.hook_registry lost re-export of {name!r}"
