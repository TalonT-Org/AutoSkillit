"""Generated-home projection for attested managed Codex routes."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

from autoskillit.core import ManagedJoinAttestation, atomic_write
from autoskillit.execution.backends import _codex_config as _codex_cfg
from autoskillit.execution.backends._codex_catalog import project_codex_catalog
from autoskillit.execution.backends._codex_hooks import (
    ManagedCodexRoute,
    managed_codex_guard_set,
    managed_codex_mcp_tools,
    sync_managed_codex_hooks_to_config,
)


def _managed_codex_config_errors(
    session_dir: Path,
    *,
    attestation: ManagedJoinAttestation,
    route: ManagedCodexRoute,
) -> list[str]:
    """Validate the generated-home contract that makes a managed route live."""
    errors: list[str] = []
    config_path = session_dir / "config.toml"
    catalog_path = session_dir / "models_cache.json"
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"managed Codex config is unreadable: {type(exc).__name__}: {exc}"]
    if config.get("model") != attestation.resolved_model:
        errors.append("managed Codex config has the wrong resolved model")
    if config.get("model_reasoning_effort") != attestation.resolved_reasoning_effort:
        errors.append("managed Codex config has the wrong resolved reasoning effort")
    server = config.get("mcp_servers", {}).get("autoskillit")
    if not isinstance(server, dict):
        errors.append("managed Codex config has no autoskillit MCP server")
    elif server.get("enabled_tools") != list(managed_codex_mcp_tools(route)):
        errors.append("managed Codex config has a divergent direct-tool allow-list")
    hooks = config.get("hooks")
    rendered_scripts = (
        {
            hook.get("command", "").rsplit(" ", 1)[-1].removeprefix("guards/")
            for entries in hooks.values()
            if isinstance(entries, list)
            for entry in entries
            if isinstance(entry, dict)
            for hook in entry.get("hooks", [])
            if isinstance(hook, dict)
        }
        if isinstance(hooks, dict)
        else set()
    )
    missing_guards = [
        guard for guard in managed_codex_guard_set(route) if guard not in rendered_scripts
    ]
    if missing_guards:
        errors.append(f"managed Codex config is missing guards: {', '.join(missing_guards)}")
    try:
        catalog_bytes = catalog_path.read_bytes()
        catalog = json.loads(catalog_bytes)
        models = catalog["models"]
        selected = [model for model in models if model.get("slug") == attestation.resolved_model]
        if len(selected) != 1:
            raise ValueError("selected model is not unique")
        model = selected[0]
        if model.get("tool_mode") != "direct" or model.get("apply_patch_tool_type") is not None:
            raise ValueError("selected model is not direct-mode projected")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"managed Codex catalog is invalid: {type(exc).__name__}: {exc}")
    else:
        actual_digest = hashlib.sha256(catalog_bytes).hexdigest()
        if actual_digest != attestation.codex_catalog_digest:
            errors.append("managed Codex catalog does not match the attested projection")
    return errors


def project_managed_route(
    backend: object,
    session_dir: Path,
    *,
    attestation: ManagedJoinAttestation,
    route: ManagedCodexRoute,
) -> None:
    """Project one attested route after source-config synchronization."""
    if not attestation.admits_backend("codex"):
        raise ValueError("managed Codex route requires a direct-mode Codex attestation")
    source_codex_home = getattr(backend, "source_codex_home", None)
    if not isinstance(source_codex_home, Path):
        raise ValueError("managed Codex route has no source Codex home")
    source_catalog = source_codex_home / "models_cache.json"
    try:
        projection = project_codex_catalog(
            source_catalog.read_bytes(),
            expected_model=attestation.resolved_model,
            expected_reasoning_effort=attestation.resolved_reasoning_effort,
        )
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"managed Codex route cannot project the installed model catalog: {exc}"
        ) from exc
    if projection.projected_sha256.removeprefix("sha256:") != attestation.codex_catalog_digest:
        raise ValueError("managed Codex catalog differs from the attested projection")
    config_path = session_dir / "config.toml"
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"managed Codex config is unreadable: {exc}") from exc
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        raise ValueError("managed Codex config has no MCP server map")
    autoskillit_server = servers.get("autoskillit")
    if not isinstance(autoskillit_server, dict):
        raise ValueError("managed Codex config has no autoskillit MCP server")
    autoskillit_server["enabled"] = True
    autoskillit_server["enabled_tools"] = list(managed_codex_mcp_tools(route))
    config["model"] = attestation.resolved_model
    config["model_reasoning_effort"] = attestation.resolved_reasoning_effort
    atomic_write(config_path, _codex_cfg._serialize_toml(config))
    atomic_write(session_dir / "models_cache.json", projection.canonical_projected_bytes)
    sync_managed_codex_hooks_to_config(config_path, route=route)
    errors = _managed_codex_config_errors(
        session_dir,
        attestation=attestation,
        route=route,
    )
    if errors:
        raise ValueError("; ".join(errors))
