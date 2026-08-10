"""Codex config.toml hook generation and synchronization.

Canonical implementation at IL-1 — importable by both CLI (IL-3) and
execution/backends (IL-1) without layer violations.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from autoskillit.core import (
    _AUTOSKILLIT_PLUGIN_KEY,
    atomic_write,
    get_logger,
    installed_plugin_artifact_root,
    installed_plugin_semantic_key,
    read_installed_plugin_artifact_identity,
)
from autoskillit.execution.backends._codex_config import (
    _read_codex_config,
    _serialize_toml,
    _write_codex_config,
)
from autoskillit.execution.backends._codex_config_lock import CodexConfigLock
from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    LIFECYCLE_CONTRACTS,
    HookDef,
    LifecycleContractDef,
    _build_hook_entry,
    hook_applies_to_backend,
    validate_lifecycle_contracts,
)

logger = get_logger(__name__)


class CodexHooksDurableRootUnavailable(RuntimeError):
    """No durable hooks directory is available for Codex configuration.

    Raised when ``_resolve_codex_hooks_dir`` cannot find any live generation
    store or legacy installed cache with ``_dispatch.py``.  The ``HOOKS_DIR``
    (dev-checkout) terminal fallback is intentionally excluded: its lifetime
    is shorter than the config artifact's, exactly the class instance this
    error exists to prevent.
    """


def find_broken_codex_hook_commands(config_path: Path | None = None) -> list[str]:
    """Detect broken autoskillit hook commands in ``~/.codex/config.toml``.

    Returns a list of broken command strings (empty if all healthy or no
    autoskillit hooks are present).  Does not modify the config.
    """
    if config_path is None:
        config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.is_file():
        return []
    result = _read_codex_config(config_path)
    broken: list[str] = []
    hooks = result.data.get("hooks", [])
    if not isinstance(hooks, list):
        return []
    for entry in hooks:
        if not isinstance(entry, dict):
            continue
        cmd = entry.get("command", "")
        if not isinstance(cmd, str) or not cmd:
            continue
        if "/autoskillit/" not in cmd and "_dispatch.py" not in cmd:
            continue
        # Check: does the dispatcher target exist?
        import shlex

        try:
            parts = shlex.split(cmd)
        except ValueError:
            broken.append(cmd)
            continue
        if len(parts) >= 3 and parts[-2].endswith("_dispatch.py"):
            if not Path(parts[-2]).is_file():
                broken.append(cmd)
        elif len(parts) >= 2:
            script = parts[-1]
            if not Path(script).is_file():
                broken.append(cmd)
    return broken


def _resolve_codex_hooks_dir(plugin_dir: Path | None = None) -> Path:
    """Select a durable absolute dispatcher root for Codex configuration.

    When ``plugin_dir`` is supplied (a session's validated generation path),
    the hooks tree inside that directory is used directly.  When ``None``
    (bindingless callers such as MCP server startup and ``init``), a
    short-lived resolve→validate of the current generation selector is
    performed through the same generation-store authority as launch binding.

    If neither the generation store nor the legacy installed cache supplies a
    dispatcher, raises :class:`CodexHooksDurableRootUnavailable` instead of
    falling back to the dev-checkout hooks directory (whose lifetime is shorter
    than the config artifact's — the exact class instance this guard prevents).
    """
    if plugin_dir is not None:
        candidate = plugin_dir / "hooks"
        if (candidate / "_dispatch.py").is_file():
            return candidate
        raise RuntimeError(f"validated plugin_dir {plugin_dir} has no hooks/_dispatch.py")

    # Bindingless path: resolve from generation store with short-lived lease
    from autoskillit import __version__
    from autoskillit.core import resolve_current_generation

    generation_dir = resolve_current_generation(Path.home(), "autoskillit", __version__)
    if generation_dir is not None:
        candidate = generation_dir / "hooks"
        if (candidate / "_dispatch.py").is_file():
            return candidate

    # Fall back to legacy installed cache
    cache_root = installed_plugin_artifact_root(Path.home(), "autoskillit", __version__)
    try:
        identity = read_installed_plugin_artifact_identity(
            cache_root,
            expected_semantic_key=installed_plugin_semantic_key(
                _AUTOSKILLIT_PLUGIN_KEY,
                __version__,
            ),
        )
    except Exception:
        raise CodexHooksDurableRootUnavailable(
            "No durable hooks directory available for Codex config. "
            "Candidates checked: generation store (absent), legacy installed "
            f"cache at {cache_root} (identity unreadable). "
            "Remedy: run `autoskillit install` from an external terminal."
        )
    cache_hooks_dir = identity.managed_path / "hooks"
    if (cache_hooks_dir / "_dispatch.py").is_file():
        return cache_hooks_dir
    raise CodexHooksDurableRootUnavailable(
        f"No durable hooks directory available for Codex config. "
        f"Legacy cache {cache_hooks_dir} exists but is missing _dispatch.py. "
        f"Remedy: run `autoskillit install` from an external terminal."
    )


def _build_codex_hook_command(hooks_dir: Path, script: str, timeout_seconds: int | None) -> dict:
    """Build a single Codex hook command dict with trusted_hash."""
    logical_name = script.removesuffix(".py")
    dispatch_path = hooks_dir / "_dispatch.py"
    script_hash = hashlib.sha256(dispatch_path.read_bytes()).hexdigest()
    cmd: dict = {
        "type": "command",
        "command": f"python3 -B {dispatch_path} {logical_name}",
        "trusted_hash": script_hash,
    }
    if timeout_seconds is not None:
        cmd["timeout"] = timeout_seconds
    return cmd


def generate_codex_hooks_config(
    hook_config_format: str = "",
    *,
    registry: Sequence[HookDef] = HOOK_REGISTRY,
    lifecycle_contracts: Sequence[LifecycleContractDef] = LIFECYCLE_CONTRACTS,
    plugin_dir: Path | None = None,
) -> dict[str, list[dict]]:
    """Generate Codex config.toml hooks entries from HOOK_REGISTRY.

    Skips interactive_only and codex fix-required/not-applicable hooks.
    Returns dict keyed by event type for [[hooks.<EventType>]] TOML format.
    """
    validate_lifecycle_contracts(
        registry,
        lifecycle_contracts,
        backend="codex",
    )
    hooks_dir = _resolve_codex_hooks_dir(plugin_dir)
    groups: dict[str, dict[tuple[str, str], dict]] = {}
    for hook_def in registry:
        if not hook_applies_to_backend(
            hook_def,
            backend="codex",
            session_scope="headless",
        ):
            continue
        event = hook_def.event_type
        key = (event, hook_def.matcher)
        hook_commands = [
            _build_codex_hook_command(hooks_dir, script, hook_def.timeout_seconds)
            for script in hook_def.scripts
        ]
        if event not in groups:
            groups[event] = {}
        if key not in groups[event]:
            entry = _build_hook_entry(hook_def, hook_commands)
            groups[event][key] = entry
        else:
            groups[event][key]["hooks"].extend(hook_commands)
    return {event: list(entries.values()) for event, entries in groups.items()}


def _is_autoskillit_hook_entry(entry: dict) -> bool:
    """Check if a Codex hooks config entry belongs to autoskillit.

    ``_resolve_codex_hooks_dir()`` varies by install state (dev checkout vs.
    retained plugin-cache incarnation), so detection cannot pin one literal
    directory string — the ``_dispatch.py`` suffix match already covers every
    autoskillit-generated command regardless of which root produced it.
    """
    for hook in entry.get("hooks", []):
        cmd = hook.get("command", "")
        if "/autoskillit/" in cmd or "_dispatch.py" in cmd:
            return True
    return False


def _upsert_hooks_text(
    config_path: Path, raw_bytes: bytes, fresh_hooks: dict[str, list[dict]]
) -> None:
    """Replace autoskillit-owned hook blocks in raw config text and append fresh ones."""
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"config file contains non-UTF-8 bytes: {exc}") from exc
    lines = text.splitlines(keepends=True)

    owned_ranges: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "[[hooks]]" or (
            stripped.startswith("[[hooks.") and stripped.endswith("]]")
        ):
            block_start = i
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("["):
                i += 1
            block_end = i
            block_text = "".join(lines[block_start:block_end])
            if "/autoskillit/" in block_text or "_dispatch.py" in block_text:
                owned_ranges.append((block_start, block_end))
        else:
            i += 1

    for start, end in reversed(owned_ranges):
        del lines[start:end]

    fresh_text = _serialize_toml({"hooks": fresh_hooks})
    result_text = "".join(lines).rstrip("\n") + "\n\n" + fresh_text
    atomic_write(config_path, result_text)


def _sync_hooks_to_codex_config_unlocked(
    config_path: Path,
    *,
    hook_config_format: str = "",
    plugin_dir: Path | None = None,
) -> bool:
    """Mutate hook entries while the caller owns the Codex config lock.

    Returns True if the config was changed, False if already up to date.
    """
    result = _read_codex_config(config_path)
    if result.is_corrupt:
        if result.raw_bytes is None:
            raise RuntimeError("corrupt ReadResult has no raw_bytes")
        fresh = generate_codex_hooks_config(
            hook_config_format=hook_config_format,
            plugin_dir=plugin_dir,
        )
        _upsert_hooks_text(config_path, result.raw_bytes, fresh)
        return True

    config = result.data
    existing_hooks = config.get("hooks", {})
    if isinstance(existing_hooks, list):
        existing_hooks = {}
    foreign_hooks: dict[str, list[dict]] = {}
    for event_type, entries in existing_hooks.items():
        if not isinstance(entries, list):
            continue
        foreign = [e for e in entries if not _is_autoskillit_hook_entry(e)]
        if foreign:
            foreign_hooks[event_type] = foreign
    fresh = generate_codex_hooks_config(
        hook_config_format=hook_config_format,
        plugin_dir=plugin_dir,
    )
    merged: dict[str, list[dict]] = {}
    for event_type in set(list(foreign_hooks.keys()) + list(fresh.keys())):
        merged[event_type] = foreign_hooks.get(event_type, []) + fresh.get(event_type, [])
    if merged == existing_hooks:
        return False
    config["hooks"] = merged
    _write_codex_config(config_path, config, source=result)
    return True


def sync_hooks_to_codex_config(
    config_path: Path | None = None,
    *,
    hook_config_format: str = "",
    plugin_dir: Path | None = None,
) -> bool:
    """Sync autoskillit hooks under the shared Codex config lock."""
    resolved_config_path = (
        (Path.home() / ".codex" / "config.toml" if config_path is None else Path(config_path))
        .expanduser()
        .resolve(strict=False)
    )
    with CodexConfigLock(resolved_config_path):
        return _sync_hooks_to_codex_config_unlocked(
            config_path=resolved_config_path,
            hook_config_format=hook_config_format,
            plugin_dir=plugin_dir,
        )
