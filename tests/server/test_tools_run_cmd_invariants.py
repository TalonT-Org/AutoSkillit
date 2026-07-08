"""Server-side invariant tests for run_cmd: recipe-read prohibition and write-target boundary."""

from __future__ import annotations

import json

import pytest

from autoskillit.server._guards import RECIPE_READ_DENY_TRIGGER
from autoskillit.server.tools.tools_execution import run_cmd
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestRecipeReadProhibitionCmd:
    """run_cmd denies recipe/skill/agent file access in headless sessions."""

    @pytest.fixture(autouse=True)
    def _headless(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")

    @pytest.mark.anyio
    async def test_denies_recipe_yaml_path(self, tool_ctx_kitchen_open):
        result = json.loads(await run_cmd(cmd="cat .autoskillit/recipes/deploy.yaml", cwd="/tmp"))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"
        assert RECIPE_READ_DENY_TRIGGER in result["result"]

    @pytest.mark.anyio
    async def test_denies_src_recipe_yaml(self, tool_ctx_kitchen_open):
        result = json.loads(await run_cmd(cmd="head src/autoskillit/recipes/base.yml", cwd="/tmp"))
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_denies_skill_md_path(self, tool_ctx_kitchen_open):
        result = json.loads(
            await run_cmd(cmd="cat src/autoskillit/skills/open-kitchen/SKILL.md", cwd="/tmp")
        )
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_denies_skills_extended_skill_md(self, tool_ctx_kitchen_open):
        result = json.loads(
            await run_cmd(
                cmd="cat src/autoskillit/skills_extended/audit-arch/SKILL.md",
                cwd="/tmp",
            )
        )
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_denies_agent_md_path(self, tool_ctx_kitchen_open):
        result = json.loads(
            await run_cmd(cmd="cat src/autoskillit/agents/explorer.md", cwd="/tmp")
        )
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_allows_benign_cmd(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(0, "hello\n", ""))
        result = json.loads(await run_cmd(cmd="echo hello", cwd="/tmp"))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_allows_non_recipe_python_file(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        result = json.loads(await run_cmd(cmd="cat src/autoskillit/core/__init__.py", cwd="/tmp"))
        assert result["success"] is True

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "cmd",
        [
            "git add -- src/autoskillit/recipes/remediation.yaml",
            "git diff --stat -- src/autoskillit/recipes/remediation.yaml",
            "git diff --name-only -- src/autoskillit/recipes/remediation.yaml",
            "git status -- src/autoskillit/recipes/remediation.yaml",
            "wc -l src/autoskillit/recipes/remediation.yaml",
        ],
    )
    async def test_allows_recipe_path_vcs_and_metadata_commands(self, tool_ctx_kitchen_open, cmd):
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        result = json.loads(await run_cmd(cmd=cmd, cwd="/tmp"))
        assert result["success"] is True
        assert "subtype" not in result

    @pytest.mark.anyio
    async def test_allows_chain_of_safe_recipe_metadata_commands(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        result = json.loads(
            await run_cmd(
                cmd=(
                    "git add -- src/autoskillit/recipes/remediation.yaml "
                    "&& git status --short -- src/autoskillit/recipes/remediation.yaml"
                ),
                cwd="/tmp",
            )
        )
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_denies_chained_recipe_read_after_allowed_git_add(self, tool_ctx_kitchen_open):
        result = json.loads(
            await run_cmd(
                cmd=(
                    "git add -- src/autoskillit/recipes/remediation.yaml "
                    "&& cat src/autoskillit/recipes/remediation.yaml"
                ),
                cwd="/tmp",
            )
        )
        assert result["success"] is False
        assert result["subtype"] == "gate_error"
        assert RECIPE_READ_DENY_TRIGGER in result["result"]

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "cmd",
        [
            "git diff -- src/autoskillit/recipes/remediation.yaml",
            "git status -v -- src/autoskillit/recipes/remediation.yaml",
            "git add -p -- src/autoskillit/recipes/remediation.yaml",
            "git add --pathspec-from-file=src/autoskillit/recipes/remediation.yaml",
            "git add --pathspec-from-file src/autoskillit/recipes/remediation.yaml",
            "git diff --stat --patch-with-stat -- src/autoskillit/recipes/remediation.yaml",
            "git diff --stat --patch-with-raw -- src/autoskillit/recipes/remediation.yaml",
            "git diff --stat --binary -- src/autoskillit/recipes/remediation.yaml",
            "git diff --check -- src/autoskillit/recipes/remediation.yaml",
            ("python3 <<'PY'\nprint(open('src/autoskillit/recipes/remediation.yaml').read())\nPY"),
            (
                "git add -- src/autoskillit/recipes/remediation.yaml\n"
                "cat src/autoskillit/recipes/remediation.yaml"
            ),
            (
                "git add -- src/autoskillit/recipes/remediation.yaml "
                "& cat src/autoskillit/recipes/remediation.yaml"
            ),
            # Command substitution $(...) bypass — exercises the _SHELL_SUBSTITUTION_RE
            # branch in command_has_blocked_protected_path_read.
            "git add -- $(cat src/autoskillit/recipes/remediation.yaml)",
            # State-var-via-history $_ bypass — exercises the
            # _SHELL_STATE_VAR_RE branch gated on len(segments) > 1.
            "git add -- src/autoskillit/recipes/remediation.yaml && cat $_",
            (
                "git add -- src/autoskillit/recipes/remediation.yaml"
                "&&cat src/autoskillit/recipes/remediation.yaml"
            ),
        ],
    )
    async def test_denies_protected_path_content_bypasses(self, tool_ctx_kitchen_open, cmd):
        result = json.loads(await run_cmd(cmd=cmd, cwd="/tmp"))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"
        assert RECIPE_READ_DENY_TRIGGER in result["result"]

    @pytest.mark.anyio
    async def test_allows_in_non_headless(self, tool_ctx_kitchen_open, monkeypatch):
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        result = json.loads(await run_cmd(cmd="cat .autoskillit/recipes/deploy.yaml", cwd="/tmp"))
        assert result["success"] is True


class TestWriteTargetBoundaryCmd:
    """run_cmd denies writes outside allowed prefixes; fails open when unset."""

    @pytest.fixture(autouse=True)
    def _headless(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "orchestrator")

    @pytest.mark.anyio
    async def test_denies_write_outside_allowed_prefix(self, tool_ctx_kitchen_open, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", "/safe/dir")
        result = json.loads(await run_cmd(cmd="echo x > /forbidden/file.txt", cwd="/tmp"))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"
        assert "outside allowed write prefixes" in result["result"]

    @pytest.mark.anyio
    async def test_allows_write_inside_prefix(self, tool_ctx_kitchen_open, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", "/safe/dir")
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        result = json.loads(await run_cmd(cmd="echo x > /safe/dir/file.txt", cwd="/tmp"))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_allows_any_write_when_no_prefix(self, tool_ctx_kitchen_open):
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        result = json.loads(await run_cmd(cmd="echo x > /anywhere/file.txt", cwd="/tmp"))
        assert result["success"] is True

    @pytest.mark.anyio
    async def test_allows_read_only_cmd_with_prefix(self, tool_ctx_kitchen_open, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", "/safe/dir")
        tool_ctx_kitchen_open.runner.push(_make_result(0, "", ""))
        result = json.loads(await run_cmd(cmd="ls /forbidden/", cwd="/tmp"))
        assert result["success"] is True
