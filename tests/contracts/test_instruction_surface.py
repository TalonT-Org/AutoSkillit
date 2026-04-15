"""Contract tests: every instruction surface must carry the pipeline tool restriction.

These tests verify that all surfaces where the orchestrator receives instructions
about native tool usage contain the full forbidden tool list with prohibition framing.
If any test fails, a drift has occurred and the corresponding surface needs updating.
"""

from __future__ import annotations

import importlib
import re
import types
from pathlib import Path

import pytest

from autoskillit.cli._mcp_names import DIRECT_PREFIX, MARKETPLACE_PREFIX
from autoskillit.core.types import PIPELINE_FORBIDDEN_TOOLS


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


class TestWriteRecipeSkillContract:
    """write-recipe SKILL.md must document the constraints field."""

    def _skill_md_text(self) -> str:
        skill_md = (
            _project_root()
            / "src"
            / "autoskillit"
            / "skills_extended"
            / "write-recipe"
            / "SKILL.md"
        )
        return skill_md.read_text()

    def test_schema_table_includes_constraints(self):
        text = self._skill_md_text()
        assert "| `kitchen_rules`" in text, (
            "write-recipe SKILL.md schema table must include a 'kitchen_rules' row"
        )

    def test_example_yaml_includes_constraints(self):
        text = self._skill_md_text()
        yaml_blocks = re.findall(r"```yaml\s*\n(.*?)```", text, re.DOTALL)
        has_constraints = any("kitchen_rules:" in block for block in yaml_blocks)
        assert has_constraints, (
            "write-recipe SKILL.md must include .kitchen_rules:' in at least one "
            "example YAML block"
        )

    def test_skill_md_names_forbidden_tools(self):
        text = self._skill_md_text()
        found = [t for t in PIPELINE_FORBIDDEN_TOOLS if t in text]
        assert len(found) >= 3, (
            f"write-recipe SKILL.md must name at least 3 forbidden tools, found only: {found}"
        )


class TestServerToolSurfaceContract:
    """Server tool docstrings and prompts must name all forbidden tools."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, minimal_ctx, monkeypatch):
        from autoskillit.pipeline.gate import DefaultGateState
        from autoskillit.server import _state

        monkeypatch.setattr(minimal_ctx, "gate", DefaultGateState(enabled=False))
        monkeypatch.setattr(_state, "_ctx", minimal_ctx)

    @pytest.mark.anyio
    async def test_open_kitchen_prompt_names_all_forbidden_tools(self):
        """open_kitchen tool text must name every forbidden tool with prohibition framing."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from autoskillit.server.tools_kitchen import open_kitchen

        mock_ctx = MagicMock()
        mock_ctx.enable_components = AsyncMock()
        with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
            with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                text = await open_kitchen(ctx=mock_ctx)

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
            Path(__file__).parent.parent.parent
            / "src/autoskillit/skills_extended/write-recipe/SKILL.md"
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
        src_root = _project_root() / "src" / "autoskillit"
        refs = []
        for skill_root in (src_root / "skills", src_root / "skills_extended"):
            for skill_md in skill_root.rglob("SKILL.md"):
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
            if attr is not None and not callable(attr) and not isinstance(attr, types.ModuleType):
                failures.append(f"{source}: {dotted!r} — attribute exists but is not callable")
        assert not failures, "Non-callable references in SKILL.md files:\n" + "\n".join(failures)

    def test_contract_validator_not_referenced_in_skill_mds(self):
        """autoskillit.contract_validator was deleted; no SKILL.md may reference it."""
        src_root = _project_root() / "src" / "autoskillit"
        stale_refs = []
        for skill_dir in (src_root / "skills", src_root / "skills_extended"):
            for skill_md in skill_dir.rglob("SKILL.md"):
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
        skills_dir = _project_root() / "src" / "autoskillit" / "skills_extended"
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


