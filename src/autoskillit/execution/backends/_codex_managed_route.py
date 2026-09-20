"""Generated-home projection for attested managed Codex routes."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    CODEX_EFFORT_MAPPING,
    CODEX_VALID_MODEL_IDS,
    ManagedJoinAttestation,
    atomic_write,
    strip_context_window_suffix,
)
from autoskillit.execution.backends import _codex_config as _codex_cfg
from autoskillit.execution.backends._codex_catalog import (
    CodexCatalogProjection,
    project_codex_catalog,
)
from autoskillit.execution.backends._codex_discovery import CODEX_MANAGED_HOME_ROUTE
from autoskillit.execution.backends._codex_hooks import (
    ManagedCodexRoute,
    managed_codex_guard_set,
    managed_codex_mcp_tools,
    sync_managed_codex_hooks_to_config,
)

if TYPE_CHECKING:
    from autoskillit.execution.backends.codex import CodexBackend


def resolve_managed_parent_identity(
    backend: CodexBackend,
    configured_model: str,
) -> tuple[str, str]:
    """Resolve a managed Codex model and its effective reasoning effort."""
    model = backend.translate_model(configured_model)
    if model not in CODEX_VALID_MODEL_IDS:
        raise ValueError(f"unsupported managed Codex model: {model}")
    effort = CODEX_EFFORT_MAPPING.get(strip_context_window_suffix(configured_model))
    if effort is None:
        source_home = backend.source_codex_home
        if source_home is None:
            raise ValueError("managed Codex route has no source Codex home")
        catalog = json.loads((source_home / "models_cache.json").read_text(encoding="utf-8"))
        matches = [entry for entry in catalog["models"] if entry.get("slug") == model]
        if len(matches) != 1:
            raise ValueError(f"managed Codex catalog does not contain {model}")
        effort = matches[0].get("default_reasoning_level")
    if not isinstance(effort, str) or not effort:
        raise ValueError(f"managed Codex model {model} has no default reasoning level")
    return model, effort


def project_source_catalog(
    backend: CodexBackend,
    model: str,
    effort: str,
) -> CodexCatalogProjection:
    """Project a direct-mode managed catalog from the configured Codex home."""
    source_home = backend.source_codex_home
    if source_home is None:
        raise ValueError("managed Codex route has no source Codex home")
    return project_codex_catalog(
        (source_home / "models_cache.json").read_bytes(),
        expected_model=model,
        expected_reasoning_effort=effort,
    )


def projected_manifest_path(backend: CodexBackend, generated_home: Path) -> Path:
    """Return the managed projection manifest associated with a generated home."""
    del backend
    catalog = CODEX_MANAGED_HOME_ROUTE.catalog_dir(generated_home)
    return catalog.parent / f".{catalog.name}.autoskillit-projection.json"


def verify_managed_session_dir(
    backend: CodexBackend,
    generated_home: Path,
    attestation: ManagedJoinAttestation,
    route: ManagedCodexRoute,
) -> list[str]:
    """Validate the live generated home against its managed-route attestation."""
    del backend
    return _managed_codex_config_errors(generated_home, attestation=attestation, route=route)


def _rendered_codex_guard_scripts(hooks: object) -> set[str]:
    """Collect guard script names from the rendered Codex hook tables."""
    if not isinstance(hooks, dict):
        return set()
    return {
        command.rsplit(" ", 1)[-1].removeprefix("guards/")
        for entries in hooks.values()
        if isinstance(entries, list)
        for entry in entries
        if isinstance(entry, dict)
        for hook in entry.get("hooks", [])
        if isinstance(hook, dict)
        for command in (hook.get("command"),)
        if isinstance(command, str)
    }


def _managed_codex_catalog_error(
    catalog_path: Path,
    *,
    attestation: ManagedJoinAttestation,
) -> str | None:
    """Validate the projected catalog and its attested digest."""
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
        return f"managed Codex catalog is invalid: {type(exc).__name__}: {exc}"

    actual_digest = hashlib.sha256(catalog_bytes).hexdigest()
    if actual_digest != attestation.codex_catalog_digest:
        return "managed Codex catalog does not match the attested projection"
    return None


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
    else:
        allowed_tools = managed_codex_mcp_tools(route)
        if (allowed_tools is None and "enabled_tools" in server) or (
            allowed_tools is not None and server.get("enabled_tools") != list(allowed_tools)
        ):
            errors.append("managed Codex config has a divergent direct-tool allow-list")
    rendered_scripts = _rendered_codex_guard_scripts(config.get("hooks"))
    missing_guards = [
        guard for guard in managed_codex_guard_set(route) if guard not in rendered_scripts
    ]
    if missing_guards:
        errors.append(f"managed Codex config is missing guards: {', '.join(missing_guards)}")
    catalog_error = _managed_codex_catalog_error(catalog_path, attestation=attestation)
    if catalog_error is not None:
        errors.append(catalog_error)
    return errors


def project_managed_route(
    backend: CodexBackend,
    session_dir: Path,
    *,
    attestation: ManagedJoinAttestation,
    route: ManagedCodexRoute,
) -> None:
    """Project one attested route after source-config synchronization."""
    if not attestation.admits_backend("codex"):
        raise ValueError("managed Codex route requires a direct-mode Codex attestation")
    try:
        projection = backend.project_source_catalog(
            attestation.resolved_model, attestation.resolved_reasoning_effort
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
    allowed_tools = managed_codex_mcp_tools(route)
    if allowed_tools is None:
        autoskillit_server.pop("enabled_tools", None)
    else:
        autoskillit_server["enabled_tools"] = list(allowed_tools)
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
