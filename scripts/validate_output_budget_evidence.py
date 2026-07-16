#!/usr/bin/env python3
"""Validate the Output Budget Protocol remediation evidence manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PHASES = (1, 2, 3, 4)
HISTORICAL_PHASE_SHAS = (
    "8091755f5d4beffbf5d368625a5fb7fb055aae6e",
    "0726dc802bc7d04b16a30d96ecf8642164dcb4f3",
    "f43b98ddefbf7e4089f88d8f3ec089c1d17bc7ae",
    "093179c8f7eb3ebe3b5783d84409d0b074333686",
)
REQUIRED_PHASE_GATE_COMMANDS = {
    1: frozenset({"task test-all", "pre-commit run --all-files"}),
    2: frozenset({"task test-all", "pre-commit run --all-files"}),
    3: frozenset(
        {
            "task test-codex-config-parse",
            "task test-all",
            "pre-commit run --all-files",
        }
    ),
    4: frozenset({"task test-all", "pre-commit run --all-files"}),
}
REQUIRED_PROBES = (
    "installed_codex_parse",
    "generated_codex_child",
    "deep_codex",
    "deep_claude_200k",
)
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


class EvidenceValidationError(ValueError):
    """Raised when evidence does not satisfy the remediation contract."""


def _require_dict(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceValidationError(f"{location} must be an object")
    return value


def _require_list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvidenceValidationError(f"{location} must be an array")
    return value


def _require_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceValidationError(f"{location} must be a non-empty string")
    return value


def _require_sha(value: Any, location: str) -> str:
    sha = _require_string(value, location)
    if SHA_RE.fullmatch(sha) is None:
        raise EvidenceValidationError(f"{location} must be a lowercase 40-hex SHA")
    return sha


def _require_digest(value: Any, location: str) -> str:
    digest = _require_string(value, location)
    if DIGEST_RE.fullmatch(digest) is None:
        raise EvidenceValidationError(f"{location} must be a lowercase SHA-256 digest")
    return digest


def _resolve_evidence_path(repo_root: Path, value: Any, location: str) -> Path:
    relative = Path(_require_string(value, location))
    if relative.is_absolute():
        raise EvidenceValidationError(f"{location} must be repository-relative")
    root = repo_root.resolve()
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise EvidenceValidationError(f"{location} escapes the repository root") from exc
    if not resolved.is_file():
        raise EvidenceValidationError(f"{location} does not exist: {relative}")
    return resolved


def _validate_file_reference(
    record: dict[str, Any],
    repo_root: Path,
    *,
    location: str,
    path_key: str,
    digest_key: str,
) -> Path:
    path = _resolve_evidence_path(repo_root, record.get(path_key), f"{location}.{path_key}")
    expected = _require_digest(record.get(digest_key), f"{location}.{digest_key}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise EvidenceValidationError(f"{location}.{digest_key} mismatch for {record[path_key]}")
    return path


def _validate_gate(
    value: Any,
    repo_root: Path,
    *,
    location: str,
    expected_sha: str,
) -> dict[str, Any]:
    gate = _require_dict(value, location)
    _require_string(gate.get("command"), f"{location}.command")
    if gate.get("status") != "pass":
        raise EvidenceValidationError(f"{location}.status must be 'pass'")
    _require_string(gate.get("summary"), f"{location}.summary")
    tested_sha = _require_sha(gate.get("gate_tested_sha"), f"{location}.gate_tested_sha")
    if tested_sha != expected_sha:
        raise EvidenceValidationError(f"{location}.gate_tested_sha must equal {expected_sha}")
    _validate_file_reference(
        gate,
        repo_root,
        location=location,
        path_key="gate_log_path",
        digest_key="gate_log_sha256",
    )
    return gate


def _validate_historical_context(manifest: dict[str, Any], repo_root: Path) -> None:
    entries = _require_list(manifest.get("historical_context"), "historical_context")
    if [entry.get("phase") for entry in entries if isinstance(entry, dict)] != list(PHASES):
        raise EvidenceValidationError("historical_context must contain phases 1 through 4")

    seen: set[str] = set()
    for index, value in enumerate(entries):
        location = f"historical_context[{index}]"
        entry = _require_dict(value, location)
        sha = _require_sha(entry.get("phase_commit_sha"), f"{location}.phase_commit_sha")
        if sha in seen:
            raise EvidenceValidationError("historical phase_commit_sha values must be distinct")
        seen.add(sha)
        if entry.get("bound_to_commit") is not False:
            raise EvidenceValidationError(f"{location}.bound_to_commit must be false")
        _require_string(entry.get("summary"), f"{location}.summary")
        _validate_file_reference(
            entry,
            repo_root,
            location=location,
            path_key="gate_log_path",
            digest_key="gate_log_sha256",
        )

    actual_shas = tuple(entry["phase_commit_sha"] for entry in entries)
    if actual_shas != HISTORICAL_PHASE_SHAS:
        raise EvidenceValidationError(
            "historical_context phase_commit_sha values do not match the audited baseline"
        )

    baseline = _require_sha(
        manifest.get("implementation_baseline_sha"), "implementation_baseline_sha"
    )
    if entries[-1]["phase_commit_sha"] != baseline:
        raise EvidenceValidationError("implementation_baseline_sha must equal historical phase 4")


def _validate_phases(manifest: dict[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    values = _require_list(manifest.get("phases"), "phases")
    if len(values) > len(PHASES):
        raise EvidenceValidationError("phases contains more than four records")
    expected_prefix = list(PHASES[: len(values)])
    actual = [value.get("phase") for value in values if isinstance(value, dict)]
    if actual != expected_prefix:
        raise EvidenceValidationError("phases must be the ordered prefix 1, 2, 3, 4")

    baseline = _require_sha(
        manifest.get("implementation_baseline_sha"), "implementation_baseline_sha"
    )
    seen = {baseline}
    phases: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        location = f"phases[{index}]"
        phase = _require_dict(value, location)
        phase_sha = _require_sha(phase.get("phase_commit_sha"), f"{location}.phase_commit_sha")
        gate_sha = _require_sha(phase.get("gate_tested_sha"), f"{location}.gate_tested_sha")
        if phase_sha != gate_sha:
            raise EvidenceValidationError(
                f"{location}.phase_commit_sha must equal gate_tested_sha"
            )
        if phase_sha in seen:
            raise EvidenceValidationError("phase commit SHAs must be distinct and post-baseline")
        seen.add(phase_sha)
        gates = _require_list(phase.get("gates"), f"{location}.gates")
        if not gates:
            raise EvidenceValidationError(f"{location}.gates must not be empty")
        commands: set[str] = set()
        for gate_index, gate in enumerate(gates):
            validated = _validate_gate(
                gate,
                repo_root,
                location=f"{location}.gates[{gate_index}]",
                expected_sha=phase_sha,
            )
            commands.add(validated["command"])
        required_commands = REQUIRED_PHASE_GATE_COMMANDS[phase["phase"]]
        if not required_commands <= commands:
            missing = sorted(required_commands - commands)
            raise EvidenceValidationError(
                f"{location}.gates missing required command(s): {missing}"
            )
        phases.append(phase)
    return phases


def _validate_indexed_closure_artifacts(
    manifest: dict[str, Any], repo_root: Path
) -> dict[str, Any] | None:
    value = manifest.get("closure")
    if value is None:
        return None
    closure = _require_dict(value, "closure")
    artifacts = _require_list(
        closure.get("historical_artifacts", []), "closure.historical_artifacts"
    )
    for index, artifact in enumerate(artifacts):
        record = _require_dict(artifact, f"closure.historical_artifacts[{index}]")
        _validate_file_reference(
            record,
            repo_root,
            location=f"closure.historical_artifacts[{index}]",
            path_key="path",
            digest_key="response_content_sha256",
        )
    return closure


def _validate_probe(
    value: Any,
    repo_root: Path,
    *,
    location: str,
    expected_sha: str,
) -> None:
    probe = _require_dict(value, location)
    if probe.get("status") != "pass":
        raise EvidenceValidationError(f"{location}.status must be 'pass'")
    _require_string(probe.get("command"), f"{location}.command")
    _require_string(probe.get("summary"), f"{location}.summary")
    tested_sha = _require_sha(probe.get("gate_tested_sha"), f"{location}.gate_tested_sha")
    if tested_sha != expected_sha:
        raise EvidenceValidationError(f"{location}.gate_tested_sha must equal {expected_sha}")
    _validate_file_reference(
        probe,
        repo_root,
        location=location,
        path_key="gate_log_path",
        digest_key="gate_log_sha256",
    )


def _validate_closure_postcondition(closure: dict[str, Any], repo_root: Path) -> None:
    merge_sha = _require_sha(closure.get("closure_pr_merge_sha"), "closure.closure_pr_merge_sha")
    postcondition = _require_dict(closure.get("postcondition"), "closure.postcondition")
    path = _validate_file_reference(
        postcondition,
        repo_root,
        location="closure.postcondition",
        path_key="path",
        digest_key="response_content_sha256",
    )
    try:
        response = _require_dict(json.loads(path.read_text()), "closure postcondition")
        repository = _require_dict(
            _require_dict(response.get("data"), "closure postcondition.data").get("repository"),
            "closure postcondition.data.repository",
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EvidenceValidationError("closure postcondition is not valid JSON") from exc

    for issue_number in (3938, 4253):
        issue = _require_dict(
            repository.get(f"issue{issue_number}"),
            f"closure postcondition issue{issue_number}",
        )
        if issue.get("state") != "CLOSED" or "## Closure" not in str(issue.get("body", "")):
            raise EvidenceValidationError(
                f"issue #{issue_number} must be CLOSED with a Closure section"
            )

    pull_request = _require_dict(repository.get("pr4259"), "closure postcondition pr4259")
    merge_commit = _require_dict(
        pull_request.get("mergeCommit"), "closure postcondition pr4259.mergeCommit"
    )
    if pull_request.get("state") != "MERGED" or merge_commit.get("oid") != merge_sha:
        raise EvidenceValidationError("PR #4259 must be MERGED at closure_pr_merge_sha")


def validate_manifest(manifest: dict[str, Any], repo_root: Path, *, mode: str) -> None:
    """Validate one manifest in incremental or complete mode."""
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise EvidenceValidationError(f"schema_version must be {SCHEMA_VERSION}")
    if mode not in {"incremental", "complete"}:
        raise EvidenceValidationError("mode must be incremental or complete")

    _validate_historical_context(manifest, repo_root)
    baseline_sha = _require_sha(
        manifest.get("implementation_baseline_sha"), "implementation_baseline_sha"
    )
    _validate_gate(
        manifest.get("baseline_regression"),
        repo_root,
        location="baseline_regression",
        expected_sha=baseline_sha,
    )
    phases = _validate_phases(manifest, repo_root)
    closure = _validate_indexed_closure_artifacts(manifest, repo_root)
    if mode == "incremental":
        return

    if len(phases) != len(PHASES):
        raise EvidenceValidationError("complete mode requires all four phases")
    audit_base_sha = _require_sha(manifest.get("audit_base_sha"), "audit_base_sha")
    audit_head_sha = _require_sha(manifest.get("audit_head_sha"), "audit_head_sha")
    if audit_base_sha == audit_head_sha:
        raise EvidenceValidationError("audit_base_sha and audit_head_sha must differ")
    if phases[-1]["phase_commit_sha"] != audit_head_sha:
        raise EvidenceValidationError("audit_head_sha must equal the Phase 4 phase_commit_sha")

    final_gates = _require_list(manifest.get("final_gates"), "final_gates")
    commands = set()
    for index, gate in enumerate(final_gates):
        validated = _validate_gate(
            gate,
            repo_root,
            location=f"final_gates[{index}]",
            expected_sha=audit_head_sha,
        )
        commands.add(validated["command"])
    if "task test-all" not in commands or "pre-commit run --all-files" not in commands:
        raise EvidenceValidationError(
            "complete mode requires final task test-all and pre-commit gates"
        )

    probes = _require_dict(manifest.get("probes"), "probes")
    for name in REQUIRED_PROBES:
        expected_sha = (
            phases[2]["phase_commit_sha"] if name == "installed_codex_parse" else audit_head_sha
        )
        _validate_probe(
            probes.get(name),
            repo_root,
            location=f"probes.{name}",
            expected_sha=expected_sha,
        )

    if closure is None:
        raise EvidenceValidationError("complete mode requires closure evidence")
    _validate_closure_postcondition(closure, repo_root)


def _git_root(manifest_path: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(manifest_path.parent), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=Path(".autoskillit/evidence/output-budget-remediation/manifest.json"),
    )
    parser.add_argument("--mode", choices=("incremental", "complete"), required=True)
    args = parser.parse_args(argv)

    try:
        manifest_path = args.manifest.resolve(strict=True)
        repo_root = _git_root(manifest_path)
        manifest = _require_dict(json.loads(manifest_path.read_text()), "manifest")
        validate_manifest(manifest, repo_root, mode=args.mode)
    except (
        EvidenceValidationError,
        FileNotFoundError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"OUTPUT_BUDGET_EVIDENCE=FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"OUTPUT_BUDGET_EVIDENCE=PASS mode={args.mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
