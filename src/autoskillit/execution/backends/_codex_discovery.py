"""Pinned parsing and attestation for Codex's model-visible skill catalog."""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import regex as re

from autoskillit.core import normalize_codex_cli_version, validate_managed_skill_entries
from autoskillit.execution.backends._codex_probes import (
    _probe_diagnostic,
    _run_bounded_codex_probe,
)


@dataclass(frozen=True, slots=True)
class CodexSkillDiscoveryContractDef:
    """Pinned upstream discovery behavior consumed by managed Codex launches."""

    legacy_root_relpath: str = "skills"
    catalog_relpath: str = "add-dir/skills"
    extra_roots_rpc: str = "skills/extraRoots/set"
    extra_roots_min_version: str = "0.136.0"
    prompt_probe: tuple[str, ...] = ("debug", "prompt-input")
    upstream_revision: str = "646f7c0a91b8e327d263335da68ae8ef212895ce"
    upstream_legacy_root_citation: str = "codex-rs/ext/skills/src/host_roots.rs:94-113"
    verified_binary: str = "codex-cli 0.153.4"


# The inner catalog remains upstream-rendered text. Its grammar was verified at
# codex-rs/core-skills/src/render.rs:62-85 (rust-v0.130.0) and at
# codex-rs/ext/skills/src/render.rs in the pinned revision above.
CODEX_SKILL_DISCOVERY_CONTRACT = CodexSkillDiscoveryContractDef()


@dataclass(frozen=True, slots=True)
class DiscoveredSkills:
    names: frozenset[str]
    roots: tuple[Path, ...]
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
        path = root / relative
    else:
        path = Path(normalized)
    if not path.is_absolute():
        raise ValueError(f"skill path is not absolute: {normalized!r}")
    return path


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

    aliases: dict[str, Path] = {}
    roots: list[Path] = []
    for raw_line in lines[roots_index + 1 : skills_index]:
        line = raw_line.strip()
        if not line:
            continue
        match = _ROOT_RE.fullmatch(line)
        if match is None:
            raise ValueError(f"malformed Skill roots entry: {line!r}")
        alias = match.group("alias")
        if alias in aliases:
            raise ValueError(f"duplicate skill-root alias: {alias}")
        root = Path(match.group("path"))
        if not root.is_absolute():
            raise ValueError(f"skill root is not absolute: {root}")
        aliases[alias] = root
        roots.append(root)

    paths: dict[str, Path] = {}
    pending_name: str | None = None
    for raw_line in lines[skills_index + 1 : -1]:
        line = raw_line.strip()
        if not line:
            continue
        match = _SKILL_RE.fullmatch(line)
        if match is not None:
            if pending_name is not None:
                raise ValueError(f"skill entry {pending_name!r} has no path")
            name = match.group("name").strip()
            if name in paths:
                raise ValueError(f"duplicate skill name: {name}")
            paths[name] = _expand_skill_path(match.group("path"), aliases)
            continue
        start_match = _SKILL_START_RE.match(line)
        if start_match is not None:
            if pending_name is not None:
                raise ValueError(f"skill entry {pending_name!r} has no path")
            pending_name = start_match.group("name").strip()
            if pending_name in paths:
                raise ValueError(f"duplicate skill name: {pending_name}")
            continue
        path_match = _PATH_ONLY_RE.fullmatch(line)
        if path_match is not None and pending_name is not None:
            paths[pending_name] = _expand_skill_path(path_match.group("path"), aliases)
            pending_name = None
            continue
        raise ValueError(f"malformed Available skills entry: {line!r}")
    if pending_name is not None:
        raise ValueError(f"skill entry {pending_name!r} has no path")
    return DiscoveredSkills(
        names=frozenset(paths),
        roots=tuple(roots),
        paths=MappingProxyType(paths),
    )