class TestOrchestratorPromptDelegation:
    """Orchestrator prompt must delegate recipe display to load_recipe."""

    def test_orchestrator_prompt_does_not_embed_recipe_data(self):
        """The orchestrator prompt must delegate recipe loading to open_kitchen(name).

        This is an architectural invariant: the CLI-to-session bridge injects
        behavioral instructions only. Recipe content is discovered by the
        session via MCP tools.
        """
        from autoskillit.cli._prompts import _build_orchestrator_prompt

        prompt = _build_orchestrator_prompt("implementation", mcp_prefix=DIRECT_PREFIX)
        # Must NOT contain recipe YAML markers
        assert "--- RECIPE ---" not in prompt
        assert "--- END RECIPE ---" not in prompt
        # Must instruct open_kitchen call with recipe name
        assert "open_kitchen" in prompt


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

    def test_git_mutating_recipes_have_clone_step(self):
        """Recipes using MCP git-mutation tools must use clone_repo."""
        from autoskillit.recipe.io import list_recipes, load_recipe

        GIT_MUTATION_TOOLS = {"create_unique_branch", "push_to_remote"}
        workflows = list_recipes(Path("/nonexistent"))
        bundled = [w for w in workflows.items if w.source.value == "builtin"]
        for wf_info in bundled:
            wf = load_recipe(wf_info.path)
            uses_mutation_tool = any(step.tool in GIT_MUTATION_TOOLS for step in wf.steps.values())
            if not uses_mutation_tool:
                continue
            has_clone = any(
                step.tool == "clone_repo" or (step.python and "clone_repo" in step.python)
                for step in wf.steps.values()
            )
            assert has_clone, (
                f"{wf_info.name} uses MCP git-mutation tools "
                f"({GIT_MUTATION_TOOLS & {s.tool for s in wf.steps.values()}}) "
                f"but never calls clone_repo — workspace isolation is missing."
            )


