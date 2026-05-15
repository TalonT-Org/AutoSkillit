"""Contract tests for run-experiment SKILL.md — data provenance lifecycle."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "run-experiment"
    / "SKILL.md"
)


def test_blocked_hypotheses_declared_in_contract() -> None:
    """run-experiment contract must declare blocked_hypotheses as an output."""
    import yaml

    contract_path = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "recipe"
        / "skill_contracts.yaml"
    )
    manifest = yaml.safe_load(contract_path.read_text())
    run_exp = manifest.get("skills", {}).get("run-experiment", {})
    output_names = [out["name"] for out in run_exp.get("outputs", [])]
    assert "blocked_hypotheses" in output_names, (
        "run-experiment contract must declare blocked_hypotheses as an output"
    )


def test_verdict_declared_in_run_experiment_contract() -> None:
    """run-experiment contract must declare verdict output with allowed_values."""
    import yaml

    contract_path = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "recipe"
        / "skill_contracts.yaml"
    )
    manifest = yaml.safe_load(contract_path.read_text())
    run_exp = manifest.get("skills", {}).get("run-experiment", {})
    outputs = run_exp.get("outputs", [])
    verdict_output = next((o for o in outputs if o.get("name") == "verdict"), None)
    assert verdict_output is not None, "run-experiment contract must declare a verdict output"
    assert "allowed_values" in verdict_output, "verdict output must have allowed_values"
    allowed = verdict_output["allowed_values"]
    assert "CONCLUSIVE" in allowed, "verdict allowed_values must include CONCLUSIVE"
    assert "BLOCKED" in allowed, "verdict allowed_values must include BLOCKED"
    assert "INCONCLUSIVE" in allowed, "verdict allowed_values must include INCONCLUSIVE"


def test_data_manifest_preflight_check() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "data manifest" in lower
    assert "pre-flight" in lower or "preflight" in lower


def test_run_experiment_env_mode_dispatch() -> None:
    """run-experiment must dispatch execution based on env_mode."""
    text = SKILL_PATH.read_text()
    assert "env_mode" in text, (
        "run-experiment/SKILL.md must reference 'env_mode' for execution dispatch"
    )
    assert "blocked_experiment" in text, (
        "run-experiment/SKILL.md must emit 'blocked_experiment' token when env_mode is unavailable"
    )


def test_run_experiment_group_manifest_output() -> None:
    """run-experiment contract must declare group_manifest as an output."""
    import yaml

    contract_path = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "recipes"
        / "contracts"
        / "research.yaml"
    )
    manifest = yaml.safe_load(contract_path.read_text())
    assert isinstance(manifest, dict), f"research.yaml did not parse to a dict: {type(manifest)}"
    assert "skills" in manifest, "research.yaml missing top-level 'skills' key"
    assert "run-experiment" in manifest["skills"], (
        "research.yaml 'skills' missing 'run-experiment' entry"
    )
    run_exp = manifest["skills"]["run-experiment"]
    output_names = [out["name"] for out in run_exp.get("outputs", [])]
    assert "group_manifest" in output_names, (
        "run-experiment contract must declare group_manifest as an output"
    )


def test_run_experiment_micromamba_run_command() -> None:
    """run-experiment must include micromamba run command for host fallback."""
    text = SKILL_PATH.read_text()
    assert "micromamba run" in text, (
        "run-experiment/SKILL.md must include 'micromamba run' command "
        "for the micromamba-host execution path"
    )
