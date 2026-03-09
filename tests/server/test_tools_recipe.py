"""Tests for autoskillit server recipe tools."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason
from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS, DefaultGateState
from autoskillit.server.tools_recipe import (
    list_recipes,
    load_recipe,
    migrate_recipe,
    validate_recipe,
)


def _extract_docstring_sections(desc: str) -> dict[str, str]:
    """Split a tool description into named sections by detecting headers.

    Returns a dict of {section_name: section_text} with lowercase-normalized keys.
    The first paragraph before any header is the ``preamble`` section.

    Detected header patterns:
    - ALL-CAPS headers with colon or em-dash (ROUTING RULES —, IMPORTANT:)
    - Capitalized phrase followed by colon (After loading:, Args:)
    - "During pipeline execution" specific header
    - "NEVER use native" prohibition header
    """
    lines = desc.split("\n")
    header_patterns = [
        # ALL-CAPS header: ROUTING RULES —, FAILURE PREDICATES —, IMPORTANT:
        re.compile(r"^([A-Z]{2,}(?:\s+[A-Z]{2,})*\s*[—:])"),
        # Capitalized phrase + colon: After loading:, Allowed during ...:, Args:
        re.compile(r"^([A-Z][a-z]+(?:\s+[a-z]+)*\s*:)"),
        # Specific: "During pipeline execution" or "NEVER use native"
        re.compile(r"^(During pipeline execution[,:]?)"),
        re.compile(r"^(NEVER use native)"),
    ]

    sections: dict[str, str] = {}
    current_key = "preamble"
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        matched_header = None

        for pattern in header_patterns:
            m = pattern.match(stripped)
            if m:
                matched_header = m.group(1)
                break

        if matched_header:
            if current_lines:
                sections[current_key] = "\n".join(current_lines).strip()
            key = matched_header.lower().rstrip(":—,").strip()
            current_key = key
            current_lines = [stripped]
        else:
            current_lines.append(line)

    if current_lines:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


# ---------------------------------------------------------------------------
# Minimal valid script YAML used across migration suggestion tests
# ---------------------------------------------------------------------------

_MINIMAL_SCRIPT_YAML = """\
name: test-script
description: Test
summary: test
ingredients:
  task:
    description: What to do
    required: true
steps:
  do-thing:
    tool: run_skill
    with:
      skill_command: "/autoskillit:investigate ${{ inputs.task }}"
      cwd: "."
    on_success: done
    on_failure: escalate
  done:
    action: stop
    message: "Done."
  escalate:
    action: stop
    message: "Failed."
kitchen_rules:
  - "Follow routing rules"
