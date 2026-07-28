import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
assert (REPO_ROOT / "pyproject.toml").exists(), "REPO_ROOT detection broken"
ORCH_DOC = REPO_ROOT / "docs" / "orchestration-levels.md"
GLOSSARY = REPO_ROOT / "docs" / "glossary.md"

pytestmark = [pytest.mark.layer("docs"), pytest.mark.medium]


def test_orchestration_levels_doc_exists():
    assert ORCH_DOC.exists(), "docs/orchestration-levels.md must be created"


def test_orchestration_levels_has_required_sections():
    text = ORCH_DOC.read_text()
    for heading in [
        "## Level Definitions",
        "## Mapping Table",
        "## Key Rules",
        "## Disambiguation",
    ]:
        assert heading in text, f"Missing section: {heading}"


def test_orchestration_levels_has_l_identifiers():
    text = ORCH_DOC.read_text()
    for level in ["L0", "L1", "L2", "L3"]:
        assert level in text


def test_glossary_has_orchestration_level_entries():
    text = GLOSSARY.read_text()
    for term in ["### L0", "### L1", "### L2", "### L3", "### food truck", "### Ghost Kitchen"]:
        assert term in text, f"Glossary missing entry: {term}"


def test_glossary_orchestrator_entry_uses_l2():
    text = GLOSSARY.read_text().replace("\r\n", "\n")
    match = re.search(r"### orchestrator\n(.+?)(?=\n###|\Z)", text, re.DOTALL)
    assert match, "Glossary missing ### orchestrator entry"
    section = match.group(1)
    assert "L2" in section, "'orchestrator' glossary entry must reference L2"
    assert "Tier 1" not in section, "'orchestrator' entry must not use 'Tier 1' language"


def test_glossary_worker_entry_uses_l1():
    text = GLOSSARY.read_text().replace("\r\n", "\n")
    match = re.search(r"### worker\n(.+?)(?=\n###|\Z)", text, re.DOTALL)
    assert match, "Glossary missing ### worker entry"
    section = match.group(1)
    assert "L1" in section, "'worker' glossary entry must reference L1"
    assert "Tier 2" not in section, "'worker' entry must not use 'Tier 2' language"


def test_orchestration_doc_cross_references_levels():
    orch_exec = REPO_ROOT / "docs" / "execution" / "orchestration.md"
    assert orch_exec.exists(), "docs/execution/orchestration.md must exist"
    text = orch_exec.read_text()
    assert "orchestration-levels.md" in text, (
        "docs/execution/orchestration.md must cross-reference orchestration-levels.md"
    )


def test_agents_md_has_il_disambiguation():
    agents_md = REPO_ROOT / "AGENTS.md"
    assert agents_md.exists(), f"AGENTS.md not found at {agents_md}"
    text = agents_md.read_text()
    assert "| IL-N" in text, "AGENTS.md must contain the '| IL-N' row in the disambiguation table"
    assert "| IL-NNN" in text, (
        "AGENTS.md must contain the '| IL-NNN' row in the disambiguation table"
    )
    assert "| L-N" in text, "AGENTS.md must contain the '| L-N' row in the disambiguation table"
    assert "Import layer level" in text, (
        "Disambiguation table must explain IL-N as 'Import layer level'"
    )
    assert "Import-linter contract ID" in text, (
        "Disambiguation table must explain IL-NNN as 'Import-linter contract ID'"
    )
    assert "Orchestration level" in text or "orchestration level" in text, (
        "Disambiguation table must explain L-N as orchestration level"
    )


@pytest.mark.parametrize("backend_name", ["claude-code", "codex"])
def test_process_issues_l2_run_skill_child_crosses_to_l1_session(backend_name: str) -> None:
    """Trace the real L2 contract through its child target and backend env boundary."""
    from autoskillit.core import SkillExecutionRole
    from autoskillit.execution import get_backend
    from autoskillit.workspace import DefaultSkillResolver
    from tests.execution.backends._plugin_binding import plugin_binding

    resolver = DefaultSkillResolver()
    parent = resolver.resolve_invocation(
        "process-issues",
        REPO_ROOT,
        SkillExecutionRole.ORCHESTRATOR,
    )
    match = re.search(r'run_skill\("(/autoskillit:([\w-]+)[^"]*)"', parent.root.canonical_content)
    assert match, "process-issues must dispatch at least one child through run_skill"
    child = resolver.resolve_invocation(
        match.group(2),
        REPO_ROOT,
        SkillExecutionRole.SESSION,
    )
    backend = get_backend(backend_name)
    with plugin_binding(Path("/projected-plugin")) as binding:
        parent_spec = backend.build_food_truck_cmd(
            orchestrator_prompt=parent.root.canonical_content,
            plugin_binding=binding,
            cwd=str(REPO_ROOT),
            completion_marker="%%L2_DONE%%",
        )
    child_spec = backend.build_skill_session_cmd(
        match.group(1),
        str(REPO_ROOT),
        completion_marker="%%L1_DONE%%",
    )

    assert parent.execution_role is SkillExecutionRole.ORCHESTRATOR
    assert child.execution_role is SkillExecutionRole.SESSION
    assert parent_spec.env["AUTOSKILLIT_SESSION_TYPE"] == "orchestrator"
    assert child_spec.env["AUTOSKILLIT_SESSION_TYPE"] == "skill"
