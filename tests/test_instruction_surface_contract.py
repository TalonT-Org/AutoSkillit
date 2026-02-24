"""Contract tests: every instruction surface must carry the pipeline tool restriction.

These tests verify that all surfaces where the orchestrator receives instructions
about native tool usage contain the full forbidden tool list with prohibition framing.
If any test fails, a drift has occurred and the corresponding surface needs updating.
"""

from __future__ import annotations

from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


class TestClaudeMdPipelineContract:
    """CLAUDE.md must contain a pipeline execution section naming all forbidden tools."""

    def test_claude_md_has_pipeline_section(self):
        from autoskillit.server import PIPELINE_FORBIDDEN_TOOLS

        claude_md = (_project_root() / "CLAUDE.md").read_text()

        # Must have a section header containing "Pipeline"
        assert "Pipeline" in claude_md, "CLAUDE.md must have a Pipeline section header"

        # Find the pipeline section content (everything after the header until next ##)
        import re

        match = re.search(
            r"###\s+\*?\*?3\.3[^#]*?Pipeline[^#]*?\*?\*?\s*\n(.*?)(?=\n##|\Z)",
            claude_md,
            re.DOTALL,
        )
        assert match, "CLAUDE.md must have a '### 3.3 Pipeline' section"
        section = match.group(1)

        # Must name every forbidden tool
        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in section]
        assert not missing, f"CLAUDE.md pipeline section missing tools: {missing}"

        # Must use prohibition framing
        prohibition_terms = ["NEVER", "Do NOT", "MUST NOT"]
        assert any(term in section for term in prohibition_terms), (
            "CLAUDE.md pipeline section must use prohibition framing"
        )

        # Must mention the delegation mechanism
        assert "run_skill" in section, (
            "CLAUDE.md pipeline section must mention run_skill as the delegation mechanism"
        )


class TestMakeScriptSkillContract:
    """make-script-skill SKILL.md must document the constraints field."""

    def _skill_md_text(self) -> str:
        skill_md = (
            _project_root() / "src" / "autoskillit" / "skills" / "make-script-skill" / "SKILL.md"
        )
        return skill_md.read_text()

    def test_schema_table_includes_constraints(self):
        text = self._skill_md_text()
        # The "Top-Level Fields" schema table must include a constraints row
        assert "| `constraints`" in text, (
            "make-script-skill SKILL.md schema table must include a 'constraints' row"
        )

    def test_example_yaml_includes_constraints(self):
        text = self._skill_md_text()
        # At least one YAML code block must include a constraints: field
        import re

        yaml_blocks = re.findall(r"```yaml\s*\n(.*?)```", text, re.DOTALL)
        has_constraints = any("constraints:" in block for block in yaml_blocks)
        assert has_constraints, (
            "make-script-skill SKILL.md must include 'constraints:' in at least one "
            "example YAML block"
        )

    def test_skill_md_names_forbidden_tools(self):
        text = self._skill_md_text()
        # Must name at least 3 forbidden tool names in the context of pipeline discipline
        from autoskillit.server import PIPELINE_FORBIDDEN_TOOLS

        found = [t for t in PIPELINE_FORBIDDEN_TOOLS if t in text]
        assert len(found) >= 3, (
            f"make-script-skill SKILL.md must name at least 3 forbidden tools, found only: {found}"
        )