"""


def _write_minimal_script(scripts_dir: Path, name: str = "test-script") -> Path:
    """Write a minimal valid workflow script with no autoskillit_version field."""
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = scripts_dir / f"{name}.yaml"
    path.write_text(_MINIMAL_SCRIPT_YAML)
    return path


class TestRecipeTools:
    """Tests for ungated list_recipes and load_recipe tools."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        """Verify these tools work WITHOUT tool activation."""
        tool_ctx.gate = DefaultGateState(enabled=False)

    # SS1
    @pytest.mark.anyio
    @patch("autoskillit.recipe._api.list_recipes")
    async def test_list_returns_json_object(self, mock_list):
        """list_recipes returns JSON object with scripts array (not gated)."""
        from autoskillit.core.types import LoadResult, RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        mock_list.return_value = LoadResult(
            items=[
                RecipeInfo(
                    name="impl",
                    description="Implement",
                    summary="plan > impl",
                    path=Path("/x"),
                    source=RecipeSource.PROJECT,
                ),
            ],
            errors=[],
        )
        result = json.loads(await list_recipes())
        assert isinstance(result, dict)
        assert len(result["recipes"]) == 1
        assert result["recipes"][0]["name"] == "impl"
        assert result["recipes"][0]["description"] == "Implement"
        assert result["recipes"][0]["summary"] == "plan > impl"
        assert "errors" not in result

    # SS2
    @pytest.mark.anyio
    async def test_load_returns_json_with_content(self, tmp_path, monkeypatch):
        """load_recipe returns JSON with content and suggestions (not gated)."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text("name: test\ndescription: Test recipe\n")
        result = json.loads(await load_recipe(name="test"))
        assert "content" in result
        assert "suggestions" in result
        assert "name: test" in result["content"]
        assert "description: Test recipe" in result["content"]

    # SS3
    @pytest.mark.anyio
    async def test_load_unknown_returns_error(self, tmp_path, monkeypatch):
        """load_recipe returns error JSON for unknown recipe name."""
        monkeypatch.chdir(tmp_path)
        result = json.loads(await load_recipe(name="nonexistent"))
        assert "error" in result
        assert "nonexistent" in result["error"]

    # SS4
    @pytest.mark.anyio
    @patch("autoskillit.recipe._api.list_recipes")
    async def test_list_reports_errors_in_response(self, mock_list):
        """list_recipes includes errors in JSON when recipes fail to parse."""
        from autoskillit.core.types import LoadReport, LoadResult

        mock_list.return_value = LoadResult(
            items=[],
            errors=[LoadReport(path=Path("/recipes/broken.yaml"), error="bad yaml")],
        )
        result = json.loads(await list_recipes())
        assert "errors" in result
        assert len(result["errors"]) == 1
        assert result["errors"][0]["file"] == "broken.yaml"
        assert "bad yaml" in result["errors"][0]["error"]

    # SS5
    @pytest.mark.anyio
    async def test_list_integration_discovers_project_recipe(self, tmp_path, monkeypatch):
        """Server tool returns project recipes alongside bundled recipes."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "pipeline.yaml").write_text(
            "name: test-pipe\ndescription: Test\nsummary: a > b\n"
            "steps:\n  done:\n    action: stop\n    message: Done\n"
        )
        result = json.loads(await list_recipes())
        names = {r["name"] for r in result["recipes"]}
        assert "test-pipe" in names

    # SS6
    @pytest.mark.anyio
    async def test_list_integration_reports_errors(self, tmp_path, monkeypatch):
        """Server tool reports parse errors to the caller from real files."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "broken.yaml").write_text("[unclosed bracket\n")
        result = json.loads(await list_recipes())
        assert "errors" in result
        assert len(result["errors"]) == 1

    # SS7
    @pytest.mark.anyio
    async def test_load_returns_json_with_suggestions(self, tmp_path, monkeypatch):
        """load_recipe response always has 'content' and 'suggestions' keys."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nkitchen_rules:\n  - test\n"
            "steps:\n  do:\n    tool: test_check\n    model: sonnet\n"
            "    on_success: done\n  done:\n    action: stop\n    message: Done\n"
        )
        result = json.loads(await load_recipe(name="test"))
        assert "content" in result
        assert "suggestions" in result
        assert isinstance(result["suggestions"], list)
        assert any(s["rule"] == "model-on-non-skill-step" for s in result["suggestions"])

    # SS8
    @pytest.mark.anyio
    async def test_list_recipes_includes_builtins_with_empty_project_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list_recipes MCP returns bundled recipes when .autoskillit/recipes/ is absent."""
        monkeypatch.chdir(tmp_path)
        # No .autoskillit/recipes/ created — simulates a fresh project with no local recipes
        result = json.loads(await list_recipes())
        names = {r["name"] for r in result["recipes"]}
        assert "implementation" in names
        assert "bugfix-loop" in names
        assert "audit-and-fix" in names
        assert "remediation" in names
        assert "smoke-test" in names

    # SS9
    @pytest.mark.anyio
    async def test_load_recipe_mcp_returns_builtin_recipe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_recipe MCP finds bundled recipes when no project .autoskillit/recipes/ dir."""
        monkeypatch.chdir(tmp_path)
        result = json.loads(await load_recipe(name="implementation"))
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert "content" in result
        assert len(result["content"]) > 0

    @pytest.mark.anyio
    async def test_load_recipe_parse_failure_is_logged_and_surfaced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_recipe emits a warning log and surfaces a validation-error finding."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        # Recipe must have 'steps' so the run_semantic_rules code path is reached
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )

        with (
            patch(
                "autoskillit.recipe._api.run_semantic_rules",
                side_effect=ValueError("injected crash"),
            ),
            patch("autoskillit.recipe._api._logger") as mock_logger,
        ):
            result = json.loads(await load_recipe(name="test"))

        assert "content" in result, "load_recipe must be non-blocking even on parse failure"
        mock_logger.warning.assert_called_once()
        assert any(s.get("rule") == "validation-error" for s in result["suggestions"]), (
            "Unexpected exception must appear as a validation-error finding in suggestions"
        )
        findings = [s for s in result["suggestions"] if s.get("rule") == "validation-error"]
        assert findings, "Expected at least one validation-error finding"
        assert findings[0]["message"] == "Invalid recipe structure: injected crash"


class TestContractMigrationAdapterValidate:
    """P7-2: ContractMigrationAdapter.validate uses _load_yaml, not yaml.safe_load."""

    def test_valid_contract_returns_true(self, tmp_path: Path) -> None:
        from autoskillit.migration.engine import ContractMigrationAdapter

        f = tmp_path / "contract.yaml"
        f.write_text("skill_hashes:\n  my-skill: abc123\n")
        adapter = ContractMigrationAdapter()
        ok, msg = adapter.validate(f)
        assert ok is True
        assert msg == ""

    def test_missing_skill_hashes_returns_false(self, tmp_path: Path) -> None:
        from autoskillit.migration.engine import ContractMigrationAdapter

        f = tmp_path / "contract.yaml"
        f.write_text("other_field: value\n")
        adapter = ContractMigrationAdapter()
        ok, msg = adapter.validate(f)
        assert ok is False
        assert "skill_hashes" in msg

    def test_invalid_yaml_returns_false(self, tmp_path: Path) -> None:
        from autoskillit.migration.engine import ContractMigrationAdapter

        f = tmp_path / "contract.yaml"
        f.write_bytes(b":\tbad: yaml: [unclosed\n")
        adapter = ContractMigrationAdapter()
        ok, msg = adapter.validate(f)
        assert ok is False
        assert msg != ""

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        from autoskillit.migration.engine import ContractMigrationAdapter

        adapter = ContractMigrationAdapter()
        ok, msg = adapter.validate(tmp_path / "nonexistent.yaml")
        assert ok is False
        assert msg != ""


class TestLoadRecipeExceptionHandling:
    """CC-1: Outer except in load_recipe must catch anticipated exceptions only."""

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx):
        """Initialize ToolContext so load_recipe can call _get_config()."""

    @pytest.mark.anyio
    async def test_yaml_error_surfaces_as_suggestion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """yaml.YAMLError is caught and returned as an error suggestion."""
        from autoskillit.core.io import YAMLError

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text("name: test\n")
        with patch("autoskillit.recipe._api.load_yaml", side_effect=YAMLError("bad yaml")):
            result = json.loads(await load_recipe(name="test"))
        assert "error" not in result
        assert any(
            s.get("rule") == "validation-error" and s.get("severity") == "error"
            for s in result["suggestions"]
        )

    @pytest.mark.anyio
    async def test_value_error_surfaces_as_suggestion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ValueError (malformed recipe structure) is caught and returned as error suggestion."""
        from autoskillit.core.types import RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        recipe_path = recipes_dir / "test.yaml"
        recipe_path.write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        fake_match = RecipeInfo(
            name="test",
            description="Test",
            source=RecipeSource.PROJECT,
            path=recipe_path,
        )
        with (
            patch("autoskillit.recipe.find_recipe_by_name", return_value=fake_match),
            patch(
                "autoskillit.recipe._api._parse_recipe", side_effect=ValueError("bad structure")
            ),
        ):
            result = json.loads(await load_recipe(name="test"))
        assert "error" not in result
        assert any(
            s.get("rule") == "validation-error" and s.get("severity") == "error"
            for s in result["suggestions"]
        )

    @pytest.mark.anyio
    async def test_file_not_found_surfaces_as_suggestion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FileNotFoundError is caught and returned as an error suggestion."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        with patch(
            "autoskillit.recipe._api.load_recipe_card",
            side_effect=FileNotFoundError("missing"),
        ):
            result = json.loads(await load_recipe(name="test"))
        assert "error" not in result
        assert any(
            s.get("rule") == "validation-error" and s.get("severity") == "error"
            for s in result["suggestions"]
        )

    @pytest.mark.anyio
    async def test_unexpected_exception_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unexpected exceptions (not in specific catches) must propagate, not be swallowed."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        with patch(
            "autoskillit.recipe._api.run_semantic_rules",
            side_effect=AttributeError("programming error"),
        ):
            with pytest.raises(AttributeError, match="programming error"):
                await load_recipe(name="test")


