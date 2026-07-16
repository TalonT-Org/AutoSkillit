from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.small

SCRIPT = Path(__file__).parents[2] / "scripts" / "validate_output_budget_evidence.py"


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("output_budget_evidence", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def _write_evidence(repo_root: Path, name: str, content: str = "passed\n") -> dict[str, str]:
    path = repo_root / "evidence" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return {
        "gate_log_path": path.relative_to(repo_root).as_posix(),
        "gate_log_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _gate(repo_root: Path, name: str, sha: str, command: str = "task test-all") -> dict:
    return {
        "command": command,
        "status": "pass",
        "summary": f"{name} passed",
        "gate_tested_sha": sha,
        **_write_evidence(repo_root, f"{name}.log"),
    }


def _manifest(repo_root: Path) -> dict:
    historical = []
    for phase, sha in enumerate(validator.HISTORICAL_PHASE_SHAS, start=1):
        historical.append(
            {
                "phase": phase,
                "phase_commit_sha": sha,
                "bound_to_commit": False,
                "summary": "Passing summary without embedded command or SHA provenance",
                **_write_evidence(repo_root, f"historical-{phase}.log"),
            }
        )
    baseline_sha = validator.HISTORICAL_PHASE_SHAS[-1]
    return {
        "schema_version": 1,
        "implementation_baseline_sha": baseline_sha,
        "historical_context": historical,
        "baseline_regression": _gate(
            repo_root,
            "baseline-regression",
            baseline_sha,
            command="task test-check",
        ),
        "phases": [],
    }


def _complete_manifest(repo_root: Path) -> dict:
    manifest = _manifest(repo_root)
    for phase, digit in enumerate("5678", start=1):
        sha = digit * 40
        manifest["phases"].append(
            {
                "phase": phase,
                "phase_commit_sha": sha,
                "gate_tested_sha": sha,
                "gates": [
                    _gate(repo_root, f"phase-{phase}", sha),
                    _gate(
                        repo_root,
                        f"phase-{phase}-pre-commit",
                        sha,
                        command="pre-commit run --all-files",
                    ),
                    *(
                        [
                            _gate(
                                repo_root,
                                "phase-3-codex-parse",
                                sha,
                                command="task test-codex-config-parse",
                            )
                        ]
                        if phase == 3
                        else []
                    ),
                ],
            }
        )

    head_sha = "8" * 40
    manifest["audit_base_sha"] = "0" * 40
    manifest["audit_head_sha"] = head_sha
    manifest["final_gates"] = [
        _gate(repo_root, "final-tests", head_sha),
        _gate(
            repo_root,
            "final-pre-commit",
            head_sha,
            command="pre-commit run --all-files",
        ),
    ]
    manifest["probes"] = {
        name: {
            "status": "pass",
            "command": f"task {name}",
            "summary": f"{name} passed",
            "gate_tested_sha": (
                manifest["phases"][2]["phase_commit_sha"]
                if name == "installed_codex_parse"
                else head_sha
            ),
            **_write_evidence(repo_root, f"{name}.log"),
        }
        for name in validator.REQUIRED_PROBES
    }

    postcondition_path = repo_root / "evidence" / "closure.json"
    postcondition_path.write_text(
        json.dumps(
            {
                "data": {
                    "repository": {
                        "issue3938": {"state": "CLOSED", "body": "## Closure\nDone"},
                        "issue4253": {"state": "CLOSED", "body": "## Closure\nDone"},
                        "pr4259": {
                            "state": "MERGED",
                            "mergeCommit": {"oid": "9" * 40},
                        },
                    }
                }
            }
        )
    )
    historical_closure = repo_root / "evidence" / "closure-history.json"
    historical_closure.write_text("{}")
    manifest["closure"] = {
        "closure_pr_merge_sha": "9" * 40,
        "historical_artifacts": [
            {
                "path": historical_closure.relative_to(repo_root).as_posix(),
                "response_content_sha256": hashlib.sha256(
                    historical_closure.read_bytes()
                ).hexdigest(),
            }
        ],
        "postcondition": {
            "path": postcondition_path.relative_to(repo_root).as_posix(),
            "response_content_sha256": hashlib.sha256(postcondition_path.read_bytes()).hexdigest(),
        },
    }
    return manifest


def test_incremental_accepts_empty_ordered_prefix(tmp_path: Path) -> None:
    validator.validate_manifest(_manifest(tmp_path), tmp_path, mode="incremental")


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_incremental_accepts_each_ordered_phase_prefix(tmp_path: Path, count: int) -> None:
    manifest = _complete_manifest(tmp_path)
    manifest["phases"] = manifest["phases"][:count]
    validator.validate_manifest(manifest, tmp_path, mode="incremental")


def test_incremental_rejects_out_of_order_phase(tmp_path: Path) -> None:
    manifest = _complete_manifest(tmp_path)
    manifest["phases"] = [manifest["phases"][1]]

    with pytest.raises(validator.EvidenceValidationError, match="ordered prefix"):
        validator.validate_manifest(manifest, tmp_path, mode="incremental")


def test_incremental_rejects_phase_gate_sha_mismatch(tmp_path: Path) -> None:
    manifest = _complete_manifest(tmp_path)
    manifest["phases"][0]["gate_tested_sha"] = "a" * 40

    with pytest.raises(validator.EvidenceValidationError, match="must equal gate_tested_sha"):
        validator.validate_manifest(manifest, tmp_path, mode="incremental")


def test_incremental_rejects_unexpected_historical_baseline(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    manifest["historical_context"][0]["phase_commit_sha"] = "a" * 40

    with pytest.raises(validator.EvidenceValidationError, match="audited baseline"):
        validator.validate_manifest(manifest, tmp_path, mode="incremental")


def test_incremental_requires_phase_pre_commit_gate(tmp_path: Path) -> None:
    manifest = _complete_manifest(tmp_path)
    manifest["phases"] = manifest["phases"][:1]
    manifest["phases"][0]["gates"] = manifest["phases"][0]["gates"][:1]

    with pytest.raises(validator.EvidenceValidationError, match="pre-commit"):
        validator.validate_manifest(manifest, tmp_path, mode="incremental")


def test_incremental_rejects_stale_evidence_hash(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    manifest["historical_context"][0]["gate_log_sha256"] = "0" * 64

    with pytest.raises(validator.EvidenceValidationError, match="mismatch"):
        validator.validate_manifest(manifest, tmp_path, mode="incremental")


def test_incremental_rejects_missing_evidence_file(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    manifest["historical_context"][0]["gate_log_path"] = "evidence/missing.log"

    with pytest.raises(validator.EvidenceValidationError, match="does not exist"):
        validator.validate_manifest(manifest, tmp_path, mode="incremental")


def test_complete_requires_all_four_phases(tmp_path: Path) -> None:
    manifest = _complete_manifest(tmp_path)
    manifest["phases"].pop()

    with pytest.raises(validator.EvidenceValidationError, match="all four phases"):
        validator.validate_manifest(manifest, tmp_path, mode="complete")


def test_complete_requires_final_gate_sha_to_match_audit_head(tmp_path: Path) -> None:
    manifest = _complete_manifest(tmp_path)
    manifest["final_gates"][0]["gate_tested_sha"] = "a" * 40

    with pytest.raises(validator.EvidenceValidationError, match="must equal"):
        validator.validate_manifest(manifest, tmp_path, mode="complete")


@pytest.mark.parametrize(
    ("probe_name", "expected_fragment"),
    [
        ("installed_codex_parse", "phase"),
        ("deep_codex", "8" * 40),
    ],
)
def test_complete_requires_probe_sha_binding(
    tmp_path: Path, probe_name: str, expected_fragment: str
) -> None:
    manifest = _complete_manifest(tmp_path)
    manifest["probes"][probe_name]["gate_tested_sha"] = "a" * 40

    with pytest.raises(validator.EvidenceValidationError, match="must equal") as exc_info:
        validator.validate_manifest(manifest, tmp_path, mode="complete")
    if expected_fragment != "phase":
        assert expected_fragment in str(exc_info.value)


def test_complete_rejects_closure_response_without_closure_section(tmp_path: Path) -> None:
    manifest = _complete_manifest(tmp_path)
    postcondition = tmp_path / manifest["closure"]["postcondition"]["path"]
    response = json.loads(postcondition.read_text())
    response["data"]["repository"]["issue3938"]["body"] = "No closure evidence"
    postcondition.write_text(json.dumps(response))
    manifest["closure"]["postcondition"]["response_content_sha256"] = hashlib.sha256(
        postcondition.read_bytes()
    ).hexdigest()

    with pytest.raises(validator.EvidenceValidationError, match="#3938"):
        validator.validate_manifest(manifest, tmp_path, mode="complete")


def test_complete_accepts_bound_phase_gate_probe_and_closure_evidence(
    tmp_path: Path,
) -> None:
    validator.validate_manifest(_complete_manifest(tmp_path), tmp_path, mode="complete")
