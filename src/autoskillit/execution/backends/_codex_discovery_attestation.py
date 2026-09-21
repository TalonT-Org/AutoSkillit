"""Attestation interval checks for Codex's model-visible skill catalog."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from autoskillit.core import InteractiveInvocationValidation, SkillDiscoveryRouteDef
from autoskillit.execution.backends import _codex_discovery as discovery

AliasState = tuple[int, int, int, int, int, int, str, Path]
FileFingerprint = tuple[tuple[str, int, int, int, int, str], ...]


def _initial_evidence(
    *,
    catalog_dir: Path,
    expected_discovery_root: Path,
    expected_entries: Sequence[tuple[str, str]],
    route: SkillDiscoveryRouteDef,
) -> tuple[dict[str, Path], Path, AliasState | None, FileFingerprint]:
    expected_paths = discovery._validated_expected_paths(catalog_dir, expected_entries)
    canonical_catalog = catalog_dir.resolve(strict=True)
    alias_state = (
        discovery._capture_managed_alias_state(
            expected_discovery_root,
            catalog_dir=canonical_catalog,
            route=route,
        )
        if route is discovery.CODEX_MANAGED_HOME_ROUTE
        else None
    )
    return (
        expected_paths,
        canonical_catalog,
        alias_state,
        discovery._fingerprint_managed_files(expected_paths),
    )


def _prompt_input_errors(
    *,
    probe_command: tuple[str, ...],
    env: Mapping[str, str],
    cwd: str,
    timeout_seconds: float,
    expected_paths: Mapping[str, Path],
    expected_discovery_root: Path,
    catalog_dir: Path,
    route: SkillDiscoveryRouteDef,
    managed_root_scope: Path | None,
    context: str,
) -> list[str]:
    result = discovery._run_bounded_codex_probe(
        probe_command,
        env=env,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        stream_limit_bytes=discovery._CODEX_DISCOVERY_STREAM_LIMIT,
    )
    if result.failure is not None:
        return [
            "Codex skill discovery probe "
            f"{result.failure}; {discovery._probe_diagnostic(result)}; {context}"
        ]
    if result.returncode != 0:
        return [
            f"Codex skill discovery probe exited with status {result.returncode}; "
            f"{discovery._probe_diagnostic(result)}; {context}"
        ]
    try:
        discovered = discovery.parse_skills_instructions(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return [f"Codex skill discovery parse failed: {exc}; {context}"]
    return discovery._catalog_discovery_errors(
        discovered,
        expected_paths,
        expected_discovery_root=expected_discovery_root,
        catalog_dir=catalog_dir,
        route=route,
        context=context,
        managed_root_scope=managed_root_scope,
    )


def _revalidation_errors(
    *,
    expected_paths: Mapping[str, Path],
    expected_discovery_root: Path,
    catalog_dir: Path,
    route: SkillDiscoveryRouteDef,
    alias_state: AliasState | None,
    before_fingerprint: FileFingerprint,
    context: str,
) -> list[str]:
    errors: list[str] = []
    try:
        if (
            alias_state is not None
            and discovery._capture_managed_alias_state(
                expected_discovery_root,
                catalog_dir=catalog_dir,
                route=route,
            )
            != alias_state
        ):
            errors.append(f"Codex skill discovery mutated the managed alias; {context}")
        after_fingerprint = discovery._fingerprint_managed_files(expected_paths)
    except (OSError, RuntimeError) as exc:
        return [
            "Codex skill discovery could not revalidate the managed catalog: "
            f"{type(exc).__name__}: {exc}; {context}"
        ]
    except ValueError as exc:
        return [f"Codex skill discovery mutated the managed catalog: {exc}; {context}"]
    if after_fingerprint != before_fingerprint:
        errors.append(f"Codex skill discovery mutated the managed catalog; {context}")
    return errors


def _successful_attestation(
    *,
    alias_state: AliasState | None,
    catalog_dir: Path,
    expected_discovery_root: Path,
    expected_entries: Sequence[tuple[str, str]],
    expected_paths: Mapping[str, Path],
    route: SkillDiscoveryRouteDef,
    before_fingerprint: FileFingerprint,
    context: str,
) -> InteractiveInvocationValidation:
    if alias_state is None:
        return InteractiveInvocationValidation(errors=())

    def pre_spawn_check() -> None:
        try:
            revalidated_paths = discovery._validated_expected_paths(catalog_dir, expected_entries)
            if revalidated_paths != expected_paths:
                raise ValueError("managed expected paths changed")
            if (
                discovery._capture_managed_alias_state(
                    expected_discovery_root,
                    catalog_dir=catalog_dir,
                    route=route,
                )
                != alias_state
            ):
                raise ValueError("managed alias identity changed")
            if discovery._fingerprint_managed_files(revalidated_paths) != before_fingerprint:
                raise ValueError("managed catalog files changed")
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(
                f"Codex skill discovery final integrity check failed: {exc}; {context}"
            ) from exc

    return InteractiveInvocationValidation(errors=(), pre_spawn_check=pre_spawn_check)


def attest(
    *,
    probe_command: tuple[str, ...],
    env: Mapping[str, str],
    cwd: str,
    catalog_dir: Path,
    expected_discovery_root: Path,
    expected_entries: Sequence[tuple[str, str]],
    route: SkillDiscoveryRouteDef,
    version: str,
    managed_root_scope: Path | None,
    timeout_seconds: float,
) -> InteractiveInvocationValidation:
    executable = probe_command[0] if probe_command else "<missing>"
    context = discovery._contract_context(
        executable=executable,
        catalog_dir=catalog_dir,
        version=version,
        timeout_seconds=timeout_seconds,
        route=route,
    )
    if not expected_discovery_root.is_absolute():
        return InteractiveInvocationValidation(
            errors=(f"Codex skill discovery expected root must be absolute; {context}",)
        )
    try:
        expected_paths, canonical_catalog, alias_state, before_fingerprint = _initial_evidence(
            catalog_dir=catalog_dir,
            expected_discovery_root=expected_discovery_root,
            expected_entries=expected_entries,
            route=route,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return InteractiveInvocationValidation(
            errors=(f"Codex skill discovery catalog validation failed: {exc}; {context}",)
        )
    errors = _prompt_input_errors(
        probe_command=probe_command,
        env=env,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        expected_paths=expected_paths,
        expected_discovery_root=expected_discovery_root,
        catalog_dir=canonical_catalog,
        route=route,
        managed_root_scope=managed_root_scope,
        context=context,
    )
    errors.extend(
        _revalidation_errors(
            expected_paths=expected_paths,
            expected_discovery_root=expected_discovery_root,
            catalog_dir=canonical_catalog,
            route=route,
            alias_state=alias_state,
            before_fingerprint=before_fingerprint,
            context=context,
        )
    )
    if errors:
        return InteractiveInvocationValidation(errors=tuple(errors))
    return _successful_attestation(
        alias_state=alias_state,
        catalog_dir=catalog_dir,
        expected_discovery_root=expected_discovery_root,
        expected_entries=expected_entries,
        expected_paths=expected_paths,
        route=route,
        before_fingerprint=before_fingerprint,
        context=context,
    )
