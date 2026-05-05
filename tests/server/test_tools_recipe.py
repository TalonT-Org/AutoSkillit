"""Tests for autoskillit server validate_recipe tool and recipe docstring contracts."""

from __future__ import annotations

import json
import re

import pytest

from autoskillit.server.tools.tools_recipe import list_recipes as list_recipes_tool
from autoskillit.server.tools.tools_recipe import validate_recipe

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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
# Minimal valid script YAML used in migration suggestion tests
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


class TestValidateRecipeTool:
    """Tests for kitchen-gated validate_recipe tool."""

    @pytest.fixture(autouse=True)
    def _ensure_ctx(self, tool_ctx):
        """Ensure server context is initialized (gate open by default)."""

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


class TestMigrationSuggestions:
    """MSUG2: validate_recipe surfaces migration warnings."""

    @pytest.fixture(autouse=True)
    def _ensure_ctx(self, tool_ctx):
        """Ensure server context is initialized (gate open by default)."""

    # MSUG2
    @pytest.mark.anyio
    async def test_validate_always_includes_outdated_version(self, tmp_path):
        """MSUG2: validate_recipe always includes outdated-script-version in semantic results."""
        script = tmp_path / "test-script.yaml"
        script.write_text(_MINIMAL_SCRIPT_YAML + 'autoskillit_version: "0.0.1"\n')

        result = json.loads(await validate_recipe(script_path=str(script)))
        assert "findings" in result
        rules = [s["rule"] for s in result["findings"]]
        assert "outdated-recipe-version" in rules


class TestDocstringSemantics:
    """Section-aware semantic checks for tool descriptions.

    Unlike TestToolSchemas (which checks token presence), these tests parse
    descriptions into named sections and verify behavioral correctness,
    routing, and cross-section consistency.
    """

    async def _get_tools(self) -> dict:
        """Return a dict of tool_name -> tool for all visible tools including kitchen-gated."""
        from fastmcp.client import Client

        from autoskillit.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
        return {t.name: t for t in tools}

    @pytest.mark.anyio
    async def test_load_recipe_action_protocol_routes_through_skill(self, kitchen_enabled):
        """After loading section must route modifications through write-recipe."""
        tools = await self._get_tools()
        desc = tools["load_recipe"].description or ""
        sections = _extract_docstring_sections(desc)

        after_loading = sections.get("after loading", "")
        assert after_loading, "load_recipe missing 'After loading' section"

        # Modification requests must route through write-recipe
        assert "write-recipe" in after_loading, (
            "After loading section must route recipe modifications through write-recipe"
        )

    @pytest.mark.anyio
    async def test_load_recipe_after_loading_does_not_instruct_direct_modification(
        self, kitchen_enabled
    ):
        """After loading section must not instruct direct file modification."""
        tools = await self._get_tools()
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

    @pytest.mark.anyio
    async def test_validate_recipe_has_failure_routing(self, kitchen_enabled):
        """validate_recipe must route validation failures to write-recipe."""
        tools = await self._get_tools()
        desc = tools["validate_recipe"].description or ""

        assert "false" in desc.lower(), (
            "validate_recipe must document the failure case (e.g. {valid: false})"
        )

        desc_lower = desc.lower()
        has_remediation_context = any(
            phrase in desc_lower for phrase in ["fix", "remediat", "correct the"]
        )
        assert has_remediation_context, (
            "validate_recipe must route failures to write-recipe for remediation"
        )

    @pytest.mark.anyio
    async def test_validate_recipe_does_not_endorse_direct_editing(self, kitchen_enabled):
        """validate_recipe must not normalize direct recipe editing."""
        tools = await self._get_tools()
        desc = tools["validate_recipe"].description or ""

        assert "or editing a recipe" not in desc, (
            "validate_recipe normalizes direct editing with 'or editing a recipe'; "
            "should qualify as going through write-recipe"
        )

    @pytest.mark.anyio
    async def test_tool_description_sections_are_not_contradictory(self, kitchen_enabled):
        """After loading must not instruct what the prohibition section prohibits."""
        tools = await self._get_tools()
        desc = tools["load_recipe"].description or ""
        sections = _extract_docstring_sections(desc)

        after_loading = sections.get("after loading", "")
        prohibition = sections.get("during pipeline execution", "") or sections.get(
            "never use native", ""
        )
        assert after_loading, "Missing 'After loading' section"
        assert prohibition, (
            "Missing prohibition section (NEVER use native / During pipeline execution)"
        )

        if "not used here" in prohibition.lower() or "prohibited" in prohibition.lower():
            write_implying_phrases = ["apply them", "save changes", "save as"]
            found = [p for p in write_implying_phrases if p.lower() in after_loading.lower()]
            assert not found, (
                f"Contradiction: prohibition section prohibits Edit/Write "
                f"but 'After loading' instructs: {found}"
            )

    @pytest.mark.anyio
    async def test_load_recipe_has_preview_format_spec(self, kitchen_enabled):
        """load_recipe must specify presentation format for loaded recipes."""
        tools = await self._get_tools()
        desc = tools["load_recipe"].description or ""

        required_fields = ["kitchen_rules", "note", "retry", "capture"]
        found = [f for f in required_fields if f in desc.lower()]
        assert len(found) >= 3, (
            f"load_recipe must specify a preview format naming critical recipe "
            f"fields. Found only: {found}"
        )

    @pytest.mark.anyio
    async def test_recipe_tool_descriptions_are_coherent(self, kitchen_enabled):
        """Recipe tools must form a coherent policy about recipe modification."""
        tools = await self._get_tools()

        failures = []

        load_desc = tools["load_recipe"].description or ""
        load_sections = _extract_docstring_sections(load_desc)
        after_loading = load_sections.get("after loading", "")
        if "apply them" in after_loading.lower():
            failures.append("load_recipe 'After loading' instructs direct editing ('apply them')")

        validate_desc = tools["validate_recipe"].description or ""
        validate_lower = validate_desc.lower()
        has_failure_routing = (
            "write-recipe" in validate_desc
            and any(w in validate_lower for w in ["fix", "fail", "invalid", "error"])
            and "false" in validate_lower
        )
        if not has_failure_routing:
            failures.append("validate_recipe has no failure routing through write-recipe")

        if "or editing a recipe" in validate_desc:
            failures.append("validate_recipe normalizes direct editing ('or editing a recipe')")

        assert not failures, "Recipe tools lack coherent modification policy:\n" + "\n".join(
            f"  - {f}" for f in failures
        )