class TestValidateRecipeTool:
    """Tests for ungated validate_recipe tool."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        """Verify this tool works WITHOUT tool activation."""
        tool_ctx.gate = DefaultGateState(enabled=False)

    # VS1
    @pytest.mark.anyio
    async def test_valid_recipe_returns_success(self, tmp_path):
        """validate_recipe returns valid=true for a correct recipe."""
        script = tmp_path / "good.yaml"
        script.write_text(
            "name: test\n"
            "description: A test recipe\n"
            "summary: a > b\n"
            "kitchen_rules:\n"
            "  - test\n"
            "steps:\n"
            "  do_thing:\n"
            "    tool: run_cmd\n"
            "    with:\n"
            "      cmd: echo hello\n"
            "      cwd: .\n"
            "    on_success: done\n"
            "  done:\n"
            "    action: stop\n"
            '    message: "Done."\n'
        )
        result = json.loads(await validate_recipe(script_path=str(script)))
        assert result["valid"] is True
        assert result["errors"] == []

    # VS2
    @pytest.mark.anyio
    async def test_invalid_recipe_returns_errors(self, tmp_path):
        """validate_recipe returns valid=false with errors for missing name."""
        script = tmp_path / "bad.yaml"
        script.write_text("description: Missing name\nsteps:\n  do_thing:\n    tool: run_cmd\n")
        result = json.loads(await validate_recipe(script_path=str(script)))
        assert result["valid"] is False
        assert any("name" in e for e in result["errors"])

    # VS3
    @pytest.mark.anyio
    async def test_nonexistent_file_returns_error(self):
        """validate_recipe returns valid=False with findings for nonexistent file."""
        result = json.loads(await validate_recipe(script_path="/nonexistent/path.yaml"))
        assert result["valid"] is False
        assert len(result["findings"]) > 0
        assert "not found" in result["findings"][0]["error"].lower()

    # VS4
    @pytest.mark.anyio
    async def test_malformed_yaml_returns_error(self, tmp_path):
        """validate_recipe returns valid=False with findings for unparseable YAML."""
        script = tmp_path / "broken.yaml"
        script.write_text("key: [\n  unclosed\n")
        result = json.loads(await validate_recipe(script_path=str(script)))
        assert result["valid"] is False
        assert len(result["findings"]) > 0
        assert "yaml" in result["findings"][0]["error"].lower()

    # T_OR10
    @pytest.mark.anyio
    async def test_validate_recipe_with_on_result(self, tmp_path):
        """validate_recipe correctly validates on_result blocks."""
        script = tmp_path / "good.yaml"
        script.write_text(
            "name: result-recipe\n"
            "description: Uses on_result\n"
            "kitchen_rules:\n"
            "  - test\n"
            "steps:\n"
            "  classify:\n"
            "    tool: classify_fix\n"
            "    on_result:\n"
            "      field: restart_scope\n"
            "      routes:\n"
            "        full_restart: done\n"
            "        partial_restart: done\n"
            "    on_failure: done\n"
            "  done:\n"
            "    action: stop\n"
            '    message: "Done."\n'
        )
        result = json.loads(await validate_recipe(script_path=str(script)))
        assert result["valid"] is True

    # DFQ14
    @pytest.mark.anyio
    async def test_validate_recipe_includes_quality_field(self, tmp_path):
        """validate_recipe response includes quality report with warnings and summary."""
        script = tmp_path / "dead.yaml"
        script.write_text(
            "name: dead-output-test\n"
            "description: Has a dead capture\n"
            "kitchen_rules:\n"
            "  - test\n"
            "steps:\n"
            "  impl:\n"
            "    tool: run_skill\n"
            "    capture:\n"
            "      worktree_path: '${{ result.worktree_path }}'\n"
            "    on_success: done\n"
            "  done:\n"
            "    action: stop\n"
            '    message: "Done."\n'
        )
        result = json.loads(await validate_recipe(script_path=str(script)))
        assert result["valid"] is False
        assert "quality" in result
        quality = result["quality"]
        assert "warnings" in quality
        assert "summary" in quality
        dead = [w for w in quality["warnings"] if w["code"] == "DEAD_OUTPUT"]
        assert len(dead) == 1
        assert dead[0]["step"] == "impl"
        assert dead[0]["field"] == "worktree_path"
        semantic_errors = [
            f
            for f in result.get("findings", [])
            if f.get("rule") == "dead-output" and f.get("severity") == "error"
        ]
        assert len(semantic_errors) == 1
        assert semantic_errors[0]["step"] == "impl"

    # SEM1
    @pytest.mark.anyio
    async def test_validate_recipe_includes_semantic_findings(self, tmp_path):
        """validate_recipe response includes 'findings' key with semantic findings."""
        script = tmp_path / "semantic.yaml"
        script.write_text(
            "name: semantic-test\n"
            "description: Has model on non-skill step\n"
            "kitchen_rules:\n"
            "  - test\n"
            "steps:\n"
            "  check:\n"
            "    tool: test_check\n"
            "    model: sonnet\n"
            "    on_success: done\n"
            "  done:\n"
            "    action: stop\n"
            '    message: "Done."\n'
        )
        result = json.loads(await validate_recipe(script_path=str(script)))
        assert "findings" in result
        assert isinstance(result["findings"], list)
        assert any(f["rule"] == "model-on-non-skill-step" for f in result["findings"])
        assert result["valid"] is True  # Warning does not block validity


class TestDocstringSemantics:
    """Section-aware semantic checks for tool descriptions.

    Unlike TestToolSchemas (which checks token presence), these tests parse
    descriptions into named sections and verify behavioral correctness,
    routing, and cross-section consistency.
    """

    def test_load_recipe_action_protocol_routes_through_skill(self):
        """After loading section must route modifications through write-recipe."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["load_recipe"].description or ""
        sections = _extract_docstring_sections(desc)

        after_loading = sections.get("after loading", "")
        assert after_loading, "load_recipe missing 'After loading' section"

        # Modification requests must route through write-recipe
        assert "write-recipe" in after_loading, (
            "After loading section must route recipe modifications through write-recipe"
        )

    def test_load_recipe_after_loading_does_not_instruct_direct_modification(self):
        """After loading section must not instruct direct file modification."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["load_recipe"].description or ""
        sections = _extract_docstring_sections(desc)

        after_loading = sections.get("after loading", "")
        assert after_loading, "load_recipe missing 'After loading' section"

        direct_edit_phrases = [
            "apply them",
            "Save changes to the original file",
            "Save as a new recipe",
        ]
        found = [p for p in direct_edit_phrases if p.lower() in after_loading.lower()]
        assert not found, f"After loading section instructs direct modification: {found}"

    def test_validate_recipe_has_failure_routing(self):
        """validate_recipe must route validation failures to write-recipe."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["validate_recipe"].description or ""

        # Must reference the failure return case
        assert "false" in desc.lower(), (
            "validate_recipe must document the failure case (e.g. {valid: false})"
        )

        # Failure routing must direct to write-recipe for remediation
        desc_lower = desc.lower()
        has_remediation_context = any(
            phrase in desc_lower for phrase in ["fix", "remediat", "correct the"]
        )
        assert has_remediation_context, (
            "validate_recipe must route failures to write-recipe for remediation"
        )

    def test_validate_recipe_does_not_endorse_direct_editing(self):
        """validate_recipe must not normalize direct recipe editing."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["validate_recipe"].description or ""

        # "or editing a recipe" without qualifying through write-recipe
        # normalizes the model directly editing YAML files
        assert "or editing a recipe" not in desc, (
            "validate_recipe normalizes direct editing with 'or editing a recipe'; "
            "should qualify as going through write-recipe"
        )

    def test_tool_description_sections_are_not_contradictory(self):
        """After loading must not instruct what the prohibition section prohibits."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["load_recipe"].description or ""
        sections = _extract_docstring_sections(desc)

        after_loading = sections.get("after loading", "")
        # Accept either old or new section header
        prohibition = sections.get("during pipeline execution", "") or sections.get(
            "never use native", ""
        )
        assert after_loading, "Missing 'After loading' section"
        assert prohibition, (
            "Missing prohibition section (NEVER use native / During pipeline execution)"
        )

        # If the prohibition section says Edit/Write are prohibited or "not used here",
        # then "After loading" must not instruct behaviors requiring file writing
        if "not used here" in prohibition.lower() or "prohibited" in prohibition.lower():
            write_implying_phrases = ["apply them", "save changes", "save as"]
            found = [p for p in write_implying_phrases if p.lower() in after_loading.lower()]
            assert not found, (
                f"Contradiction: prohibition section prohibits Edit/Write "
                f"but 'After loading' instructs: {found}"
            )

    def test_load_recipe_has_preview_format_spec(self):
        """load_recipe must specify presentation format for loaded recipes."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["load_recipe"].description or ""

        required_fields = ["kitchen_rules", "note", "retry", "capture"]
        found = [f for f in required_fields if f in desc.lower()]
        assert len(found) >= 3, (
            f"load_recipe must specify a preview format naming critical recipe "
            f"fields. Found only: {found}"
        )

    def test_recipe_tool_descriptions_are_coherent(self):
        """Recipe tools must form a coherent policy about recipe modification."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp as server

        tools = {
            c.name: c for c in server._local_provider._components.values() if isinstance(c, Tool)
        }

        failures = []

        # load_recipe: modifications must route through write-recipe
        load_desc = tools["load_recipe"].description or ""
        load_sections = _extract_docstring_sections(load_desc)
        after_loading = load_sections.get("after loading", "")
        if "apply them" in after_loading.lower():
            failures.append("load_recipe 'After loading' instructs direct editing ('apply them')")

        # validate_recipe: failure must route through write-recipe
        validate_desc = tools["validate_recipe"].description or ""
        validate_lower = validate_desc.lower()
        has_failure_routing = (
            "write-recipe" in validate_desc
            and any(w in validate_lower for w in ["fix", "fail", "invalid", "error"])
            and "false" in validate_lower
        )
        if not has_failure_routing:
            failures.append("validate_recipe has no failure routing through write-recipe")

        # validate_recipe: must not normalize direct editing
        if "or editing a recipe" in validate_desc:
            failures.append("validate_recipe normalizes direct editing ('or editing a recipe')")

        assert not failures, "Recipe tools lack coherent modification policy:\n" + "\n".join(
            f"  - {f}" for f in failures
        )


