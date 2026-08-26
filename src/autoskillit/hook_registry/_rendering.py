"""Hook command rendering + hooks.json generation.

This module owns the single authoritative formatter for ``hooks.json`` /
``settings.json`` entries. ``_build_hook_entry`` is shared between the two
generation paths so path A/B divergence is structurally impossible —
``test_generate_hooks_json_and_sync_produce_equivalent_entries`` enforces
the equivalence.

Two explicit modes for ``_build_hook_command``:

- ``relocatable=True`` (hooks.json only): emits the quoted
  ``PLUGIN_ROOT_TOKEN`` form, expanded by Claude Code at hook-invocation
  time against the plugin version that supplied the file. ``hooks_dir``
  is ignored and may be ``None``.
- ``relocatable=False`` (settings.json only, dev-mode, machine-local):
  bakes the caller-supplied absolute ``hooks_dir``. ``hooks_dir`` is
  required.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from pathlib import Path

from ._hashing import compute_registry_hash
from ._hooks_defs import (
    _LOGICAL_HOOK_COMPONENT,
    HookDef,
    LifecycleContractDef,
)
from ._registry_data import (
    HOOK_REGISTRY,
    LIFECYCLE_CONTRACTS,
    PLUGIN_ROOT_TOKEN,
    RETIRED_SCRIPT_BASENAMES,
)


def _build_hook_entry(hook_def: HookDef, hook_commands: list[dict]) -> dict:
    """Build the per-entry dict for a hook definition.

    Always-matcherless events (``SessionStart``, ``Stop``) omit the
    ``matcher`` key entirely — Claude Code's documented matcherless event
    schema has no matcher field. ``PreToolUse`` is treated as matcherless
    only when its ``matcher`` is empty (the matcherless PreToolUse entries
    added with REQ-JOIN-005). All other events include ``matcher``.
    This is the single authoritative formatter for both hooks.json and
    settings.json generation.
    """
    if hook_def.event_type in {"SessionStart", "Stop"}:
        return {"hooks": hook_commands}
    if hook_def.event_type == "PreToolUse" and not hook_def.matcher:
        return {"hooks": hook_commands}
    return {"matcher": hook_def.matcher, "hooks": hook_commands}


def _build_hook_command(
    hooks_dir: Path | None,
    script: str,
    timeout_seconds: int | None,
    *,
    relocatable: bool = False,
) -> dict:
    """Build a single hook command dict using the stable dispatcher format."""
    logical_name = script.removesuffix(".py")
    if relocatable:
        command = render_relocatable_hook_command(logical_name)
    else:
        if hooks_dir is None:
            raise ValueError("hooks_dir is required when relocatable=False")
        command = f"python3 -B {hooks_dir / '_dispatch.py'} {logical_name}"
    cmd: dict = {
        "type": "command",
        "command": command,
    }
    if timeout_seconds is not None:
        cmd["timeout"] = timeout_seconds
    return cmd


def render_relocatable_hook_command(logical_name: str) -> str:
    """Render one validated dispatcher command for a plugin hooks artifact."""
    logical_name = logical_name.removesuffix(".py").strip("/")
    components = logical_name.split("/")
    if not logical_name or any(
        _LOGICAL_HOOK_COMPONENT.fullmatch(component) is None for component in components
    ):
        raise ValueError(f"invalid logical hook name: {logical_name!r}")
    return f'python3 -B "{PLUGIN_ROOT_TOKEN}/hooks/_dispatch.py" {shlex.quote(logical_name)}'


def render_hooks_json_text(
    registry: Sequence[HookDef] = HOOK_REGISTRY,
    lifecycle_contracts: Sequence[LifecycleContractDef] = LIFECYCLE_CONTRACTS,
) -> str:
    """Canonical serialization of :func:`generate_hooks_json`.

    This is the single authority for rendered-manifest bytes — every publisher
    (marketplace, self-heal, projection staging, startup drift check) must use
    this function rather than re-deriving the JSON text inline.  Three sites
    consume the same bytes: :func:`write_generated_hooks_json` writes them,
    ``run_startup_drift_check`` compares them, and the projection cache key
    digests them; keeping the serialization in one place prevents the three
    from drifting apart.
    """
    return json.dumps(generate_hooks_json(registry, lifecycle_contracts), indent=2) + "\n"


def generate_hooks_json(
    registry: Sequence[HookDef] = HOOK_REGISTRY,
    lifecycle_contracts: Sequence[LifecycleContractDef] = LIFECYCLE_CONTRACTS,
) -> dict:
    """Generate the hooks.json structure from HOOK_REGISTRY using the stable dispatcher.

    Multiple HookDef entries with the same (event_type, matcher) are consolidated
    into a single settings.json entry so Claude Code sees no duplicate matchers.
    """
    from ._risky_operations import validate_lifecycle_contracts

    validate_lifecycle_contracts(
        registry,
        lifecycle_contracts,
        backend="claude_code",
    )
    # Preserve insertion order; merge scripts from same (event_type, matcher) key.
    groups: dict[tuple[str, str], dict] = {}
    for hook_def in registry:
        key = (hook_def.event_type, hook_def.matcher)
        hook_commands = [
            _build_hook_command(None, script, hook_def.timeout_seconds, relocatable=True)
            for script in hook_def.scripts
        ]
        if key not in groups:
            groups[key] = _build_hook_entry(hook_def, hook_commands)
        else:
            groups[key]["hooks"].extend(hook_commands)

    by_event: dict[str, list] = {}
    for (event_type, _), entry in groups.items():
        by_event.setdefault(event_type, []).append(entry)
    registry_hash = compute_registry_hash(
        registry,
        RETIRED_SCRIPT_BASENAMES,
        lifecycle_contracts,
    )
    return {"hooks": by_event, "_autoskillit_registry_hash": registry_hash}
