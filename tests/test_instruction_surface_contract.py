"""Contract tests: every instruction surface must carry the pipeline tool restriction.

These tests verify that all surfaces where the orchestrator receives instructions
about native tool usage contain the full forbidden tool list with prohibition framing.
If any test fails, a drift has occurred and the corresponding surface needs updating.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from autoskillit.core.types import PIPELINE_FORBIDDEN_TOOLS


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
        skill_md = _project_root() / "src" / "autoskillit" / "skills" / "write-recipe" / "SKILL.md"
        return skill_md.read_text()

    def test_schema_table_includes_constraints(self):
        text = self._skill_md_text()
        assert "| `kitchen_rules`" in text, (
            "make-script-skill SKILL.md schema table must include a 'kitchen_rules' row"
        )

    def test_example_yaml_includes_constraints(self):
        text = self._skill_md_text()
        yaml_blocks = re.findall(r"```yaml\s*\n(.*?)```", text, re.DOTALL)
        has_constraints = any("kitchen_rules:" in block for block in yaml_blocks)
        assert has_constraints, (
            "make-script-skill SKILL.md must include .kitchen_rules:' in at least one "
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
    def _close_kitchen(self, tool_ctx):
        from autoskillit.pipeline.gate import GateState

        tool_ctx.gate = GateState(enabled=False)

    def test_open_kitchen_prompt_names_all_forbidden_tools(self):
        """open_kitchen prompt text must name every forbidden tool with prohibition framing."""
        from autoskillit.server import open_kitchen

        result = open_kitchen()
        content = result.messages[0].content
        text = content.text if hasattr(content, "text") else str(content)

        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in text]
        assert not missing, f"open_kitchen prompt missing tools: {missing}"

        has_framing = any(term in text for term in ("NEVER", "Do NOT", "MUST NOT"))
        assert has_framing, "open_kitchen prompt lacks prohibition framing (NEVER/Do NOT/MUST NOT)"

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

    def test_load_recipe_docstring_names_all_forbidden_tools(self):
        """load_recipe docstring must name every forbidden tool."""
        from autoskillit.server import load_recipe

        doc = load_recipe.__doc__
        assert doc, "load_skill_script has no docstring"
        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in doc]
        assert not missing, f"load_recipe docstring missing tools: {missing}"


class TestBundledWorkflowContract:
    """Bundled workflow YAML files must name all forbidden tools in constraints."""

    def test_bundled_workflows_constraints_name_all_forbidden_tools(self):
        """All bundled workflow YAML files must name every forbidden tool in constraints."""
        from autoskillit.recipe_io import (
            list_recipes as list_workflows,
        )
        from autoskillit.recipe_io import (
            load_recipe as load_workflow,
        )

        workflows = list_workflows(Path("/nonexistent"))
        bundled = [w for w in workflows.items if w.source.value == "builtin"]
        assert len(bundled) >= 4, f"Expected >= 4 bundled workflows, got {len(bundled)}"

        for wf_info in bundled:
            wf = load_workflow(wf_info.path)
            assert wf.kitchen_rules, f"{wf_info.name} has no constraints"
            all_text = " ".join(wf.kitchen_rules)
            missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in all_text]
            assert not missing, f"{wf_info.name} constraints missing tools: {missing}"


class TestSkillMdToolNameCurrency:
    """SKILL.md files must reference current tool names, not pre-rename identifiers."""

    def test_write_recipe_skill_references_kitchen_status(self):
        """write-recipe SKILL.md must use kitchen_status, not old autoskillit_status."""
        skill_md = Path(__file__).parent.parent / "src/autoskillit/skills/write-recipe/SKILL.md"
        content = skill_md.read_text()
        assert "autoskillit_status" not in content, (
            "write-recipe/SKILL.md still references 'autoskillit_status'. "
            "Update all occurrences to 'kitchen_status'."
        )


_AUTOSKILLIT_CALLABLE_RE = re.compile(r"\bautoskillit\.[a-z_]+\.[a-z_]+\b")


class TestRunPythonCallableContract:
    """Every autoskillit.module.function reference in any SKILL.md must be importable and callable.

    This mirrors the exact validation that _import_and_call() performs at runtime.
    If the test passes, agents can call the path; if it fails, the path is stale.
    """

    def _collect_callable_refs(self) -> list[tuple[str, str]]:
        """Returns list of (skill_name/SKILL.md relative path, dotted callable path)."""
        skills_dir = _project_root() / "src" / "autoskillit" / "skills"
        refs = []
        for skill_md in skills_dir.rglob("SKILL.md"):
            content = skill_md.read_text()
            skill_name = skill_md.parent.name
            for match in _AUTOSKILLIT_CALLABLE_RE.finditer(content):
                refs.append((f"{skill_name}/SKILL.md", match.group(0)))
        return refs

    def test_all_callable_refs_are_importable(self):
        """All autoskillit.* callable paths found in SKILL.md files must be importable."""
        failures = []
        for source, dotted in self._collect_callable_refs():
            module_path, _attr = dotted.rsplit(".", 1)
            try:
                importlib.import_module(module_path)
            except ImportError as exc:
                failures.append(f"{source}: {dotted!r} — ImportError: {exc}")
        assert not failures, "Stale module paths in SKILL.md files:\n" + "\n".join(failures)

    def test_all_callable_refs_have_valid_attribute(self):
        """All autoskillit.* callable paths in SKILL.md must resolve to an existing attribute."""
        failures = []
        for source, dotted in self._collect_callable_refs():
            module_path, attr_name = dotted.rsplit(".", 1)
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                continue  # caught by test_all_callable_refs_are_importable
            if not hasattr(module, attr_name):
                failures.append(
                    f"{source}: {dotted!r} — no attribute {attr_name!r} on {module_path!r}"
                )
        assert not failures, "Stale attribute references in SKILL.md files:\n" + "\n".join(
            failures
        )

    def test_all_callable_refs_are_callable(self):
        """All autoskillit.* attribute references in SKILL.md must be callable."""
        failures = []
        for source, dotted in self._collect_callable_refs():
            module_path, attr_name = dotted.rsplit(".", 1)
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                continue
            attr = getattr(module, attr_name, None)
            if attr is not None and not callable(attr):
                failures.append(f"{source}: {dotted!r} — attribute exists but is not callable")
        assert not failures, "Non-callable references in SKILL.md files:\n" + "\n".join(failures)

    def test_contract_validator_not_referenced_in_skill_mds(self):
        """autoskillit.contract_validator was deleted; no SKILL.md may reference it."""
        skills_dir = _project_root() / "src" / "autoskillit" / "skills"
        stale_refs = []
        for skill_md in skills_dir.rglob("SKILL.md"):
            content = skill_md.read_text()
            if "autoskillit.contract_validator" in content:
                stale_refs.append(str(skill_md.relative_to(_project_root())))
        assert not stale_refs, (
            "Deleted module 'autoskillit.contract_validator' still referenced:\n"
            + "\n".join(stale_refs)
        )