class TestLoadSkillScriptFailurePredicates:
    """The load_recipe tool description documents failure predicates."""

    def test_description_documents_run_skill_failure(self):
        """The routing rules must define failure for run_skill, not just test_check."""
        from fastmcp.tools import Tool

        from autoskillit.server import mcp

        tools = {
            c.name: c for c in mcp._local_provider._components.values() if isinstance(c, Tool)
        }
        desc = tools["load_recipe"].description or ""
        assert "run_skill" in desc
        assert "success" in desc.lower()


class TestMigrationSuggestions:
    """MSUG2: validate_recipe surfaces migration warnings."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        """Verify these tools work WITHOUT tool activation."""
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)

    # MSUG2
    @pytest.mark.anyio
    async def test_validate_always_includes_outdated_version(self, tmp_path):
        """MSUG2: validate_recipe always includes outdated-script-version in semantic results."""
        script = tmp_path / "test-script.yaml"
        script.write_text(_MINIMAL_SCRIPT_YAML)

        result = json.loads(await validate_recipe(script_path=str(script)))
        assert "findings" in result
        rules = [s["rule"] for s in result["findings"]]
        assert "outdated-recipe-version" in rules


class TestMigrationSuppression:
    """SUP1, SUP4: load_recipe respects migration.suppressed config."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        """Verify these tools work WITHOUT tool activation."""
        from autoskillit.pipeline.gate import DefaultGateState

        tool_ctx.gate = DefaultGateState(enabled=False)

    # SUP1
    @pytest.mark.anyio
    async def test_outdated_version_not_in_suggestions_when_suppressed(
        self, tmp_path, monkeypatch, tool_ctx
    ):
        """SUP1: outdated-recipe-version absent when recipe is suppressed; headless not called."""
        from autoskillit.config import MigrationConfig

        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        _write_minimal_script(scripts_dir, "test-script")

        tool_ctx.config = AutomationConfig(migration=MigrationConfig(suppressed=["test-script"]))

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with patch("autoskillit.execution.headless.run_headless_core", mock_headless):
            result = json.loads(await load_recipe(name="test-script"))

        assert "suggestions" in result
        rules = [s["rule"] for s in result["suggestions"]]
        assert "outdated-recipe-version" not in rules
        mock_headless.assert_not_called()

    # SUP4
    @pytest.mark.anyio
    async def test_validate_always_includes_outdated_version_regardless_of_suppression(
        self, tmp_path, tool_ctx
    ):
        """SUP4: validate_recipe includes outdated-script-version even when suppressed."""
        from autoskillit.config import MigrationConfig

        script = tmp_path / "test-script.yaml"
        script.write_text(_MINIMAL_SCRIPT_YAML)

        # Even with script suppressed in config, validate_recipe does not filter
        tool_ctx.config = AutomationConfig(migration=MigrationConfig(suppressed=["test-script"]))

        result = json.loads(await validate_recipe(script_path=str(script)))
        assert "findings" in result
        rules = [s["rule"] for s in result["findings"]]
        assert "outdated-recipe-version" in rules


class TestApplyTriageGate:
    """T3: _apply_triage_gate caches triage result and skips on second call."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        tool_ctx.gate = DefaultGateState(enabled=False)

    @pytest.mark.anyio
    async def test_apply_triage_gate_second_call_skips_triage(
        self, tmp_path, monkeypatch, tool_ctx
    ):
        """Second _apply_triage_gate call reads from cache; triage_staleness not re-invoked."""
        import copy

        from autoskillit.recipe.staleness_cache import read_staleness_cache
        from autoskillit.server.helpers import _apply_triage_gate

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        recipe_yaml = (
            "name: triage-test\ndescription: T\n"
            "steps:\n  done:\n    action: stop\n    message: Done\n"
        )
        recipe_path = recipes_dir / "triage-test.yaml"
        recipe_path.write_text(recipe_yaml)

        name = "triage-test"
        result_template = {
            "content": recipe_yaml,
            "suggestions": [
                {
                    "rule": "stale-contract",
                    "reason": "hash_mismatch",
                    "skill": "investigate",
                    "stored_value": "sha256:old",
                    "current_value": "sha256:new",
                    "message": "investigate SKILL.md changed",
                    "severity": "info",
                }
            ],
            "valid": True,
        }

        # Get real recipe_info before mocking find
        recipe_info = tool_ctx.recipes.find(name, Path.cwd())
        assert recipe_info is not None

        # Mock _ctx.recipes.find to verify it is NOT called when recipe_info is injected
        mock_find = AsyncMock(return_value=recipe_info)
        monkeypatch.setattr(tool_ctx.recipes, "find", mock_find)

        mock_triage = AsyncMock(
            return_value=[{"meaningful": False, "summary": "ok", "skill": "investigate"}]
        )
        with patch("autoskillit._llm_triage.triage_staleness", mock_triage):
            # First call: triage_staleness invoked once
            await _apply_triage_gate(copy.deepcopy(result_template), name, recipe_info=recipe_info)

        assert mock_triage.call_count == 1
        assert mock_find.call_count == 0, "find() must not be called when recipe_info is injected"

        cache_path = tmp_path / ".autoskillit" / "temp" / "recipe_staleness_cache.json"
        cached = read_staleness_cache(cache_path, name)
        assert cached is not None
        assert cached.triage_result == "cosmetic"

        with patch("autoskillit._llm_triage.triage_staleness", mock_triage):
            # Second call: must read from cache and skip triage_staleness entirely
            await _apply_triage_gate(copy.deepcopy(result_template), name, recipe_info=recipe_info)

        assert mock_triage.call_count == 1, (
            "triage_staleness must not be called on second invocation"
        )


