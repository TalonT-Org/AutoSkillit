from __future__ import annotations

from autoskillit.execution.pr_analysis import DOMAIN_PATHS, partition_files_by_domain


def test_server_files_assigned_to_server_domain():
    result = partition_files_by_domain(["src/autoskillit/server/tools_execution.py"])
    assert "Server/MCP Tools" in result
    assert "src/autoskillit/server/tools_execution.py" in result["Server/MCP Tools"]


def test_execution_file_assigned_to_pipeline_execution_domain():
    result = partition_files_by_domain(["src/autoskillit/execution/headless.py"])
    assert "Pipeline/Execution" in result
    assert "src/autoskillit/execution/headless.py" in result["Pipeline/Execution"]


def test_pipeline_file_assigned_to_pipeline_execution_domain():
    result = partition_files_by_domain(["src/autoskillit/pipeline/pr_gates.py"])
    assert "Pipeline/Execution" in result
    assert "src/autoskillit/pipeline/pr_gates.py" in result["Pipeline/Execution"]


def test_recipe_file_assigned_to_recipe_domain():
    result = partition_files_by_domain(["src/autoskillit/recipe/schema.py"])
    assert "Recipe/Validation" in result


def test_cli_file_assigned_to_cli_workspace_domain():
    result = partition_files_by_domain(["src/autoskillit/cli/app.py"])
    assert "CLI/Workspace" in result


def test_workspace_file_assigned_to_cli_workspace_domain():
    result = partition_files_by_domain(["src/autoskillit/workspace/skills.py"])
    assert "CLI/Workspace" in result


def test_skills_file_assigned_to_skills_domain():
    result = partition_files_by_domain(["src/autoskillit/skills_extended/open-pr/SKILL.md"])
    assert "Skills" in result


def test_test_file_assigned_to_tests_domain():
    result = partition_files_by_domain(["tests/test_something.py"])
    assert "Tests" in result


def test_core_file_assigned_to_core_config_infra():
    result = partition_files_by_domain(["src/autoskillit/core/types.py"])
    assert "Core/Config/Infra" in result


def test_config_file_assigned_to_core_config_infra():
    result = partition_files_by_domain(["src/autoskillit/config/settings.py"])
    assert "Core/Config/Infra" in result


def test_hooks_file_assigned_to_core_config_infra():
    result = partition_files_by_domain(["src/autoskillit/hooks/quota_check.py"])
    assert "Core/Config/Infra" in result


def test_unknown_file_goes_to_other():
    result = partition_files_by_domain(["pyproject.toml"])
    assert "Other" in result
    assert "pyproject.toml" in result["Other"]


def test_empty_list_returns_empty_dict():
    result = partition_files_by_domain([])
    assert result == {}


def test_result_only_includes_non_empty_domains():
    result = partition_files_by_domain(["src/autoskillit/server/tools_execution.py"])
    assert "Tests" not in result
    assert "Skills" not in result


def test_domain_paths_defines_at_least_seven_domains():
    assert len(DOMAIN_PATHS) >= 7


def test_mixed_files_across_multiple_domains():
    files = [
        "src/autoskillit/server/tools_execution.py",
        "src/autoskillit/execution/headless.py",
        "tests/test_something.py",
    ]
    result = partition_files_by_domain(files)
    assert "Server/MCP Tools" in result
    assert "Pipeline/Execution" in result
    assert "Tests" in result


def test_custom_domain_paths_override():
    custom = {"MyDomain": ["custom/path/"]}
    result = partition_files_by_domain(["custom/path/foo.py"], domain_paths=custom)
    assert "MyDomain" in result
