"""Native validation of finalized Codex interactive launches."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import (
    CODEX_HOME_ENV_VAR,
    PROVIDER_PROFILE_ENV_VAR,
    SESSION_ADD_DIR_SUBDIR,
    CmdOrigin,
    CmdSpec,
    InteractiveInvocationValidation,
    SkillDiscoveryRouteDef,
    ValidatedAddDir,
)
from autoskillit.execution.backends._codex_cmd_builders import CodexFlags
from autoskillit.execution.backends._codex_config import _format_toml_value
from autoskillit.execution.backends._codex_discovery import (
    CODEX_DISCOVERY_ATTESTATION_TIMEOUT_SECONDS,
    CODEX_MANAGED_HOME_ROUTE,
    CODEX_PROJECTED_HOME_ROUTE,
    CODEX_SKILL_DISCOVERY_CONTRACT,
    probe_codex_version,
)
from autoskillit.execution.backends._codex_discovery_attestation import attest
from autoskillit.execution.backends._codex_probes import (
    _validate_inert_rollout_paths,
    _validate_mcp_probe,
)

_CODEX_SQLITE_HOME_ENV_VAR = "CODEX_SQLITE_HOME"


def _interactive_probe_prefix(origin: CmdOrigin) -> tuple[str, ...]:
    command: list[str] = [origin.binary]
    for flag, value in origin.kv_flags:
        if flag == CodexFlags.PROFILE:
            command.extend((flag, value))
    for flag, value in origin.variadic_pairs:
        if flag == CodexFlags.CONFIG_OVERRIDE:
            command.extend((flag, value))
    return tuple(command)


def _run_interactive_native_probes(
    spec: CmdSpec,
    *,
    origin: CmdOrigin,
    generated_home: Path,
    catalog_dir: Path,
    managed_catalog: ValidatedAddDir,
    expected_discovery_root: Path,
    managed_root_scope: Path,
    route: SkillDiscoveryRouteDef,
    config_bytes: bytes,
    before_fingerprint: tuple[tuple[str, str, int, int], ...],
) -> InteractiveInvocationValidation:
    probe_command = (*_interactive_probe_prefix(origin), "mcp", "list", CodexFlags.JSON)
    errors = _validate_mcp_probe(
        probe_command,
        env=spec.env,
        cwd=spec.cwd,
        config_bytes=config_bytes,
    )
    after_errors, after_fingerprint = _validate_inert_rollout_paths(generated_home)
    errors.extend(after_errors)
    if not after_errors and after_fingerprint != before_fingerprint:
        errors.append("Codex MCP validation mutated the inert rollout path topology")
    if errors:
        return InteractiveInvocationValidation(errors=tuple(errors))

    raw_version, _, version_errors = probe_codex_version(
        executable=origin.binary,
        env=spec.env,
        cwd=spec.cwd,
        timeout_seconds=CODEX_DISCOVERY_ATTESTATION_TIMEOUT_SECONDS,
    )
    if version_errors:
        return InteractiveInvocationValidation(errors=tuple(version_errors))

    discovery_validation = attest(
        probe_command=(
            *_interactive_probe_prefix(origin),
            *CODEX_SKILL_DISCOVERY_CONTRACT.prompt_probe,
        ),
        env=spec.env,
        cwd=spec.cwd,
        catalog_dir=catalog_dir,
        expected_discovery_root=expected_discovery_root,
        expected_entries=managed_catalog.skill_entries,
        managed_root_scope=managed_root_scope,
        route=route,
        version=raw_version,
        timeout_seconds=CODEX_DISCOVERY_ATTESTATION_TIMEOUT_SECONDS,
    )
    final_errors, final_fingerprint = _validate_inert_rollout_paths(generated_home)
    errors = list(discovery_validation.errors)
    errors.extend(final_errors)
    if not final_errors and final_fingerprint != before_fingerprint:
        errors.append("Codex skill discovery mutated the inert rollout path topology")
    if errors:
        return InteractiveInvocationValidation(errors=tuple(errors))
    return discovery_validation


def _validated_interactive_origin(spec: CmdSpec) -> tuple[CmdOrigin | None, list[str]]:
    origin = spec.origin
    if origin is None:
        return None, ["Codex interactive validation requires unambiguous CmdOrigin metadata"]
    reconstructed: list[str] = [origin.binary, *origin.mode_flags]
    for flag, value in origin.kv_flags:
        reconstructed.extend((flag, value))
    reconstructed.extend(value for _role, value in origin.positional)
    for flag, value in origin.variadic_pairs:
        reconstructed.extend((flag, value))
    if tuple(reconstructed) != spec.cmd:
        return None, ["Codex interactive CmdOrigin does not describe the finalized command"]
    if not spec.cwd or not Path(spec.cwd).is_absolute():
        return None, ["Codex interactive validation requires an absolute finalized cwd"]
    return origin, []


def _validate_projected_interactive_invocation(
    spec: CmdSpec,
    origin: CmdOrigin,
    route: SkillDiscoveryRouteDef,
) -> InteractiveInvocationValidation:
    home_value = spec.env.get(CODEX_HOME_ENV_VAR)
    if not home_value:
        return InteractiveInvocationValidation(
            errors=("Codex projected interactive validation requires CODEX_HOME",)
        )
    if spec.env.get(_CODEX_SQLITE_HOME_ENV_VAR):
        return InteractiveInvocationValidation(
            errors=("Codex projected interactive environment must not contain CODEX_SQLITE_HOME",)
        )
    projected_home = Path(home_value)
    if not projected_home.is_absolute():
        return InteractiveInvocationValidation(
            errors=("Codex projected interactive CODEX_HOME must be absolute",)
        )
    try:
        canonical_home = projected_home.resolve(strict=True)
    except OSError as exc:
        return InteractiveInvocationValidation(
            errors=(f"Codex projected interactive CODEX_HOME is unreadable: {exc}",)
        )
    if (
        projected_home != canonical_home
        or projected_home.is_symlink()
        or not projected_home.is_dir()
    ):
        return InteractiveInvocationValidation(
            errors=("Codex projected interactive CODEX_HOME must be a canonical real directory",)
        )

    catalog_dir = route.catalog_dir(projected_home)
    expected_discovery_root = route.discovery_root(projected_home)
    if expected_discovery_root is None:
        return InteractiveInvocationValidation(
            errors=("Codex projected interactive discovery route has no loader entry point",)
        )
    raw_version, _, version_errors = probe_codex_version(
        executable=origin.binary,
        env=spec.env,
        cwd=spec.cwd,
        timeout_seconds=CODEX_DISCOVERY_ATTESTATION_TIMEOUT_SECONDS,
    )
    if version_errors:
        return InteractiveInvocationValidation(errors=tuple(version_errors))
    return attest(
        probe_command=(
            *_interactive_probe_prefix(origin),
            *CODEX_SKILL_DISCOVERY_CONTRACT.prompt_probe,
        ),
        env=spec.env,
        cwd=spec.cwd,
        catalog_dir=catalog_dir,
        expected_discovery_root=expected_discovery_root,
        expected_entries=spec.projected_skill_entries,
        route=route,
        version=raw_version,
        managed_root_scope=projected_home.parent,
        timeout_seconds=CODEX_DISCOVERY_ATTESTATION_TIMEOUT_SECONDS,
    )


def _managed_home(spec: CmdSpec) -> tuple[Path | None, str | None]:
    home_value = spec.env.get(CODEX_HOME_ENV_VAR)
    if not home_value or home_value != spec.env.get(_CODEX_SQLITE_HOME_ENV_VAR):
        return (
            None,
            "Codex interactive reserved home and SQLite environment must name "
            "the same generated home",
        )
    generated_home = Path(home_value)
    if not generated_home.is_absolute():
        return None, "Codex interactive generated home must be absolute"
    generated_home = generated_home.resolve(strict=False)
    if str(generated_home) != home_value:
        return None, "Codex interactive generated home environment is not canonical"
    return generated_home, None


def _managed_catalog_error(
    managed_catalog: ValidatedAddDir,
    generated_home: Path,
    route: SkillDiscoveryRouteDef,
) -> tuple[Path | None, Path | None, str | None]:
    expected_add_dir = generated_home / SESSION_ADD_DIR_SUBDIR
    error = next(
        (
            message
            for invalid, message in (
                (
                    managed_catalog.session_home != str(generated_home),
                    "Codex interactive managed skill catalog is bound to another home",
                ),
                (
                    Path(managed_catalog.path) != expected_add_dir,
                    "Codex interactive managed skill catalog is bound to another add-dir",
                ),
                (
                    not managed_catalog.skill_entries,
                    "Codex interactive managed skill catalog has no frozen entries",
                ),
            )
            if invalid
        ),
        None,
    )
    expected_discovery_root = route.discovery_root(generated_home)
    if expected_discovery_root is None:
        return None, None, "Codex managed discovery route has no loader entry point"
    return route.catalog_dir(generated_home), expected_discovery_root, error


def _managed_command_error(spec: CmdSpec, origin: CmdOrigin, generated_home: Path) -> str | None:
    sqlite_override = f"sqlite_home={_format_toml_value(str(generated_home))}"
    config_overrides = [
        value for flag, value in origin.variadic_pairs if flag == CodexFlags.CONFIG_OVERRIDE
    ]
    profiles = [value for flag, value in origin.kv_flags if flag == CodexFlags.PROFILE]
    selected_profile = spec.env.get(PROVIDER_PROFILE_ENV_VAR)
    return next(
        (
            message
            for invalid, message in (
                (
                    not config_overrides or config_overrides[-1] != sqlite_override,
                    "Codex interactive command is missing the highest-precedence "
                    "generated-home sqlite_home override",
                ),
                (len(profiles) > 1, "Codex interactive command has an ambiguous selected profile"),
                (
                    profiles != ([selected_profile] if selected_profile else []),
                    "Codex interactive profile metadata does not match the child environment",
                ),
            )
            if invalid
        ),
        None,
    )


def _validate_managed_interactive_invocation(
    spec: CmdSpec,
    origin: CmdOrigin,
    route: SkillDiscoveryRouteDef,
    managed_catalog: ValidatedAddDir,
) -> InteractiveInvocationValidation:
    generated_home, home_error = _managed_home(spec)
    if home_error is not None:
        return InteractiveInvocationValidation(errors=(home_error,))
    if generated_home is None:
        raise RuntimeError(
            "Codex interactive validation invariant violated: "
            "_managed_home returned no error and no generated home"
        )
    catalog_dir, expected_discovery_root, catalog_error = _managed_catalog_error(
        managed_catalog,
        generated_home,
        route,
    )
    if catalog_error is not None:
        return InteractiveInvocationValidation(errors=(catalog_error,))
    if catalog_dir is None or expected_discovery_root is None:
        raise RuntimeError(
            "Codex interactive validation invariant violated: "
            "_managed_catalog_error returned no error and missing catalog metadata"
        )
    command_error = _managed_command_error(spec, origin, generated_home)
    if command_error is not None:
        return InteractiveInvocationValidation(errors=(command_error,))
    try:
        config_bytes = (generated_home / "config.toml").read_bytes()
    except OSError as exc:
        return InteractiveInvocationValidation(
            errors=(
                f"Failed to read finalized generated Codex config: {type(exc).__name__}: {exc}",
            )
        )
    layout_errors, before_fingerprint = _validate_inert_rollout_paths(generated_home)
    if layout_errors:
        return InteractiveInvocationValidation(errors=tuple(layout_errors))
    return _run_interactive_native_probes(
        spec,
        origin=origin,
        generated_home=generated_home,
        catalog_dir=catalog_dir,
        managed_catalog=managed_catalog,
        expected_discovery_root=expected_discovery_root,
        managed_root_scope=generated_home.parent,
        route=route,
        config_bytes=config_bytes,
        before_fingerprint=before_fingerprint,
    )


def _validate_interactive_discovery_route(
    spec: CmdSpec,
    origin: CmdOrigin,
    route: SkillDiscoveryRouteDef,
    managed_catalog: ValidatedAddDir | None,
) -> InteractiveInvocationValidation:
    if route is CODEX_PROJECTED_HOME_ROUTE:
        if managed_catalog is not None:
            return InteractiveInvocationValidation(
                errors=("Codex projected discovery route cannot use a managed catalog",)
            )
        if not spec.projected_skill_entries:
            return InteractiveInvocationValidation(
                errors=("Codex projected discovery route requires projected catalog evidence",)
            )
        return _validate_projected_interactive_invocation(spec, origin, route)
    if route is not CODEX_MANAGED_HOME_ROUTE:
        return InteractiveInvocationValidation(
            errors=(f"unsupported Codex interactive discovery route: {route.name}",)
        )
    if managed_catalog is None:
        return InteractiveInvocationValidation(
            errors=("Codex managed discovery route requires managed catalog evidence",)
        )
    if spec.projected_skill_entries:
        return InteractiveInvocationValidation(
            errors=("Codex managed discovery route cannot use projected catalog evidence",)
        )
    return _validate_managed_interactive_invocation(spec, origin, route, managed_catalog)


def validate_codex_interactive_invocation(spec: CmdSpec) -> InteractiveInvocationValidation:
    origin, origin_errors = _validated_interactive_origin(spec)
    if origin_errors:
        return InteractiveInvocationValidation(errors=tuple(origin_errors))
    if origin is None:
        raise RuntimeError(
            "Codex interactive validation invariant violated: "
            "_validated_interactive_origin returned no errors and no origin"
        )
    route = spec.skill_discovery_route
    if route is None:
        return InteractiveInvocationValidation(
            errors=("Codex interactive validation requires a declared skill discovery route",)
        )
    managed_catalog = spec.managed_skill_catalog
    if managed_catalog is not None and spec.projected_skill_entries:
        return InteractiveInvocationValidation(
            errors=("Codex interactive validation received mixed managed and projected catalogs",)
        )
    return _validate_interactive_discovery_route(
        spec,
        origin,
        route,
        managed_catalog,
    )
