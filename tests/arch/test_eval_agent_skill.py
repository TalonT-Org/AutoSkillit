"""Structural integrity tests for the eval-agent skill."""

from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SKILL_DIR = _PROJECT_ROOT / ".autoskillit" / "skills" / "eval-agent"
_SKILL_FILE = _SKILL_DIR / "SKILL.md"
_RECIPE_FILE = _PROJECT_ROOT / ".autoskillit" / "recipes" / "eval" / "agent-eval.yaml"


def test_eval_agent_skill_exists():
    """eval-agent SKILL.md exists at .autoskillit/skills/eval-agent/SKILL.md."""
    assert _SKILL_FILE.is_file(), ".autoskillit/skills/eval-agent/SKILL.md must exist"


def test_eval_agent_frontmatter_name():
    """Frontmatter name field matches directory name."""
    source = _SKILL_FILE.read_text()
    parts = source.split("---", 2)
    assert len(parts) >= 3, "SKILL.md must have YAML frontmatter"
    fm = yaml.safe_load(parts[1])
    assert isinstance(fm, dict), "frontmatter must parse to dict"
    assert fm.get("name") == "eval-agent"


def test_eval_agent_categories_include_eval():
    """Frontmatter categories includes eval."""
    source = _SKILL_FILE.read_text()
    parts = source.split("---", 2)
    fm = yaml.safe_load(parts[1])
    assert "eval" in fm.get("categories", [])


def test_eval_agent_has_critical_constraints():
    """SKILL.md has Critical Constraints with NEVER and ALWAYS blocks."""
    source = _SKILL_FILE.read_text()
    assert "## Critical Constraints" in source
    assert "**NEVER:**" in source
    assert "**ALWAYS:**" in source


def test_eval_agent_uses_agent_tool():
    """SKILL.md instructs use of native Agent tool with autoskillit: subagent_type."""
    source = _SKILL_FILE.read_text()
    assert "Agent(subagent_type=" in source
    assert "autoskillit:" in source


def test_eval_agent_uses_write_tool():
    """SKILL.md instructs use of native Write tool for output capture."""
    source = _SKILL_FILE.read_text()
    assert "Write" in source and "tool" in source.lower()


def test_eval_agent_emits_output_token():
    """SKILL.md emits agent_output_path structured output token."""
    source = _SKILL_FILE.read_text()
    assert "agent_output_path =" in source


def test_eval_agent_no_source_modification():
    """SKILL.md prohibits source code modification."""
    source = _SKILL_FILE.read_text()
    never_section = (
        source.split("**NEVER:**")[1].split("**ALWAYS:**")[0]
        if "**NEVER:**" in source and "**ALWAYS:**" in source
        else ""
    )
    assert "source code" in never_section.lower() or "Modify any source" in never_section


def test_eval_agent_no_kitchen_tools():
    """SKILL.md does not reference MCP kitchen tools."""
    source = _SKILL_FILE.read_text()
    assert "open_kitchen" not in source
    assert "run_skill" not in source
    assert "run_cmd" not in source


def test_agent_eval_recipe_uses_bare_invocation():
    """agent-eval.yaml invokes eval-agent as bare /eval-agent (project-local convention)."""
    content = _RECIPE_FILE.read_text()
    assert "/eval-agent " in content
    assert "/autoskillit:eval-agent" not in content


def test_eval_agent_references_temp_variable():
    """SKILL.md uses {{AUTOSKILLIT_TEMP}} not literal paths."""
    source = _SKILL_FILE.read_text()
    assert "{{AUTOSKILLIT_TEMP}}" in source


def test_eval_agent_error_handling_writes_json():
    """SKILL.md documents error handling that writes JSON and emits token on failure."""
    source = _SKILL_FILE.read_text()
    assert '"success": false' in source or '"success":false' in source
    assert "error" in source.lower()