class TestLoadRecipeReadOnly:
    """P4: load_recipe is strictly read-only — no migration, no contract card generation."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        """load_recipe works WITHOUT tool activation."""
        tool_ctx.gate = DefaultGateState(enabled=False)

    @pytest.mark.anyio
    async def test_load_recipe_does_not_call_migration_engine(self, tmp_path, monkeypatch):
        """load_recipe must not trigger headless migration even when migrations are applicable."""
        monkeypatch.chdir(tmp_path)
        with (
            patch("autoskillit.migration.loader.applicable_migrations", return_value=["v0.1.0"]),
            patch("autoskillit.execution.headless.run_headless_core") as mock_headless,
            patch("autoskillit.recipe.contracts.generate_recipe_card") as mock_gen,
        ):
            result = json.loads(await load_recipe(name="implementation"))
        assert "error" not in result
        mock_headless.assert_not_called()
        mock_gen.assert_not_called()

    @pytest.mark.anyio
    async def test_load_recipe_does_not_auto_generate_contract_card(self, tmp_path, monkeypatch):
        """load_recipe must not call generate_recipe_card even when no card exists."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        with patch("autoskillit.recipe.contracts.generate_recipe_card") as mock_gen:
            await load_recipe(name="test")
        mock_gen.assert_not_called()


