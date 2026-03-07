# test_gate.py — unit tests for _gate.py constants and functions


def test_gated_tools_is_frozenset():
    from autoskillit.pipeline.gate import GATED_TOOLS

    assert isinstance(GATED_TOOLS, frozenset)


def test_ungated_tools_is_frozenset():
    from autoskillit.pipeline.gate import UNGATED_TOOLS

    assert isinstance(UNGATED_TOOLS, frozenset)


def test_tool_sets_are_disjoint():
    from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS

    assert GATED_TOOLS.isdisjoint(UNGATED_TOOLS)


def test_tool_sets_total_count():
    from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS

    assert len(GATED_TOOLS) == 18
    assert len(UNGATED_TOOLS) == 7


def test_gated_tools_contains_expected_names():
    from autoskillit.pipeline.gate import GATED_TOOLS

    expected = {
        "run_cmd",
        "run_python",
        "read_db",
        "run_skill",
        "test_check",
        "merge_worktree",
        "reset_test_dir",
        "classify_fix",
        "reset_workspace",
        "migrate_recipe",
        "clone_repo",
        "remove_clone",
        "push_to_remote",
        "report_bug",
        "prepare_issue",
        "enrich_issues",
        "claim_issue",
        "release_issue",
    }
    assert GATED_TOOLS == expected


def test_prepare_issue_in_gated_tools():
    from autoskillit.pipeline.gate import GATED_TOOLS

    assert "prepare_issue" in GATED_TOOLS


def test_check_quota_not_in_ungated_tools():
    """check_quota must not be in UNGATED_TOOLS.
    UNGATED_TOOLS contains tools agents legitimately call.
    check_quota enforcement is structural (hook), not agent-invoked."""
    from autoskillit.pipeline.gate import UNGATED_TOOLS

    assert "check_quota" not in UNGATED_TOOLS


def test_ungated_tools_contains_expected_names():
    from autoskillit.pipeline.gate import UNGATED_TOOLS

    expected = {
        "kitchen_status",
        "get_pipeline_report",
        "get_token_summary",
        "list_recipes",
        "load_recipe",
        "validate_recipe",
        "fetch_github_issue",
    }
    assert UNGATED_TOOLS == expected


def test_gate_state_enable_disable_transitions():
    from autoskillit.pipeline.gate import DefaultGateState

    gs = DefaultGateState()
    assert gs.enabled is False
    gs.enable()
    assert gs.enabled is True
    gs.disable()
    assert gs.enabled is False


def test_gate_error_result_fields():
    import json

    from autoskillit.pipeline.gate import gate_error_result

    parsed = json.loads(gate_error_result())
    assert parsed["success"] is False
    assert parsed["is_error"] is True
    assert parsed["subtype"] == "gate_error"
    assert parsed["exit_code"] == -1
    assert parsed["needs_retry"] is False
    assert parsed["retry_reason"] == "none"
    assert "open_kitchen" in parsed["result"]
    # Verify all standard response envelope fields are present:
    assert "session_id" in parsed
    assert "stderr" in parsed
    assert "token_usage" in parsed


def test_gate_error_result_accepts_custom_message():
    import json

    from autoskillit.pipeline.gate import gate_error_result

    parsed = json.loads(gate_error_result("Custom gate error text"))
    assert parsed["result"] == "Custom gate error text"
    assert parsed["success"] is False
    assert parsed["subtype"] == "gate_error"
    assert parsed["retry_reason"] == "none"
    assert parsed["is_error"] is True
    assert parsed["exit_code"] == -1
    assert parsed["needs_retry"] is False


def test_helpers_has_no_gate_error_result_duplicate():
    import autoskillit.server.helpers as helpers_mod

    assert not hasattr(helpers_mod, "_gate_error_result"), (
        "_gate_error_result must be removed from server.helpers — "
        "use gate_error_result() from pipeline.gate instead"
    )


def test_gate_imports_only_from_core():
    """gate.py (L1 pipeline) may only import from autoskillit.core (L0)."""
    import ast

    from autoskillit.core.paths import pkg_root

    src = (pkg_root() / "pipeline" / "gate.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module and "autoskillit" in node.module:
                assert node.module == "autoskillit.core" or node.module.startswith(
                    "autoskillit.core."
                ), (
                    f"gate.py (L1) may only import from autoskillit.core (L0): "
                    f"found 'from {node.module} import ...'"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "autoskillit" not in alias.name, (
                        "gate.py must not use bare autoskillit imports"
                    )
