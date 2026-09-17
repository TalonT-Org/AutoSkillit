"""Shared JSON loading helpers for smoke_utils sub-modules."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.core import (
    ProbedRequirement,
    atomic_write,
    canonical_json_bytes,
    probe_substitutions,
)

_AUDIT_REQUIREMENT_KEYS = frozenset({"requirement_id", "requirement_text", "evidence_summary"})


def _load_json(src: str) -> list | dict:
    """Load JSON from a string or file path. Returns a list or dict."""
    try:
        return json.loads(src)
    except (json.JSONDecodeError, TypeError) as string_err:
        try:
            return json.loads(Path(src).read_text())
        except (OSError, json.JSONDecodeError) as file_err:
            raise file_err from string_err


def try_load_json(path: Path) -> dict | None:
    """Attempt to load JSON from path, returning None on failure."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _read_audit_probe_input(label: str, path: Path) -> str:
    """Read a probe input file with full orchestration context on failure."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"{label} is required for probe_audit_substitutions: {path}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is not valid UTF-8: {path} ({exc.reason})") from exc


def _decode_audit_requirements(label: str, path: Path) -> object:
    """Decode a probe requirements JSON file with orchestration context."""
    text = _read_audit_probe_input(label, path)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{label} is not valid JSON ({exc.msg} at line {exc.lineno} "
            f"column {exc.colno}): {path}"
        ) from exc


def probe_audit_substitutions(
    requirements_path: str,
    diff_path: str,
    output_dir: str,
) -> dict[str, str]:
    """Write deterministic substitution findings for audit-impl orchestration."""

    paths = {
        "requirements_path": Path(requirements_path),
        "diff_path": Path(diff_path),
        "output_dir": Path(output_dir),
    }
    for name, path in paths.items():
        if not path.is_absolute():
            raise ValueError(f"{name} must be absolute, got {str(path)!r}")

    raw_requirements = _decode_audit_requirements("requirements_path", paths["requirements_path"])
    if not isinstance(raw_requirements, list):
        raise ValueError(
            f"requirements_path must contain a JSON array, got {type(raw_requirements).__name__}"
        )
    requirements: list[ProbedRequirement] = []
    for index, raw in enumerate(raw_requirements):
        if not isinstance(raw, dict) or frozenset(raw) != _AUDIT_REQUIREMENT_KEYS:
            actual = sorted(raw) if isinstance(raw, dict) else type(raw).__name__
            raise ValueError(
                f"requirements_path[{index}] must contain exactly "
                f"{sorted(_AUDIT_REQUIREMENT_KEYS)!r}: got {actual}"
            )
        if not all(isinstance(raw[key], str) for key in _AUDIT_REQUIREMENT_KEYS):
            raise ValueError(f"requirements_path[{index}] values must all be strings")
        requirements.append(ProbedRequirement(**raw))

    findings = probe_substitutions(
        tuple(requirements),
        _read_audit_probe_input("diff_path", paths["diff_path"]),
    )
    payload = {
        "flagged_requirement_ids": list(
            dict.fromkeys(finding.requirement_id for finding in findings)
        ),
        "findings": [
            {
                "requirement_id": finding.requirement_id,
                "trigger": finding.trigger.value,
                "matched_marker": finding.matched_marker,
                "matched_cue": finding.matched_prescription,
            }
            for finding in findings
        ],
    }
    paths["output_dir"].mkdir(parents=True, exist_ok=True)
    output_path = paths["output_dir"] / "audit_substitution_probe.json"
    atomic_write(output_path, canonical_json_bytes(payload))
    return {"probe_path": str(output_path)}