class TestMigrateRecipe:
    """P4: migrate_recipe is a gated tool that runs migration engine and regenerates cards."""

    @pytest.fixture(autouse=True)
    def _open_kitchen(self, tool_ctx):
        """migrate_recipe requires tool activation."""
        tool_ctx.gate = DefaultGateState(enabled=True)

    def _setup_migration_env(
        self,
        tmp_path,
        monkeypatch,
        tool_ctx,
        *,
        suppressed: list[str] | None = None,
    ):
        """Create directory structure, fake migration YAML, and config."""
        import autoskillit
        import autoskillit.migration.loader as ml
        from autoskillit.config import MigrationConfig

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        recipe_path = recipes_dir / "test-script.yaml"
        recipe_path.write_text(_MINIMAL_SCRIPT_YAML)

        installed_ver = autoskillit.__version__
        fake_mig_dir = tmp_path / "migrations"
        fake_mig_dir.mkdir()
        migration_yaml = (
            f"from_version: '0.0.0'\n"
            f"to_version: '{installed_ver}'\n"
            "description: Upgrade scripts\n"
            "changes:\n"
            "  - id: add-summary-field\n"
            "    description: Scripts now require a summary field\n"
            "    instruction: Add summary field to your script\n"
        )
        (fake_mig_dir / "0.0.0-migration.yaml").write_text(migration_yaml)
        monkeypatch.setattr(ml, "_migrations_dir", lambda: fake_mig_dir)

        tool_ctx.config = AutomationConfig(migration=MigrationConfig(suppressed=suppressed or []))

        temp_mig_dir = tmp_path / ".autoskillit" / "temp" / "migrations"
        temp_mig_dir.mkdir(parents=True)

        migrated_content = _MINIMAL_SCRIPT_YAML + f"autoskillit_version: '{installed_ver}'\n"
        return {
            "recipe_path": recipe_path,
            "temp_mig_dir": temp_mig_dir,
            "migrated_content": migrated_content,
            "installed_ver": installed_ver,
        }

    def test_migrate_recipe_is_in_gated_tools(self):
        """migrate_recipe is a gated tool."""
        assert "migrate_recipe" in GATED_TOOLS

    def test_migrate_recipe_not_in_ungated_tools(self):
        """migrate_recipe is not an ungated tool."""
        assert "migrate_recipe" not in UNGATED_TOOLS

    @pytest.mark.anyio
    async def test_migrate_recipe_requires_gate(self, tool_ctx):
        """migrate_recipe returns gate_error when kitchen is closed."""
        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await migrate_recipe(name="test"))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_migrate_recipe_not_found(self, tmp_path, monkeypatch):
        """migrate_recipe returns error for unknown recipe name."""
        monkeypatch.chdir(tmp_path)
        result = json.loads(await migrate_recipe(name="nonexistent"))
        assert "error" in result
        assert "nonexistent" in result["error"]

    @pytest.mark.anyio
    async def test_migrate_recipe_up_to_date(self, tmp_path, monkeypatch):  # SRV-UPD-1
        """migrate_recipe returns up_to_date when no migrations applicable and contract fresh."""
        monkeypatch.chdir(tmp_path)
        with (
            patch("autoskillit.migration.loader.applicable_migrations", return_value=[]),
            patch("autoskillit.recipe.load_recipe_card", return_value={"skill_hashes": {}}),
            patch("autoskillit.recipe.check_contract_staleness", return_value=[]),
        ):
            result = json.loads(await migrate_recipe(name="implementation"))
        assert result.get("status") == "up_to_date"

    # LR1
    @pytest.mark.anyio
    async def test_auto_migrates_outdated_recipe(self, tmp_path, monkeypatch, tool_ctx):
        """LR1: When recipe version < installed, _run_headless_core is called once."""
        ctx = self._setup_migration_env(tmp_path, monkeypatch, tool_ctx)
        (ctx["temp_mig_dir"] / "test-script.yaml").write_text(ctx["migrated_content"])

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with (
            patch("autoskillit.execution.headless.run_headless_core", mock_headless),
            patch("autoskillit.recipe.generate_recipe_card", return_value=None),
        ):
            result = json.loads(await migrate_recipe(name="test-script"))

        mock_headless.assert_awaited_once()
        assert result.get("status") == "migrated"
        assert "contracts_regenerated" in result

    # LR4
    @pytest.mark.anyio
    async def test_clears_failure_record_after_successful_migration(
        self, tmp_path, monkeypatch, tool_ctx
    ):
        """LR4: FailureStore.clear(name) is called when migration succeeds."""
        from autoskillit.migration.store import FailureStore, default_store_path

        ctx = self._setup_migration_env(tmp_path, monkeypatch, tool_ctx)
        (ctx["temp_mig_dir"] / "test-script.yaml").write_text(ctx["migrated_content"])

        store = FailureStore(default_store_path(tmp_path))
        store.record(
            name="test-script",
            file_path=ctx["recipe_path"],
            file_type="recipe",
            error="prior failure",
            retries_attempted=1,
        )
        assert store.has_failure("test-script")

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with (
            patch("autoskillit.execution.headless.run_headless_core", mock_headless),
            patch("autoskillit.recipe.contracts.generate_recipe_card", return_value=None),
        ):
            await migrate_recipe(name="test-script")

        fresh_store = FailureStore(default_store_path(tmp_path))
        assert not fresh_store.has_failure("test-script")

    # LR5
    @pytest.mark.anyio
    async def test_records_failure_when_migration_fails(self, tmp_path, monkeypatch, tool_ctx):
        """LR5: When headless returns success=False, failure is recorded to failures.json."""
        from autoskillit.migration.store import FailureStore, default_store_path

        self._setup_migration_env(tmp_path, monkeypatch, tool_ctx)

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=False,
                result="headless failed",
                session_id="",
                subtype="error",
                is_error=True,
                exit_code=1,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with patch("autoskillit.execution.headless.run_headless_core", mock_headless):
            result = json.loads(await migrate_recipe(name="test-script"))

        assert "error" in result
        store = FailureStore(default_store_path(tmp_path))
        assert store.has_failure("test-script")

    # LR7
    @pytest.mark.anyio
    async def test_suppressed_recipe_not_migrated(self, tmp_path, monkeypatch, tool_ctx):
        """LR7: When name in migration.suppressed, headless is never called."""
        self._setup_migration_env(tmp_path, monkeypatch, tool_ctx, suppressed=["test-script"])

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with patch("autoskillit.execution.headless.run_headless_core", mock_headless):
            result = json.loads(await migrate_recipe(name="test-script"))

        mock_headless.assert_not_called()
        assert result.get("status") == "up_to_date"

    # LR8
    @pytest.mark.anyio
    async def test_up_to_date_recipe_not_migrated(self, tmp_path, monkeypatch, tool_ctx):
        """LR8: When applicable_migrations returns [], headless is never called."""
        import autoskillit
        import autoskillit.migration.loader as ml
        from autoskillit.config import MigrationConfig

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        current_ver = autoskillit.__version__
        (recipes_dir / "test-script.yaml").write_text(
            _MINIMAL_SCRIPT_YAML + f"autoskillit_version: '{current_ver}'\n"
        )

        empty_mig_dir = tmp_path / "migrations"
        empty_mig_dir.mkdir()
        monkeypatch.setattr(ml, "_migrations_dir", lambda: empty_mig_dir)
        tool_ctx.config = AutomationConfig(migration=MigrationConfig(suppressed=[]))

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with (
            patch("autoskillit.execution.headless.run_headless_core", mock_headless),
            patch("autoskillit.recipe.load_recipe_card", return_value={"skill_hashes": {}}),
            patch("autoskillit.recipe.check_contract_staleness", return_value=[]),
        ):
            result = json.loads(await migrate_recipe(name="test-script"))

        mock_headless.assert_not_called()
        assert result.get("status") == "up_to_date"

    # SRV-NEW-1
    @pytest.mark.anyio
    async def test_migrate_recipe_regenerates_stale_contract(
        self, tmp_path, monkeypatch, tool_ctx
    ):
        """migrate_recipe with version migration also regenerates stale contracts."""
        ctx = self._setup_migration_env(tmp_path, monkeypatch, tool_ctx)
        (ctx["temp_mig_dir"] / "test-script.yaml").write_text(ctx["migrated_content"])

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with (
            patch("autoskillit.execution.headless.run_headless_core", mock_headless),
            patch("autoskillit.recipe.load_recipe_card", return_value=None),
            patch("autoskillit.recipe.generate_recipe_card", return_value={}),
        ):
            result = json.loads(await migrate_recipe(name="test-script"))

        assert result.get("status") == "migrated"
        assert result.get("contracts_regenerated") == ["test-script"]