def _contract_context(
    *,
    executable: str,
    catalog_dir: Path | str,
    version: str,
) -> str:
    contract = CODEX_SKILL_DISCOVERY_CONTRACT
    return (
        f"contract_revision={contract.upstream_revision} "
        f"contract_source={contract.upstream_legacy_root_citation} "
        f"catalog={catalog_dir} executable={executable} version={version}"
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


def probe_codex_version(
    *,
    executable: str,
    env: Mapping[str, str],
    cwd: str,
    timeout_seconds: float = 30,
) -> tuple[str, str, list[str]]:
    """Probe one exact Codex executable and normalize its bounded version output."""
    result = _run_bounded_codex_probe(
        (executable, "--version"),
        env=env,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )
    context = _contract_context(executable=executable, catalog_dir="unbound", version="unknown")
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


def attest_catalog_discovery(
    *,
    probe_command: tuple[str, ...],
    env: Mapping[str, str],
    cwd: str,
    catalog_dir: Path,
    expected_entries: Sequence[tuple[str, str]],
    version: str,
    timeout_seconds: float = 30,
) -> list[str]:
    """Require Codex's real prompt loader to expose the frozen managed catalog."""
    executable = probe_command[0] if probe_command else "<missing>"
    context = _contract_context(
        executable=executable,
        catalog_dir=catalog_dir,
        version=version,
    )
    try:
        expected_paths = _validated_expected_paths(catalog_dir, expected_entries)
        before_fingerprint = _fingerprint_managed_files(expected_paths)
    except (OSError, ValueError) as exc:
        return [f"Codex skill discovery catalog validation failed: {exc}; {context}"]

    result = _run_bounded_codex_probe(
        probe_command,
        env=env,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
    )
    errors: list[str] = []
    if result.failure is not None:
        errors.append(
            f"Codex skill discovery probe {result.failure}; {_probe_diagnostic(result)}; {context}"
        )
    elif result.returncode != 0:
        errors.append(
            f"Codex skill discovery probe exited with status {result.returncode}; "
            f"{_probe_diagnostic(result)}; {context}"
        )
    else:
        try:
            prompt_json = result.stdout.decode("utf-8")
            discovered = parse_skills_instructions(prompt_json)
        except (UnicodeDecodeError, ValueError) as exc:
            errors.append(f"Codex skill discovery parse failed: {exc}; {context}")
        else:
            missing = sorted(set(expected_paths) - discovered.names)
            misplaced: list[str] = []
            for name, expected_path in expected_paths.items():
                actual_path = discovered.paths.get(name)
                if actual_path is None:
                    continue
                try:
                    actual_canonical = actual_path.resolve(strict=True)
                except OSError:
                    misplaced.append(f"{name}={actual_path} (unreadable)")
                    continue
                if actual_canonical != expected_path:
                    misplaced.append(f"{name}={actual_path}")
            if missing:
                errors.append(
                    f"Codex skill discovery is missing managed names {missing}; "
                    f"roots={[str(root) for root in discovered.roots]}; {context}"
                )
            if misplaced:
                errors.append(
                    f"Codex skill discovery reported misplaced managed paths {misplaced}; "
                    f"roots={[str(root) for root in discovered.roots]}; {context}"
                )
            legacy_root = (
                catalog_dir.parent.parent / CODEX_SKILL_DISCOVERY_CONTRACT.legacy_root_relpath
            )
            legacy_matches = [root for root in discovered.roots if root == legacy_root]
            if len(legacy_matches) != 1:
                errors.append(
                    f"Codex skill discovery roots do not contain legacy root {legacy_root}; "
                    f"roots={[str(root) for root in discovered.roots]}; {context}"
                )
            else:
                try:
                    legacy_target = legacy_matches[0].resolve(strict=True)
                except OSError as exc:
                    errors.append(
                        f"Codex skill discovery legacy root is unreadable: {exc}; {context}"
                    )
                else:
                    if legacy_target != catalog_dir:
                        errors.append(
                            "Codex skill discovery legacy root does not resolve to the managed "
                            f"catalog; roots={[str(root) for root in discovered.roots]}; {context}"
                        )
    try:
        after_fingerprint = _fingerprint_managed_files(expected_paths)
    except (OSError, ValueError) as exc:
        errors.append(f"Codex skill discovery mutated the managed catalog: {exc}; {context}")
    else:
        if after_fingerprint != before_fingerprint:
            errors.append(f"Codex skill discovery mutated the managed catalog; {context}")
    return errors


__all__ = [
    "CODEX_SKILL_DISCOVERY_CONTRACT",
    "CodexSkillDiscoveryContractDef",
    "DiscoveredSkills",
    "attest_catalog_discovery",
    "parse_skills_instructions",
    "probe_codex_version",
]
