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

from autoskillit.config.settings import AutomationConfig
from autoskillit.core.types import PIPELINE_FORBIDDEN_TOOLS
from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS
from autoskillit.workspace.skills import SkillResolver


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


class TestClaudeMdPipelineContract:
    """CLAUDE.md must contain a pipeline execution section naming all forbidden tools."""

    def test_claude_md_has_pipeline_section(self):
        claude_md = (_project_root() / "CLAUDE.md").read_text()

        assert "Pipeline" in claude_md, "CLAUDE.md must have a Pipeline section header"

        match = re.search(
            r"###\s+\*?\*?\d+\.\d+[^#]*?Pipeline[^#]*?\*?\*?\s*\n(.*?)(?=\n##|\Z)",
            claude_md,
            re.DOTALL,
        )
        assert match, (
            "CLAUDE.md must have a Pipeline sub-section (e.g. '### **3.4. Pipeline Execution**')"
        )
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
        from autoskillit.pipeline.gate import DefaultGateState

        original_gate = tool_ctx.gate
        tool_ctx.gate = DefaultGateState(enabled=False)
        yield
        tool_ctx.gate = original_gate

    def test_open_kitchen_prompt_names_all_forbidden_tools(self):
        """open_kitchen prompt text must name every forbidden tool with prohibition framing."""
        from autoskillit.server.prompts import open_kitchen

        result = open_kitchen()
        content = result.messages[0].content
        text = content.text if hasattr(content, "text") else str(content)

        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in text]
        assert not missing, f"open_kitchen prompt missing tools: {missing}"

        has_framing = any(term in text for term in ("NEVER", "Do NOT", "MUST NOT"))
        assert has_framing, "open_kitchen prompt lacks prohibition framing (NEVER/Do NOT/MUST NOT)"

    def test_run_skill_docstring_names_all_forbidden_tools(self):
        """run_skill docstring must name every forbidden tool."""
        from autoskillit.server.tools_execution import run_skill

        doc = run_skill.__doc__
        assert doc, "run_skill has no docstring"
        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in doc]
        assert not missing, f"run_skill docstring missing tools: {missing}"

    def test_run_skill_retry_docstring_names_all_forbidden_tools(self):
        """run_skill_retry docstring must name every forbidden tool."""
        from autoskillit.server.tools_execution import run_skill_retry

        doc = run_skill_retry.__doc__
        assert doc, "run_skill_retry has no docstring"
        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in doc]
        assert not missing, f"run_skill_retry docstring missing tools: {missing}"

    def test_load_recipe_docstring_names_all_forbidden_tools(self):
        """load_recipe docstring must name every forbidden tool."""
        from autoskillit.server.tools_recipe import load_recipe

        doc = load_recipe.__doc__
        assert doc, "load_skill_script has no docstring"
        missing = [t for t in PIPELINE_FORBIDDEN_TOOLS if t not in doc]
        assert not missing, f"load_recipe docstring missing tools: {missing}"