# ---------------------------------------------------------------------------
# Diagram field tests (DG-12 through DG-15)
# ---------------------------------------------------------------------------

_MINIMAL_RECIPE_FOR_DIAGRAM = """\
name: my-recipe
description: Test recipe for diagram tests
summary: step1 -> done
ingredients:
  task:
    description: What to do
    required: true
steps:
  step1:
    tool: run_skill
    with:
      skill_command: "/autoskillit:investigate ${{ inputs.task }}"
      cwd: "."
    on_success: done
    on_failure: escalate
  done:
    action: stop
    message: "Done."
  escalate:
    action: stop
    message: "Failed."
kitchen_rules:
  - "Use AutoSkillit tools only"
"""


class TestLoadRecipeDiagram:
    """Tests for diagram field in load_recipe responses (DG-12 through DG-15)."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        tool_ctx.gate = DefaultGateState(enabled=False)

    def _setup_project_recipe(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        recipe_path = recipes_dir / "my-recipe.yaml"
        recipe_path.write_text(_MINIMAL_RECIPE_FOR_DIAGRAM)
        return recipes_dir

    # DG-12
    @pytest.mark.anyio
    async def test_load_recipe_response_has_diagram_key(self, tmp_path, monkeypatch):
        """DG-12: load_recipe response always contains a 'diagram' key."""
        self._setup_project_recipe(tmp_path, monkeypatch)
        result = json.loads(await load_recipe(name="my-recipe"))
        assert "diagram" in result

    # DG-13
    @pytest.mark.anyio
    async def test_load_recipe_diagram_none_when_not_generated(self, tmp_path, monkeypatch):
        """DG-13: diagram is None when no diagram file exists."""
        self._setup_project_recipe(tmp_path, monkeypatch)
        result = json.loads(await load_recipe(name="my-recipe"))
        assert result["diagram"] is None

    # DG-14
    @pytest.mark.anyio
    async def test_load_recipe_diagram_content_when_exists(self, tmp_path, monkeypatch):
        """DG-14: diagram is non-None string when diagram file exists."""
        from autoskillit.recipe.diagrams import generate_recipe_diagram

        recipes_dir = self._setup_project_recipe(tmp_path, monkeypatch)
        recipe_path = recipes_dir / "my-recipe.yaml"
        generate_recipe_diagram(recipe_path, recipes_dir)

        result = json.loads(await load_recipe(name="my-recipe"))
        assert isinstance(result["diagram"], str)
        assert "<!-- autoskillit-recipe-hash:" in result["diagram"]

    # DG-15
    @pytest.mark.anyio
    async def test_load_recipe_stale_diagram_in_suggestions(self, tmp_path, monkeypatch):
        """DG-15: stale diagram appears in suggestions."""
        from autoskillit.recipe.diagrams import generate_recipe_diagram

        recipes_dir = self._setup_project_recipe(tmp_path, monkeypatch)
        recipe_path = recipes_dir / "my-recipe.yaml"
        # Generate diagram first, then mutate recipe to make it stale
        generate_recipe_diagram(recipe_path, recipes_dir)
        recipe_path.write_text(recipe_path.read_text() + "\n# modified\n")

        result = json.loads(await load_recipe(name="my-recipe"))
        rules = [s["rule"] for s in result["suggestions"]]
        assert "stale-diagram" in rules


# ---------------------------------------------------------------------------
# P5F2: Accessor pattern tests (ungated tools must use _get_ctx_or_none)
# ---------------------------------------------------------------------------


# P5F2-T1
@pytest.mark.anyio
async def test_list_recipes_no_ctx_returns_empty(monkeypatch):
    """list_recipes returns empty-list JSON when server is uninitialized."""
    import autoskillit.server._state as _state_mod

    monkeypatch.setattr(_state_mod, "_ctx", None)
    result = json.loads(await list_recipes())
    assert result == []


# P5F2-T2
@pytest.mark.anyio
async def test_load_recipe_no_ctx_returns_error(monkeypatch):
    """load_recipe returns error JSON when server is uninitialized."""
    import autoskillit.server._state as _state_mod

    monkeypatch.setattr(_state_mod, "_ctx", None)
    result = json.loads(await load_recipe(name="anything"))
    assert "error" in result


# P5F2-T3
@pytest.mark.anyio
async def test_validate_recipe_no_ctx_returns_error(monkeypatch, tmp_path):
    """validate_recipe returns invalid JSON when server is uninitialized."""
    import autoskillit.server._state as _state_mod

    monkeypatch.setattr(_state_mod, "_ctx", None)
    result = json.loads(await validate_recipe(script_path=str(tmp_path / "x.yaml")))
    assert result.get("valid") is False


# P5F2-T4  (import hygiene check)
def test_tools_recipe_does_not_import_raw_ctx():
    """tools_recipe.py must not import _ctx directly from server._state."""
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).parents[2] / "src/autoskillit/server/tools_recipe.py"
    ).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "_state" in node.module:
                names = [alias.name for alias in node.names]
                assert "_ctx" not in names, (
                    "tools_recipe.py must not import raw _ctx — use _get_ctx_or_none()"
                )