class TestSousChefMergePhaseContract:
    """sous-chef/SKILL.md must carry a MERGE PHASE mandatory section."""

    def _sous_chef_text(self) -> str:
        return (
            _project_root() / "src" / "autoskillit" / "skills" / "sous-chef" / "SKILL.md"
        ).read_text()

    def test_sous_chef_has_merge_phase_section(self):
        text = self._sous_chef_text()
        assert "MERGE PHASE" in text, (
            "sous-chef/SKILL.md must contain a 'MERGE PHASE' mandatory section"
        )

    def test_sous_chef_prohibits_parallel_gh_pr_merge(self):
        text = self._sous_chef_text()
        merge_ref_idx = text.find("gh pr merge")
        assert merge_ref_idx != -1, (
            "sous-chef/SKILL.md must explicitly name 'gh pr merge' in the prohibition"
        )
        context = text[max(0, merge_ref_idx - 300) : merge_ref_idx + 300]
        has_prohibition = any(
            phrase in context
            for phrase in (
                "NEVER allow",
                "NEVER call",
                "NEVER use",
                "must not",
                "prohibited",
            )
        )
        assert has_prohibition, (
            "sous-chef/SKILL.md must contain a prohibition on parallel gh pr merge calls "
            "with the prohibition phrase within 300 chars of 'gh pr merge'"
        )

    def test_sous_chef_requires_sequential_merge_without_queue(self):
        text = self._sous_chef_text()
        assert "sequential" in text.lower(), (
            "sous-chef/SKILL.md must require sequential merging when no merge queue exists"
        )

    def test_sous_chef_names_detection_command(self):
        text = self._sous_chef_text()
        # Must reference the GraphQL detection approach
        assert "mergeQueue" in text or "merge_queue" in text.lower(), (
            "sous-chef/SKILL.md must specify the merge-queue detection command"
        )

    def test_sous_chef_routes_conflict_to_on_failure(self):
        text = self._sous_chef_text()
        # Must say conflicts route to on_failure, not run_cmd git
        assert "on_failure" in text, (
            "sous-chef/SKILL.md must state that merge conflicts route to on_failure"
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
        skills_root = _project_root() / "src" / "autoskillit" / "skills_extended"
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


class TestResolveFailuresDirtyTreeContract:
    """resolve-failures SKILL.md must document dirty tree pre-check."""

    def test_resolve_failures_mentions_dirty_tree(self):
        skill_md = (
            _project_root()
            / "src"
            / "autoskillit"
            / "skills_extended"
            / "resolve-failures"
            / "SKILL.md"
        )
        content = skill_md.read_text()
        assert "dirty" in content.lower() or "uncommitted" in content.lower(), (
            "resolve-failures SKILL.md must document the dirty tree pre-check step"
        )


class TestResolveFailuresCITruthContract:
    """resolve-failures SKILL.md must document the CI-truth rule."""

    def test_resolve_failures_rejects_local_pass_when_ci_failing(self):
        skill_md = _project_root() / "src/autoskillit/skills_extended/resolve-failures/SKILL.md"
        content = skill_md.read_text().lower()
        # Must mention that CI is the source of truth and local pass != resolution
        assert "ci is the source of truth" in content, (
            "resolve-failures SKILL.md must state CI is the source of truth"
        )

    def test_resolve_failures_documents_flaky_test_investigation(self):
        skill_md = _project_root() / "src/autoskillit/skills_extended/resolve-failures/SKILL.md"
        content = skill_md.read_text().lower()
        # Must describe what to do when tests pass locally but CI failed
        assert "passes locally" in content or "flak" in content, (
            "resolve-failures SKILL.md must document the flaky test / "
            "local-pass != resolution case"
        )

    def test_resolve_failures_parses_ci_context_args(self):
        skill_md = _project_root() / "src/autoskillit/skills_extended/resolve-failures/SKILL.md"
        content = skill_md.read_text()
        # Must document parsing of ci_conclusion and diagnosis_path
        assert (
            "ci_conclusion" in content or "ci_failed" in content or "diagnosis_path" in content
        ), (
            "resolve-failures SKILL.md must document parsing of CI context arguments "
            "passed by the resolve_ci recipe step"
        )


class TestContextLimitBehaviorContract:
    """File-writing skills in pipeline recipes must document Context Limit Behavior."""

    _SKILLS_ROOT = (
        Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit" / "skills_extended"
    )

    def test_resolve_failures_has_context_limit_section(self):
        """resolve-failures SKILL.md must contain '## Context Limit Behavior'."""
        skill_md = self._SKILLS_ROOT / "resolve-failures" / "SKILL.md"
        content = skill_md.read_text()
        assert "## Context Limit Behavior" in content, (
            "resolve-failures/SKILL.md must contain a '## Context Limit Behavior' section. "
            "This skill commits during execution; context exhaustion can leave edits "
            "uncommitted on disk. The section must instruct the skill to verify tree "
            "cleanliness before emitting structured output."
        )

    def test_pipeline_file_writing_skills_have_context_limit_section(self):
        """Every write_behavior=always skill used in a step with on_context_limit must
        document Context Limit Behavior in its SKILL.md.

        Checks all bundled recipe steps: if a step has on_context_limit AND invokes a
        skill with write_behavior=always, that skill's SKILL.md must contain the section.
        """
        from autoskillit.core import SKILL_TOOLS
        from autoskillit.recipe.contracts import load_bundled_manifest, resolve_skill_name
        from autoskillit.recipe.io import builtin_recipes_dir, load_recipe

        manifest = load_bundled_manifest()
        assert manifest is not None, "load_bundled_manifest() returned None"
        skills = manifest.get("skills", {})

        missing: list[str] = []
        for yaml_path in sorted(builtin_recipes_dir().glob("*.yaml")):
            recipe = load_recipe(yaml_path)
            for _step_name, step in recipe.steps.items():
                if step.tool not in SKILL_TOOLS:
                    continue
                if step.on_context_limit is None:
                    continue
                skill_cmd = step.with_args.get("skill_command", "")
                skill = resolve_skill_name(skill_cmd)
                if not skill:
                    continue
                skill_data = skills.get(skill, {})
                if skill_data.get("write_behavior") != "always":
                    continue
                skill_md_path = self._SKILLS_ROOT / skill / "SKILL.md"
                if not skill_md_path.exists():
                    continue
                content = skill_md_path.read_text()
                if "## Context Limit Behavior" not in content:
                    missing.append(f"{skill} (used in {yaml_path.name})")

        assert not missing, (
            "These write_behavior=always skills lack a '## Context Limit Behavior' section "
            f"in their SKILL.md: {', '.join(sorted(set(missing)))}"
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


@pytest.mark.parametrize("mcp_prefix", [DIRECT_PREFIX, MARKETPLACE_PREFIX])
def test_orchestrator_tool_name_matches_open_kitchen_hook_matcher(mcp_prefix: str) -> None:
    """The fully-qualified tool name in the prompt must satisfy the hook registry matcher."""
    from autoskillit.hook_registry import HOOK_REGISTRY

    open_kitchen_matchers = [h.matcher for h in HOOK_REGISTRY if "open_kitchen" in h.matcher]
    assert open_kitchen_matchers, "Expected at least one open_kitchen hook matcher"
    qualified_name = f"{mcp_prefix}open_kitchen"
    for matcher in open_kitchen_matchers:
        assert re.search(matcher, qualified_name), (
            f"Prompt tool name '{qualified_name}' does not match hook matcher '{matcher}'"
        )