class TestBundledWorkflowContract:
    """Bundled workflow YAML files must name all forbidden tools in constraints."""

    def test_bundled_workflows_constraints_name_all_forbidden_tools(self):
        """All bundled workflow YAML files must name every forbidden tool in constraints."""
        from autoskillit.recipe.io import (
            list_recipes as list_workflows,
        )
        from autoskillit.recipe.io import (
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
        skill_md = (
            Path(__file__).parent.parent.parent / "src/autoskillit/skills/write-recipe/SKILL.md"
        )
        content = skill_md.read_text()
        assert "autoskillit_status" not in content, (
            "write-recipe/SKILL.md still references 'autoskillit_status'. "
            "Update all occurrences to 'kitchen_status'."
        )


_AUTOSKILLIT_CALLABLE_RE = re.compile(r"\bautoskillit(?:\.[a-z_]+)+\b")


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


class TestMultiPartScopeContract:
    """Consuming skills must carry scope-fence instructions for multi-part plans."""

    def _skill_text(self, skill_name: str) -> str:
        skills_dir = _project_root() / "src" / "autoskillit" / "skills"
        return (skills_dir / skill_name / "SKILL.md").read_text()

    def test_dry_walkthrough_detects_part_suffix(self):
        text = self._skill_text("dry-walkthrough")
        assert "_part_" in text, "dry-walkthrough SKILL.md must contain '_part_' detection logic"

    def test_dry_walkthrough_scope_warning_verification(self):
        text = self._skill_text("dry-walkthrough")
        assert "scope warning block" in text.lower(), (
            "dry-walkthrough SKILL.md must describe verification of the scope warning block"
        )

    def test_dry_walkthrough_emits_terminal_notice(self):
        text = self._skill_text("dry-walkthrough")
        assert "PART" in text and "do not" in text.lower(), (
            "dry-walkthrough SKILL.md must instruct the agent to emit a scope-boundary "
            "terminal notice when a part suffix is detected"
        )

    def test_implement_worktree_scope_fence(self):
        text = self._skill_text("implement-worktree")
        assert "_part_" in text, (
            "implement-worktree SKILL.md must contain '_part_' detection logic"
        )
        assert "SCOPE FENCE" in text or "scope fence" in text.lower(), (
            "implement-worktree SKILL.md must contain a SCOPE FENCE instruction"
        )

    def test_implement_worktree_no_merge_scope_fence(self):
        text = self._skill_text("implement-worktree-no-merge")
        assert "_part_" in text, (
            "implement-worktree-no-merge SKILL.md must contain '_part_' detection logic"
        )
        assert "SCOPE FENCE" in text or "scope fence" in text.lower(), (
            "implement-worktree-no-merge SKILL.md must contain a SCOPE FENCE instruction"
        )


class TestClaudeMdConfigSurfaceContract:
    """CLAUDE.md developer guidance surfaces must match their code-level sources of truth.

    These tests ensure that when settings.py defaults, gate.py tool sets, or the
    bundled skill list changes, CLAUDE.md's documented values are kept in sync.
    A failing test here means CLAUDE.md has drifted from the code it describes.
    """

    def _claude_md(self) -> str:
        return (_project_root() / "CLAUDE.md").read_text()

    def test_header_total_tool_count_matches_gate(self):
        """CLAUDE.md Section 1 header must state the correct total MCP tool count."""
        total = len(GATED_TOOLS) + len(UNGATED_TOOLS)
        content = self._claude_md()
        assert f"{total} MCP tools" in content, (
            f"CLAUDE.md header says the wrong total MCP tool count. "
            f"gate.py defines {total} tools ({len(GATED_TOOLS)} gated + "
            f"{len(UNGATED_TOOLS)} ungated). Update the header in Section 1."
        )

    def test_header_gated_tool_count_matches_gate(self):
        """CLAUDE.md Section 1 header must state the correct gated tool count."""
        gated = len(GATED_TOOLS)
        content = self._claude_md()
        assert f"{gated} gated" in content, (
            f"CLAUDE.md header says the wrong gated tool count. "
            f"gate.py defines {gated} gated tools. "
            f"Update all occurrences of the gated count in CLAUDE.md."
        )

    def test_header_skill_count_matches_filesystem(self):
        """CLAUDE.md Section 1 header must state the correct bundled skill count."""
        count = len(SkillResolver().list_all())
        content = self._claude_md()
        assert f"{count} bundled skills" in content, (
            f"CLAUDE.md header says the wrong bundled skill count. "
            f"SkillResolver finds {count} skills on disk. "
            f"Update all occurrences of the skill count in CLAUDE.md."
        )

    def test_all_gated_tools_in_mcp_table(self):
        """CLAUDE.md must document all gated tools (backtick-wrapped) somewhere in its content."""
        content = self._claude_md()
        missing = [t for t in GATED_TOOLS if f"`{t}`" not in content]
        assert not missing, (
            f"CLAUDE.md is missing documentation for these gated tools: {missing}. "
            f"Add each tool name (backtick-wrapped) to CLAUDE.md."
        )

    def test_testing_guidelines_mentions_task_test_check(self):
        """CLAUDE.md Section 4 (Testing Guidelines) must document task test-check.

        task test-check is the automation/MCP command used by test_check and
        merge_worktree. Section 4 must document both commands and their roles
        rather than declaring task test-all as the sole command.
        """
        content = self._claude_md()
        section_start = content.find("## **4. Testing Guidelines**")
        assert section_start != -1, "CLAUDE.md must have a Section 4: Testing Guidelines"
        section_end = content.find("\n## **", section_start + 1)
        section = (
            content[section_start:section_end] if section_end != -1 else content[section_start:]
        )
        assert "task test-check" in section, (
            "CLAUDE.md Section 4 (Testing Guidelines) does not mention 'task test-check'. "
            "Update the 'Run tests' bullet to document both: "
            "task test-all (human-facing, includes lint) and "
            "task test-check (automation/MCP, unambiguous PASS/FAIL)."
        )

    def test_config_table_test_check_command_matches_settings(self):
        """CLAUDE.md testing guidelines must mention the current test_check.command default.

        This is a sync guard: when settings.py's default changes, this test
        will fail unless CLAUDE.md's testing guidelines are also updated.
        """
        default_cmd = AutomationConfig().test_check.command
        cmd_prose = " ".join(default_cmd)  # e.g. "task test-check"
        content = self._claude_md()
        assert cmd_prose in content, (
            f"CLAUDE.md does not contain test_check command {cmd_prose!r}. "
            f"Update the testing guidelines to reflect the current default ({default_cmd!r})."
        )


class TestQuotaGuardStructuralEnforcement:
    """Quota guard must be structurally enforced by the PreToolUse hook, not via docstring."""

    def test_load_recipe_has_no_quota_guard_instructions(self):
        """Quota guard enforcement is structural (hook), not instructional (docstring)."""
        from autoskillit.server.tools_recipe import load_recipe

        docstring = load_recipe.__doc__ or ""
        assert "QUOTA GUARD" not in docstring, (
            "QUOTA GUARD instructions must not appear in load_recipe docstring; "
            "quota enforcement is now handled by the PreToolUse hook."
        )


class TestSourceIsolationContract:
    """Every instruction surface that introduces the clone/source_dir pattern
    must carry an explicit SOURCE ISOLATION prohibition covering git checkout."""

    _SENTINEL = "SOURCE ISOLATION"

    def test_clone_using_recipes_have_source_isolation_rule(self):
        """All bundled clone-using recipes must have SOURCE ISOLATION in kitchen_rules."""
        from autoskillit.recipe.io import list_recipes, load_recipe

        workflows = list_recipes(Path("/nonexistent"))
        bundled = [w for w in workflows.items if w.source.value == "builtin"]
        clone_recipes = []
        for wf_info in bundled:
            raw = wf_info.path.read_text()
            if "clone_repo" not in raw:
                continue
            clone_recipes.append(wf_info.name)
            wf = load_recipe(wf_info.path)
            assert wf.kitchen_rules, f"{wf_info.name} has no kitchen_rules"
            all_rules = " ".join(wf.kitchen_rules)
            assert self._SENTINEL in all_rules, (
                f"{wf_info.name} uses clone_repo but kitchen_rules lack '{self._SENTINEL}'"
            )
            assert "checkout" in all_rules.lower(), (
                f"{wf_info.name} SOURCE ISOLATION rule must explicitly mention 'checkout'"
            )

        assert len(clone_recipes) >= 3, (
            f"Expected >=3 bundled clone-using recipes, found: {clone_recipes}"
        )

    def test_clone_repo_tool_docstring_has_source_isolation(self):
        """clone_repo MCP tool docstring must include SOURCE ISOLATION prohibition."""
        from autoskillit.server.tools_clone import clone_repo

        doc = clone_repo.__doc__ or ""
        assert self._SENTINEL in doc, "clone_repo docstring must contain 'SOURCE ISOLATION'"
        assert "checkout" in doc.lower(), (
            "clone_repo docstring must explicitly mention 'checkout' as prohibited"
        )

    def test_workspace_clone_module_has_source_isolation(self):
        """workspace/clone.py module docstring must carry the SOURCE ISOLATION note."""
        import autoskillit.workspace.clone as clone_mod

        doc = clone_mod.__doc__ or ""
        assert self._SENTINEL in doc, (
            "autoskillit.workspace.clone module docstring must contain 'SOURCE ISOLATION'"
        )


def test_open_pr_skill_does_not_contain_git_push():
    """The open-pr SKILL.md must not contain 'git push -u origin' as a workflow step.
    The recipe manages all push operations via push_to_remote. The skill is a pure
    PR creation operation."""
    import re

    from autoskillit.core.paths import pkg_root

    skill_path = pkg_root() / "skills" / "open-pr" / "SKILL.md"
    content = skill_path.read_text()
    # Match lines that start with a step number and contain 'git push -u origin'
    push_step_pattern = re.compile(r"^\s*\d+\.\s.*git push\s+-u origin", re.MULTILINE)
    matches = push_step_pattern.findall(content)
    assert not matches, (
        "open-pr SKILL.md must not contain 'git push -u origin' as a workflow step. "
        "The recipe's push_to_remote step manages publishing the branch."
    )


class TestPathArgSkillsContract:
    """Path-argument skills must document path-detection parsing in their SKILL.md."""

    PATH_ARG_SKILLS = [
        "implement-worktree-no-merge",
        "implement-worktree",
        "retry-worktree",
        "resolve-failures",
    ]
    SENTINEL = "path detection"

    def test_path_arg_skills_have_path_detection_instructions(self):
        skills_root = _project_root() / "src" / "autoskillit" / "skills"
        missing = []
        for skill_name in self.PATH_ARG_SKILLS:
            skill_md = skills_root / skill_name / "SKILL.md"
            content = skill_md.read_text().lower()
            if self.SENTINEL not in content:
                missing.append(skill_name)
        assert not missing, (
            f"These SKILL.md files lack path-detection instructions "
            f"(missing '{self.SENTINEL}'): {missing}"
        )


def test_claude_md_documents_all_source_modules() -> None:
    """Every .py file in src/autoskillit/ must appear by name in CLAUDE.md.

    For __init__.py files, the containing package directory name must appear.
    For all other files, the filename must appear somewhere in CLAUDE.md.
    """
    claude_path = Path(__file__).parent.parent.parent / "CLAUDE.md"
    content = claude_path.read_text()
    src_root = Path(__file__).parent.parent.parent / "src" / "autoskillit"

    missing = []
    for py_file in sorted(src_root.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        rel = py_file.relative_to(src_root)
        if py_file.name == "__init__.py":
            # For sub-package inits, verify the package directory is documented
            parent = rel.parent
            if parent != Path(".") and (parent.name + "/") not in content:
                missing.append(str(rel))
        else:
            if py_file.name not in content:
                missing.append(str(rel))

    assert not missing, (
        f"Modules not documented in CLAUDE.md: {', '.join(missing)}. "
        "Update the Architecture section in CLAUDE.md."
    )
