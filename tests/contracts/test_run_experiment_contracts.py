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


def test_blocked_hypotheses_token_documented() -> None:
    text = SKILL_PATH.read_text()
    assert "blocked_hypotheses" in text


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


def test_run_experiment_micromamba_run_command() -> None:
    """run-experiment must include micromamba run command for host fallback."""
    text = SKILL_PATH.read_text()
    assert "micromamba run" in text, (
        "run-experiment/SKILL.md must include 'micromamba run' command "
        "for the micromamba-host execution path"
    )
