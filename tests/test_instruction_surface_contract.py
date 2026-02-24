"""Contract tests: every instruction surface must carry the pipeline tool restriction.

These tests verify that all surfaces where the orchestrator receives instructions
about native tool usage contain the full forbidden tool list with prohibition framing.
If any test fails, a drift has occurred and the corresponding surface needs updating.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.types import PIPELINE_FORBIDDEN_TOOLS


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


class TestClaudeMdPipelineContract:
    """CLAUDE.md must contain a pipeline execution section naming all forbidden tools."""

    def test_claude_md_has_pipeline_section(self):
        claude_md = (_project_root() / "CLAUDE.md").read_text()

        assert "Pipeline" in claude_md, "CLAUDE.md must have a Pipeline section header"

        match = re.search(
            r"###\s+\*?\*?3\.3[^#]*?Pipeline[^#]*?\*?\*?\s*\n(.*?)(?=\n##|\Z)",
            claude_md,
            re.DOTALL,
        )
        assert match, "CLAUDE.md must have a '### 3.3 Pipeline' section"
        section = match.group(1)

        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in section]
        assert not missing, f"CLAUDE.md pipeline section missing tools: {missing}"

        prohibition_terms = ["NEVER", "Do NOT", "MUST NOT"]
        assert any(term in section for term in prohibition_terms), (
            "CLAUDE.md pipeline section must use prohibition framing"
        )

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
        assert "| `constraints`" in text, (
            "make-script-skill SKILL.md schema table must include a 'constraints' row"
        )

    def test_example_yaml_includes_constraints(self):
        text = self._skill_md_text()
        yaml_blocks = re.findall(r"```yaml\s*\n(.*?)```", text, re.DOTALL)
        has_constraints = any("constraints:" in block for block in yaml_blocks)
        assert has_constraints, (
            "make-script-skill SKILL.md must include 'constraints:' in at least one "
            "example YAML block"
        )

    def test_skill_md_names_forbidden_tools(self):
        text = self._skill_md_text()
        found = [t for t in PIPELINE_FORBIDDEN_TOOLS if t in text]
        assert len(found) >= 3, (
            f"make-script-skill SKILL.md must name at least 3 forbidden tools, found only: {found}"
        )


class TestServerToolSurfaceContract:
    """Server tool docstrings and prompts must name all forbidden tools."""

    @pytest.fixture(autouse=True)
    def _disable_tools(self, monkeypatch):
        import autoskillit.server as srv

        monkeypatch.setattr(srv, "_tools_enabled", False)

    def test_enable_tools_prompt_names_all_forbidden_tools(self):
        """enable_tools prompt text must name every forbidden tool with prohibition framing."""
        from autoskillit.server import enable_tools

        result = enable_tools()
        content = result.messages[0].content
        text = content.text if hasattr(content, "text") else str(content)

        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in text]
        assert not missing, f"enable_tools prompt missing tools: {missing}"

        has_framing = any(term in text for term in ("NEVER", "Do NOT", "MUST NOT"))
        assert has_framing, "enable_tools prompt lacks prohibition framing (NEVER/Do NOT/MUST NOT)"

    def test_run_skill_docstring_names_all_forbidden_tools(self):
        """run_skill docstring must name every forbidden tool."""
        from autoskillit.server import run_skill

        doc = run_skill.__doc__
        assert doc, "run_skill has no docstring"
        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in doc]
        assert not missing, f"run_skill docstring missing tools: {missing}"

    def test_run_skill_retry_docstring_names_all_forbidden_tools(self):
        """run_skill_retry docstring must name every forbidden tool."""
        from autoskillit.server import run_skill_retry

        doc = run_skill_retry.__doc__
        assert doc, "run_skill_retry has no docstring"
        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in doc]
        assert not missing, f"run_skill_retry docstring missing tools: {missing}"

    def test_load_skill_script_docstring_names_all_forbidden_tools(self):
        """load_skill_script docstring must name every forbidden tool."""
        from autoskillit.server import load_skill_script

        doc = load_skill_script.__doc__
        assert doc, "load_skill_script has no docstring"
        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in doc]
        assert not missing, f"load_skill_script docstring missing tools: {missing}"


class TestBundledWorkflowContract:
    """Bundled workflow YAML files must name all forbidden tools in constraints."""

    def test_bundled_workflows_constraints_name_all_forbidden_tools(self):
        """All bundled workflow YAML files must name every forbidden tool in constraints."""
        from autoskillit.workflow_loader import list_workflows, load_workflow

        workflows = list_workflows(Path("/nonexistent"))
        bundled = [w for w in workflows.items if w.source.value == "builtin"]
        assert len(bundled) >= 4, f"Expected >= 4 bundled workflows, got {len(bundled)}"

        for wf_info in bundled:
            wf = load_workflow(wf_info.path)
            assert wf.constraints, f"{wf_info.name} has no constraints"
            all_text = " ".join(wf.constraints)
            missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in all_text]
            assert not missing, f"{wf_info.name} constraints missing tools: {missing}"
