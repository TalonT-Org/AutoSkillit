"""Pinned parsing and attestation for Codex's model-visible skill catalog."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import regex as re

from autoskillit.core import (
    PluginLaunchBinding,
    PluginLoadMode,
    SkillDiscoveryMechanism,
    SkillDiscoveryRouteDef,
    UpstreamSupportStatus,
    normalize_codex_cli_version,
    validate_managed_skill_entries,
)
from autoskillit.execution.backends._codex_probes import (
    _probe_diagnostic,
    _run_bounded_codex_probe,
)

CODEX_CLI_MIN_VERSION = "0.156.1"


@dataclass(frozen=True, slots=True)
class CodexSkillDiscoveryContractDef:
    """Pinned upstream discovery behavior consumed by managed Codex launches."""

    prompt_probe: tuple[str, ...] = ("debug", "prompt-input")
    upstream_revision: str = "646f7c0a91b8e327d263335da68ae8ef212895ce"
    verified_binary: str = "codex-cli 0.153.4"
    # First release carrying `skills/extraRoots/set`; product support starts
    # at CODEX_CLI_MIN_VERSION.
    extra_roots_min_version: str = "0.136.0"


# The inner catalog remains upstream-rendered text. Its grammar was verified at
# codex-rs/core-skills/src/render.rs:62-85 (rust-v0.130.0) and at
# codex-rs/ext/skills/src/render.rs in the pinned revision above.
CODEX_SKILL_DISCOVERY_CONTRACT = CodexSkillDiscoveryContractDef()
_UPSTREAM = CODEX_SKILL_DISCOVERY_CONTRACT.upstream_revision
CODEX_MANAGED_HOME_ROUTE = SkillDiscoveryRouteDef(
    name="codex_managed_home_skills_alias",
    mechanism=SkillDiscoveryMechanism.CODEX_HOME_SKILLS,
    upstream_status=UpstreamSupportStatus.DEPRECATED,
    tracking_issue=4717,
    catalog_relpath="add-dir/skills",
    discovery_root_relpath="skills",
    upstream_citation=f"codex-rs/ext/skills/src/host_roots.rs:95-100@{_UPSTREAM}",
)
CODEX_PROJECTED_HOME_ROUTE = SkillDiscoveryRouteDef(
    name="codex_projected_home_skills",
    mechanism=SkillDiscoveryMechanism.CODEX_HOME_SKILLS,
    upstream_status=UpstreamSupportStatus.DEPRECATED,
    tracking_issue=4717,
    catalog_relpath="skills",
    discovery_root_relpath="skills",
    upstream_citation=f"codex-rs/ext/skills/src/host_roots.rs:95-100@{_UPSTREAM}",
)
CODEX_APP_SERVER_ROUTE = SkillDiscoveryRouteDef(
    name="codex_app_server_extra_roots",
    mechanism=SkillDiscoveryMechanism.APP_SERVER_EXTRA_ROOTS,
    upstream_status=UpstreamSupportStatus.SUPPORTED,
    tracking_issue=None,
    catalog_relpath="add-dir/skills",
    discovery_root_relpath=None,
    upstream_citation=(
        f"codex-rs/app-server/src/request_processors/catalog_processor.rs:556-561@{_UPSTREAM}"
    ),
)
CODEX_DISCOVERY_ATTESTATION_TIMEOUT_SECONDS = 30.0
_CODEX_DISCOVERY_STREAM_LIMIT = 1024 * 1024


def select_interactive_discovery_route(
    *,
    generated_home: Path | None,
    plugin_binding: PluginLaunchBinding | None,
) -> SkillDiscoveryRouteDef | None:
    if generated_home is not None:
        return CODEX_MANAGED_HOME_ROUTE
    if plugin_binding is not None and plugin_binding.load_mode is PluginLoadMode.PROJECTED_HOME:
        return CODEX_PROJECTED_HOME_ROUTE
    return None


@dataclass(frozen=True, slots=True)
class DiscoveredSkills:
    names: frozenset[str]
    roots: tuple[Path, ...]
    root_tokens: tuple[str, ...]
    paths: Mapping[str, Path]


_ROOT_RE = re.compile(r"^- `(?P<alias>r[0-9]+)` = `(?P<path>[^`]+)`$")
_SKILL_RE = re.compile(r"^- (?P<name>[^:\s][^:]*):.*?\(file:\s*`?(?P<path>[^)`]+)`?\)\s*$")
_SKILL_START_RE = re.compile(r"^- (?P<name>[^:\s][^:]*):")
_PATH_ONLY_RE = re.compile(r"^\s*\(file:\s*`?(?P<path>[^)`]+)`?\)\s*$")


def _skills_instruction_text(document: Any) -> str:
    if not isinstance(document, list):
        raise ValueError("Codex prompt-input JSON must be a response-item list")
    candidates: list[str] = []
    for item in document:
        if not isinstance(item, dict):
            raise ValueError("Codex prompt-input JSON contains a malformed response item")
        content = item.get("content")
        if not isinstance(content, list):
            raise ValueError("Codex prompt-input response item has malformed content")
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("Codex prompt-input content contains a malformed item")
            text = part.get("text")
            if part.get("type") == "input_text" and isinstance(text, str):
                if item.get("role") == "developer" and text.startswith("<skills_instructions>"):
                    candidates.append(text)
    if len(candidates) != 1:
        raise ValueError("Codex prompt-input must contain exactly one <skills_instructions> block")
    return candidates[0]


def _expand_skill_path(token: str, aliases: Mapping[str, Path]) -> Path:
    normalized = token.strip()
    alias, separator, relative = normalized.partition("/")
    if separator and re.fullmatch(r"r[0-9]+", alias):
        try:
            root = aliases[alias]
        except KeyError as exc:
            raise ValueError(f"unknown skill-root alias: {alias}") from exc
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"skill path escapes skill root: {normalized!r}")
        path = root / relative_path
    else:
        path = Path(normalized)
    if not path.is_absolute():
        raise ValueError(f"skill path is not absolute: {normalized!r}")
    return path


def _parse_skill_roots(
    lines: Sequence[str],
) -> tuple[tuple[Path, ...], tuple[str, ...], dict[str, Path]]:
    aliases: dict[str, Path] = {}
    roots: list[Path] = []
    root_tokens: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        match = _ROOT_RE.fullmatch(line)
        if match is None:
            raise ValueError(f"malformed Skill roots entry: {line!r}")
        alias = match.group("alias")
        if alias in aliases:
            raise ValueError(f"duplicate skill-root alias: {alias}")
        root_token = match.group("path")
        root = Path(root_token)
        if not root.is_absolute():
            raise ValueError(f"skill root is not absolute: {root}")
        aliases[alias] = root
        roots.append(root)
        root_tokens.append(root_token)
    return tuple(roots), tuple(root_tokens), aliases


def _new_skill_name(raw_name: str, paths: Mapping[str, Path]) -> str:
    name = raw_name.strip()
    if name in paths:
        raise ValueError(f"duplicate skill name: {name}")
    return name


def _parse_available_skills(
    lines: Sequence[str],
    aliases: Mapping[str, Path],
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    pending_name: str | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        match = _SKILL_RE.fullmatch(line)
        if match is not None:
            if pending_name is not None:
                raise ValueError(f"skill entry {pending_name!r} has no path")
            name = _new_skill_name(match.group("name"), paths)
            paths[name] = _expand_skill_path(match.group("path"), aliases)
            continue
        start_match = _SKILL_START_RE.match(line)
        if start_match is not None:
            if pending_name is not None:
                raise ValueError(f"skill entry {pending_name!r} has no path")
            pending_name = _new_skill_name(start_match.group("name"), paths)
            continue
        path_match = _PATH_ONLY_RE.fullmatch(line)
        if path_match is not None and pending_name is not None:
            paths[pending_name] = _expand_skill_path(path_match.group("path"), aliases)
            pending_name = None
            continue
        raise ValueError(f"malformed Available skills entry: {line!r}")
    if pending_name is not None:
        raise ValueError(f"skill entry {pending_name!r} has no path")
    return paths


def parse_skills_instructions(prompt_input_json: str) -> DiscoveredSkills:
    """Parse the consumed Codex skills grammar from a prompt-input JSON envelope."""
    try:
        document = json.loads(prompt_input_json)
    except json.JSONDecodeError as exc:
        raise ValueError("Codex prompt-input contains malformed JSON") from exc
    text = _skills_instruction_text(document)
    lines = text.splitlines()
    if not lines or lines[0].strip() != "<skills_instructions>":
        raise ValueError("malformed <skills_instructions> block opening")
    if not lines[-1].strip() == "</skills_instructions>":
        raise ValueError("malformed <skills_instructions> block closing")
    try:
        roots_index = next(
            index for index, line in enumerate(lines) if line.strip() == "### Skill roots"
        )
        skills_index = next(
            index for index, line in enumerate(lines) if line.strip() == "### Available skills"
        )
    except StopIteration as exc:
        raise ValueError(
            "<skills_instructions> block is missing Skill roots or Available skills heading"
        ) from exc
    if roots_index >= skills_index:
        raise ValueError("<skills_instructions> headings are out of order")
    if any("(file:" in line for line in lines[: skills_index + 1]):
        raise ValueError("skill path line appears outside Available skills")

    roots, root_tokens, aliases = _parse_skill_roots(lines[roots_index + 1 : skills_index])
    paths = _parse_available_skills(lines[skills_index + 1 : -1], aliases)
    return DiscoveredSkills(
        names=frozenset(paths),
        roots=roots,
        root_tokens=root_tokens,
        paths=MappingProxyType(paths),
    )


def _contract_context(
    *,
    executable: str,
    catalog_dir: Path | str,
    version: str,
    timeout_seconds: float,
    route: SkillDiscoveryRouteDef | None = None,
) -> str:
    contract = CODEX_SKILL_DISCOVERY_CONTRACT
    source = route.upstream_citation if route is not None else "unbound"
    return (
        f"contract_revision={contract.upstream_revision} "
        f"contract_source={source} "
        f"catalog={catalog_dir} executable={executable} version={version} "
        f"timeout_seconds={timeout_seconds}"
    )


def _validated_expected_paths(
    catalog_dir: Path,
    expected_entries: Sequence[tuple[str, str]],
) -> dict[str, Path]:
    if not catalog_dir.is_absolute():
        raise ValueError("managed catalog path must be absolute")
    canonical_catalog = catalog_dir.resolve(strict=True)
    if catalog_dir != canonical_catalog or catalog_dir.is_symlink() or not catalog_dir.is_dir():
        raise ValueError("managed catalog must be a canonical real directory")
    relative_paths = validate_managed_skill_entries(expected_entries)
    return {
        name: canonical_catalog / relative_path for name, relative_path in relative_paths.items()
    }


def _fingerprint_managed_files(
    expected_paths: Mapping[str, Path],
) -> tuple[tuple[str, int, int, int, int, str], ...]:
    fingerprint: list[tuple[str, int, int, int, int, str]] = []
    for name, path in expected_paths.items():
        before = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(before.st_mode):
            raise ValueError(f"managed skill file must be a regular file: {path}")
        content = path.read_bytes()
        after = path.lstat()
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_after != identity_before or not stat.S_ISREG(after.st_mode):
            raise ValueError(f"managed skill file changed while fingerprinting: {path}")
        fingerprint.append((*((name,) + identity_after), hashlib.sha256(content).hexdigest()))
    return tuple(fingerprint)


def _capture_managed_alias_state(
    alias_root: Path,
    *,
    catalog_dir: Path,
    route: SkillDiscoveryRouteDef,
) -> tuple[int, int, int, int, int, int, str, Path]:
    before = alias_root.lstat()
    if not stat.S_ISLNK(before.st_mode):
        raise ValueError(f"managed discovery alias must be a symlink: {alias_root}")
    token = os.readlink(alias_root)
    if token != route.alias_target:
        raise ValueError(f"managed discovery root has an unexpected alias target: {alias_root}")
    resolved_target = alias_root.resolve(strict=True)
    after = alias_root.lstat()
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity:
        raise ValueError(f"managed discovery root changed while validating: {alias_root}")
    if resolved_target != catalog_dir:
        raise ValueError(f"managed discovery root does not resolve to catalog: {alias_root}")
    return (*after_identity, token, resolved_target)


def _validate_expected_discovery_root(
    discovered: DiscoveredSkills,
    *,
    expected_discovery_root: Path,
    catalog_dir: Path,
    route: SkillDiscoveryRouteDef,
    context: str,
    managed_root_scope: Path | None,
) -> list[str]:
    if route is CODEX_MANAGED_HOME_ROUTE:
        return _managed_root_policy_errors(
            discovered,
            expected_discovery_root=expected_discovery_root,
            catalog_dir=catalog_dir,
            context=context,
            managed_root_scope=managed_root_scope,
        )
    return _direct_root_errors(
        discovered,
        expected_discovery_root=expected_discovery_root,
        catalog_dir=catalog_dir,
        context=context,
        managed_root_scope=managed_root_scope,
    )


def _managed_root_policy_errors(
    discovered: DiscoveredSkills,
    *,
    expected_discovery_root: Path,
    catalog_dir: Path,
    context: str,
    managed_root_scope: Path | None,
) -> list[str]:
    roots = [str(root) for root in discovered.roots]
    primary_tokens = {str(expected_discovery_root), str(catalog_dir)}
    primary_indexes = [
        index for index, token in enumerate(discovered.root_tokens) if token in primary_tokens
    ]
    if len(primary_indexes) != 1:
        return [
            "Codex skill discovery root-policy requires exactly one exact managed "
            f"primary root; roots={list(discovered.root_tokens)}; {context}"
        ]
    try:
        primary_target = discovered.roots[primary_indexes[0]].resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return [f"Codex skill discovery managed primary root is unreadable: {exc}; {context}"]
    if primary_target != catalog_dir:
        return [
            "Codex skill discovery managed primary root does not resolve to the "
            f"catalog; roots={roots}; {context}"
        ]
    scope = managed_root_scope.resolve(strict=False) if managed_root_scope else None
    allowed_system = str(catalog_dir / ".system")
    for index, (token, root) in enumerate(zip(discovered.root_tokens, discovered.roots)):
        if index == primary_indexes[0] or token == allowed_system:
            continue
        try:
            resolved = root.resolve(strict=False)
        except RuntimeError as exc:
            return [
                f"Codex skill discovery root-policy could not resolve {root}: {exc}; {context}"
            ]
        in_managed_scope = (
            (scope is not None and (resolved == scope or scope in resolved.parents))
            or resolved == catalog_dir
            or catalog_dir in resolved.parents
        )
        if in_managed_scope:
            return [
                "Codex skill discovery root-policy rejects additional managed root "
                f"{token!r}; roots={list(discovered.root_tokens)}; {context}"
            ]
    return []


def _direct_root_errors(
    discovered: DiscoveredSkills,
    *,
    expected_discovery_root: Path,
    catalog_dir: Path,
    context: str,
    managed_root_scope: Path | None,
) -> list[str]:
    roots = [str(root) for root in discovered.roots]
    root_matches = [root for root in discovered.roots if root == expected_discovery_root]
    if not root_matches:
        return [
            "Codex skill discovery roots do not contain expected discovery root "
            f"{expected_discovery_root}; roots={roots}; {context}"
        ]
    if len(root_matches) > 1:
        return [
            "Codex skill discovery roots contain duplicate expected discovery root "
            f"{expected_discovery_root}; roots={roots}; {context}"
        ]
    try:
        root_target = root_matches[0].resolve(strict=True)
    except OSError as exc:
        return [f"Codex skill discovery expected root is unreadable: {exc}; {context}"]
    if root_target != catalog_dir:
        return [
            "Codex skill discovery expected root does not resolve to the "
            f"catalog; roots={roots}; {context}"
        ]
    if managed_root_scope is not None:
        scope = managed_root_scope.resolve(strict=False)
        for root in discovered.roots:
            if root == expected_discovery_root:
                continue
            resolved = root.resolve(strict=False)
            if resolved != scope and scope not in resolved.parents:
                continue
            if resolved == catalog_dir or catalog_dir in resolved.parents:
                continue
            return [
                "Codex skill discovery exposes a foreign managed root "
                f"{root}; roots={roots}; {context}"
            ]
    return []


def _catalog_discovery_errors(
    discovered: DiscoveredSkills,
    expected_paths: Mapping[str, Path],
    *,
    expected_discovery_root: Path,
    catalog_dir: Path,
    route: SkillDiscoveryRouteDef,
    managed_root_scope: Path | None,
    context: str,
) -> list[str]:
    missing = sorted(set(expected_paths) - discovered.names)
    misplaced: list[str] = []
    for name, expected_path in expected_paths.items():
        actual_path = discovered.paths.get(name)
        if actual_path is None:
            continue
        try:
            actual_canonical = actual_path.resolve(strict=True)
        except OSError as exc:
            misplaced.append(f"{name}={actual_path} (unreadable: {type(exc).__name__}: {exc})")
            continue
        if actual_canonical != expected_path:
            misplaced.append(f"{name}={actual_path}")

    roots = [str(root) for root in discovered.roots]
    errors: list[str] = []
    if missing:
        errors.append(
            f"Codex skill discovery is missing expected names {missing}; roots={roots}; {context}"
        )
    if misplaced:
        errors.append(
            f"Codex skill discovery reported misplaced expected paths {misplaced}; "
            f"roots={roots}; {context}"
        )
    errors.extend(
        _validate_expected_discovery_root(
            discovered,
            expected_discovery_root=expected_discovery_root,
            catalog_dir=catalog_dir,
            route=route,
            managed_root_scope=managed_root_scope,
            context=context,
        )
    )
    return errors


def probe_codex_version(
    *,
    executable: str,
    env: Mapping[str, str],
    cwd: str,
    timeout_seconds: float = CODEX_DISCOVERY_ATTESTATION_TIMEOUT_SECONDS,
) -> tuple[str, str, list[str]]:
    """Probe one exact Codex executable and normalize its bounded version output."""
    result = _run_bounded_codex_probe(
        (executable, "--version"),
        env=env,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )
    context = _contract_context(
        executable=executable,
        catalog_dir="unbound",
        version="unknown",
        timeout_seconds=timeout_seconds,
    )
    if result.failure is not None:
        return (
            "",
            "",
            [f"Codex version probe {result.failure}; {_probe_diagnostic(result)}; {context}"],
        )
    if result.returncode != 0:
        return (
            "",
            "",
            [
                f"Codex version probe exited with status {result.returncode}; "
                f"{_probe_diagnostic(result)}; {context}"
            ],
        )
    try:
        raw = result.stdout.decode("utf-8").strip()
        normalized = normalize_codex_cli_version(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        return "", "", [f"Codex version probe returned malformed output: {exc}; {context}"]
    return raw, normalized, []


__all__ = [
    "CODEX_APP_SERVER_ROUTE",
    "CODEX_CLI_MIN_VERSION",
    "CODEX_DISCOVERY_ATTESTATION_TIMEOUT_SECONDS",
    "CODEX_MANAGED_HOME_ROUTE",
    "CODEX_PROJECTED_HOME_ROUTE",
    "CODEX_SKILL_DISCOVERY_CONTRACT",
    "CodexSkillDiscoveryContractDef",
    "DiscoveredSkills",
    "parse_skills_instructions",
    "probe_codex_version",
    "select_interactive_discovery_route",
]
