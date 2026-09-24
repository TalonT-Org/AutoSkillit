"""Generated-home projection for attested managed Codex routes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    CODEX_EFFORT_MAPPING,
    CODEX_VALID_MODEL_IDS,
    ContainmentError,
    ManagedCodexRoute,
    ManagedJoinAttestation,
    SemanticAdaptationContext,
    atomic_write,
    read_stable_contained_bytes,
    strip_context_window_suffix,
)
from autoskillit.execution.backends import _codex_config as _codex_cfg
from autoskillit.execution.backends._codex_catalog import (
    CODEX_CATALOG_LIMIT,
    CodexCatalogAcquisitionError,
    CodexCatalogProjection,
    acquire_bundled_codex_catalog,
    project_codex_catalog,
    resolve_codex_catalog_effort,
)
from autoskillit.execution.backends._codex_discovery import CODEX_MANAGED_HOME_ROUTE
from autoskillit.execution.backends._codex_hooks import (
    managed_codex_guard_set,
    managed_codex_mcp_tools,
    sync_managed_codex_hooks_to_config,
)

if TYPE_CHECKING:
    from autoskillit.execution.backends.codex import CodexBackend

_MANAGED_CATALOG_FILENAME = "autoskillit-models.json"


def prepare_managed_codex_catalog(
    backend: CodexBackend,
    configured_model: str,
    *,
    scratch_root: Path,
    deadline: float,
) -> tuple[str, str, CodexCatalogProjection]:
    """Acquire and project the installed bundled catalog for managed issuance."""
    if not backend.capabilities.managed_fixed_batch_route_capable:
        raise ValueError("backend has no managed fixed-batch route")
    model = backend.translate_model(configured_model)
    if model not in CODEX_VALID_MODEL_IDS:
        raise ValueError(f"unsupported managed Codex model: {model}")
    codex = shutil.which(backend.binary_name())
    if codex is None:
        raise CodexCatalogAcquisitionError("codex_unavailable")
    raw_catalog = acquire_bundled_codex_catalog(
        codex,
        scratch_root=scratch_root,
        environment=os.environ,
        deadline=deadline,
    )
    effort = CODEX_EFFORT_MAPPING.get(strip_context_window_suffix(configured_model))
    if effort is None:
        effort = resolve_codex_catalog_effort(raw_catalog, expected_model=model)
    projection = project_codex_catalog(
        raw_catalog,
        expected_model=model,
        expected_reasoning_effort=effort,
    )
    return model, effort, projection


def read_managed_codex_catalog(backend: CodexBackend, generated_home: Path) -> bytes:
    """Read one stable, bounded catalog snapshot from a generated home."""
    del backend
    try:
        _, catalog = read_stable_contained_bytes(
            generated_home / _MANAGED_CATALOG_FILENAME,
            generated_home,
            max_size_bytes=CODEX_CATALOG_LIMIT,
        )
    except (ContainmentError, OSError) as exc:
        raise ValueError(f"managed Codex catalog is unreadable: {exc}") from exc
    return catalog


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
    *,
    managed_codex_catalog: bytes | None = None,
) -> list[str]:
    """Validate the live generated home against its managed-route attestation."""
    if managed_codex_catalog is None:
        try:
            managed_codex_catalog = read_managed_codex_catalog(backend, generated_home)
        except ValueError as exc:
            return [str(exc)]
    return _managed_codex_config_errors(
        generated_home,
        attestation=attestation,
        route=route,
        managed_codex_catalog=managed_codex_catalog,
    )


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
    catalog_bytes: bytes,
    *,
    attestation: ManagedJoinAttestation,
) -> str | None:
    """Validate the projected catalog and its attested digest."""
    try:
        catalog = json.loads(catalog_bytes)
        models = catalog["models"]
        if not isinstance(models, list) or not all(isinstance(model, dict) for model in models):
            raise TypeError("models must be a list of objects")
        selected = [model for model in models if model.get("slug") == attestation.resolved_model]
        if len(selected) != 1:
            raise ValueError("selected model is not unique")
        model = selected[0]
        if model.get("tool_mode") != "direct" or model.get("apply_patch_tool_type") is not None:
            raise ValueError("selected model is not direct-mode projected")
        if model.get("upgrade") is not None:
            raise ValueError("offers a model migration")
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
    managed_codex_catalog: bytes,
) -> list[str]:
    """Validate the generated-home contract that makes a managed route live."""
    errors: list[str] = []
    config_path = session_dir / "config.toml"
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"managed Codex config is unreadable: {type(exc).__name__}: {exc}"]
    if config.get("model") != attestation.resolved_model:
        errors.append(
            "managed Codex config has the wrong resolved model "
            f"(attested {attestation.resolved_model!r}, found {config.get('model')!r})"
        )
    if config.get("model_reasoning_effort") != attestation.resolved_reasoning_effort:
        errors.append(
            "managed Codex config has the wrong resolved reasoning effort "
            f"(attested {attestation.resolved_reasoning_effort!r}, "
            f"found {config.get('model_reasoning_effort')!r})"
        )
    expected_catalog_path = str((session_dir / _MANAGED_CATALOG_FILENAME).resolve())
    if config.get("model_catalog_json") != expected_catalog_path:
        errors.append(
            "managed Codex config has the wrong resolved model catalog path "
            f"(attested {expected_catalog_path!r}, "
            f"found {config.get('model_catalog_json')!r})"
        )
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
    catalog_error = _managed_codex_catalog_error(
        managed_codex_catalog,
        attestation=attestation,
    )
    if catalog_error is not None:
        errors.append(catalog_error)
    return errors


def _write_managed_codex_route(
    session_dir: Path,
    *,
    attestation: ManagedJoinAttestation,
    route: ManagedCodexRoute,
    catalog: bytes,
) -> None:
    """Validate the synchronized config and write the attested route projection."""
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
    catalog_path = session_dir / _MANAGED_CATALOG_FILENAME
    config["model_catalog_json"] = str(catalog_path.resolve())
    atomic_write(catalog_path, catalog)
    atomic_write(config_path, _codex_cfg._serialize_toml(config))
    sync_managed_codex_hooks_to_config(config_path, route=route)


def project_managed_route(
    backend: CodexBackend,
    session_dir: Path,
    *,
    adaptation_context: SemanticAdaptationContext,
    route: ManagedCodexRoute,
) -> None:
    """Project one attested route after source-config synchronization."""
    del backend
    attestation = adaptation_context.managed_join_attestation
    if attestation is None:
        raise ValueError("managed Codex route requires a managed-join attestation")
    if not attestation.admits_backend("codex"):
        raise ValueError("managed Codex route requires a direct-mode Codex attestation")
    catalog = adaptation_context.managed_codex_catalog
    if catalog is None:
        raise ValueError("managed Codex route requires its attested catalog snapshot")
    if hashlib.sha256(catalog).hexdigest() != attestation.codex_catalog_digest:
        raise ValueError("managed Codex catalog differs from the attested projection")
    catalog_error = _managed_codex_catalog_error(catalog, attestation=attestation)
    if catalog_error is not None:
        raise ValueError(catalog_error)
    _write_managed_codex_route(
        session_dir,
        attestation=attestation,
        route=route,
        catalog=catalog,
    )
    errors = _managed_codex_config_errors(
        session_dir,
        attestation=attestation,
        route=route,
        managed_codex_catalog=catalog,
    )
    if errors:
        raise ValueError("; ".join(errors))