class TestLoadSkillScriptFailurePredicates:
    """The load_recipe tool description documents failure predicates."""

    async def _get_tools(self) -> dict:
        """Return dict of tool_name -> tool for all visible tools including kitchen-gated."""
        from fastmcp.client import Client

        from autoskillit.server import mcp

        async with Client(mcp) as client:
            tools = await client.list_tools()
        return {t.name: t for t in tools}

    @pytest.mark.anyio
    async def test_description_documents_run_skill_failure(self, kitchen_enabled):
        """The routing rules must define failure for run_skill, not just test_check."""
        tools = await self._get_tools()
        desc = tools["load_recipe"].description or ""
        assert "run_skill" in desc
        assert "success" in desc.lower()


# ---------------------------------------------------------------------------
# P5F2: Accessor pattern tests
# ---------------------------------------------------------------------------


# P5F2-T3
@pytest.mark.anyio
async def test_validate_recipe_no_recipes_returns_error(tool_ctx, tmp_path):
    """validate_recipe returns invalid JSON when recipes is not configured."""
    tool_ctx.recipes = None
    result = json.loads(await validate_recipe(script_path=str(tmp_path / "x.yaml")))
    assert result.get("valid") is False


# T7: list_recipes MCP tool hides campaign when fleet disabled
@pytest.mark.anyio
@pytest.mark.feature("fleet")
async def test_list_recipes_mcp_tool_hides_campaign_when_fleet_disabled(
    tool_ctx, tmp_path, monkeypatch
):
    """list_recipes MCP tool must exclude campaign recipes when fleet feature is disabled."""
    from pathlib import Path

    recipe_dir = tmp_path / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "my-campaign.yaml").write_text(
        "name: my-campaign\ndescription: test\nkind: campaign\nsteps: {}\n"
    )
    tool_ctx.config.features["fleet"] = False
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    raw = await list_recipes_tool()
    result = json.loads(raw)
    recipe_names = [r["name"] for r in result.get("recipes", [])]
    assert "my-campaign" not in recipe_names, (
        "Campaign recipe must not appear when fleet feature is disabled"
    )


@pytest.mark.anyio
async def test_list_recipes_returns_error_string_when_context_missing() -> None:
    """list_recipes must return an error message string (not []) when tool_ctx is None."""
    from autoskillit.server.tools.tools_recipe import list_recipes

    result = await list_recipes()
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert "error" in parsed or parsed.get("is_error") is True


# P5F2-T4  (import hygiene check)
def test_tools_recipe_does_not_import_raw_ctx():
    """tools_recipe.py must not import _ctx directly from server._state."""
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).parents[2] / "src/autoskillit/server/tools/tools_recipe.py"
    ).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "_state" in node.module:
                names = [alias.name for alias in node.names]
                assert "_ctx" not in names, (
                    "tools_recipe.py must not import raw _ctx — use _get_ctx_or_none()"
                )
