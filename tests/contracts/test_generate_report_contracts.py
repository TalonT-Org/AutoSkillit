"""Contract tests for generate-report SKILL.md — data provenance lifecycle."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "generate-report"
    / "SKILL.md"
)


def test_data_scope_statement_required() -> None:
    text = SKILL_PATH.read_text()
    assert "Data Scope Statement" in text or "data scope statement" in text.lower()


def test_data_scope_in_executive_summary() -> None:
    text = SKILL_PATH.read_text()
    assert "Executive Summary" in text


def test_metrics_provenance_check() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "provenance" in lower


def test_gate_enforcement_no_substitution() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "substitut" in lower


def test_gate_enforcement_fail_state() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "fail" in lower and "gate" in lower


def test_no_rust_specific_package_manager() -> None:
    """Environment section must not reference cargo tree (Rust-specific)."""
    text = SKILL_PATH.read_text()
    assert "cargo tree" not in text, (
        "generate-report/SKILL.md references 'cargo tree' (Rust-specific). "
        "Use language-agnostic package manager examples."
    )


def test_domain_adaptive_ordering_guidance() -> None:
    """SKILL.md must include guidance on domain-adaptive section ordering."""
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "biology" in lower, (
        "generate-report/SKILL.md has no domain-adaptive ordering guidance. "
        "Add notes on biology/non-engineering section ordering conventions."
    )
    assert "domain-adaptive" in lower, (
        "generate-report/SKILL.md must mention 'domain-adaptive' section ordering "
        "to verify the guidance actually covers non-engineering conventions."
    )


def test_data_availability_section_supported() -> None:
    """SKILL.md must include an optional Data Availability section in the template."""
    text = SKILL_PATH.read_text()
    assert "Data Availability" in text, (
        "generate-report/SKILL.md template is missing an optional 'Data Availability' "
        "section. Required by biology and social science journals."
    )


def test_recommendations_or_discussion_framing() -> None:
    """SKILL.md must allow 'Discussion and Future Directions' as an alternative
    to 'Recommendations' for non-engineering domains."""
    text = SKILL_PATH.read_text()
    assert "Discussion and Future Directions" in text, (
        "generate-report/SKILL.md does not offer 'Discussion and Future Directions' "
        "as an alternative framing for the Recommendations section."
    )


def test_generate_report_step25_no_host_venv() -> None:
    """Step 2.5 must not create a .plot-venv on the host filesystem."""
    text = SKILL_PATH.read_text()
    assert "### Step 2.5" in text, (
        "generate-report/SKILL.md is missing '### Step 2.5' section header"
    )
    assert "### Step 3" in text, "generate-report/SKILL.md is missing '### Step 3' section header"
    step25_section = text.split("### Step 2.5")[1].split("### Step 3")[0]
    assert ".plot-venv" not in step25_section, (
        "generate-report/SKILL.md Step 2.5 must not create a .plot-venv on the host — "
        "all package installation must happen inside the Docker container"
    )


def test_generate_report_step25_uses_docker_run() -> None:
    """Step 2.5 must use docker run to execute visualization scripts."""
    text = SKILL_PATH.read_text()
    assert "### Step 2.5" in text, (
        "generate-report/SKILL.md is missing '### Step 2.5' section header"
    )
    assert "### Step 3" in text, "generate-report/SKILL.md is missing '### Step 3' section header"
    step25_section = text.split("### Step 2.5")[1].split("### Step 3")[0]
    assert "docker run" in step25_section.lower(), (
        "generate-report/SKILL.md Step 2.5 must reference 'docker run' for executing "
        "visualization scripts inside the experiment container"
    )


def test_dockerfile_template_asset_exists() -> None:
    """assets/research/Dockerfile.template must exist."""
    asset_path = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "assets"
        / "research"
        / "Dockerfile.template"
    )
    assert asset_path.exists(), (
        "src/autoskillit/assets/research/Dockerfile.template must exist — "
        "this is the canonical Docker template for research worktrees"
    )


def test_dockerfile_template_uses_micromamba_base() -> None:
    """Dockerfile.template must use mambaorg/micromamba base image."""
    asset_path = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "assets"
        / "research"
        / "Dockerfile.template"
    )
    content = asset_path.read_text()
    assert "mambaorg/micromamba" in content, (
        "assets/research/Dockerfile.template must use mambaorg/micromamba as the base image"
    )


def test_dockerfile_template_wires_bash_env() -> None:
    """Dockerfile.template must set BASH_ENV to wire micromamba activation into every shell."""
    asset_path = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "assets"
        / "research"
        / "Dockerfile.template"
    )
    content = asset_path.read_text()
    assert "BASH_ENV" in content, (
        "assets/research/Dockerfile.template must set BASH_ENV to wire micromamba "
        "activation into every shell context (including non-interactive)"
    )
