"""Codex command-time runtime-home validation and environment binding."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import (
    CODEX_HOME_ENV_VAR,
    CODEX_RESERVED_HOME_ENV_VARS,
    PluginLaunchBinding,
    SkillDiscoveryRouteDef,
)
from autoskillit.execution.backends._codex_discovery import (
    CODEX_MANAGED_HOME_ROUTE,
    CODEX_PROJECTED_HOME_ROUTE,
)


def _configure_interactive_home(
    *,
    route: SkillDiscoveryRouteDef | None,
    generated_home: Path | None,
    plugin_binding: PluginLaunchBinding | None,
    base_env: dict[str, str],
    merged_extras: dict[str, str],
) -> tuple[tuple[str, str], ...]:
    if route is CODEX_MANAGED_HOME_ROUTE:
        if generated_home is None:
            raise ValueError("managed discovery route requires a generated home")
        for reserved_key in CODEX_RESERVED_HOME_ENV_VARS:
            merged_extras[reserved_key] = str(generated_home)
        return ()
    if route is CODEX_PROJECTED_HOME_ROUTE:
        if plugin_binding is None:
            raise ValueError("projected discovery route requires a plugin binding")
        projected_home = plugin_binding.plugin_dir
        if projected_home is None or not projected_home.is_dir():
            raise ValueError("projected CODEX_HOME must be an existing directory")
        if projected_home != projected_home.resolve(strict=True):
            raise ValueError("projected CODEX_HOME must already be canonical")
        if not plugin_binding.skill_entries:
            raise ValueError("projected CODEX_HOME requires nonempty skill entries")
        base_env.pop("CODEX_SQLITE_HOME", None)
        merged_extras.pop("CODEX_SQLITE_HOME", None)
        merged_extras[CODEX_HOME_ENV_VAR] = str(projected_home)
        return plugin_binding.skill_entries
    if route is not None:
        raise ValueError(f"unsupported Codex interactive discovery route: {route.name}")
    if plugin_binding is None:
        return ()
    merged_extras.setdefault(CODEX_HOME_ENV_VAR, str(plugin_binding.plugin_dir))
    return ()


def _canonical_generated_home(home: Path | str, *, argument_name: str) -> Path:
    """Validate the generated child runtime home shared by every Codex builder."""
    supplied_home = Path(home)
    if not supplied_home.is_absolute():
        raise ValueError(f"{argument_name} must be absolute")
    generated_home = supplied_home.expanduser().resolve(strict=False)
    if supplied_home != generated_home:
        raise ValueError(f"{argument_name} must already be canonical")
    return generated_home


def _generated_home_config_overrides(generated_home: Path) -> dict[str, str]:
    """Keep the app-server's per-thread SQLite location bound to its wrapper home."""
    return {"sqlite_home": str(generated_home)}
