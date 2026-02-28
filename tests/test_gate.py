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

    assert len(GATED_TOOLS) == 16  # +3 for clone_repo/remove_clone/push_to_remote, +1 for fetch_github_issue
    assert len(UNGATED_TOOLS) == 6


def test_gated_tools_contains_expected_names():
    from autoskillit.pipeline.gate import GATED_TOOLS

    expected = {
        "run_cmd",
        "run_python",
        "read_db",
        "run_skill",
        "run_skill_retry",
        "test_check",
        "merge_worktree",
        "reset_test_dir",
        "classify_fix",
        "reset_workspace",
        "migrate_recipe",
        "check_quota",
        "clone_repo",
        "remove_clone",
        "push_to_remote",
        "fetch_github_issue",
    }
    assert GATED_TOOLS == expected


def test_check_quota_in_gated_tools():
    from autoskillit.pipeline.gate import GATED_TOOLS

    assert "check_quota" in GATED_TOOLS


def test_gated_and_ungated_are_disjoint_after_addition():
    from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS

    assert GATED_TOOLS.isdisjoint(UNGATED_TOOLS)


def test_ungated_tools_contains_expected_names():
    from autoskillit.pipeline.gate import UNGATED_TOOLS

    expected = {
        "kitchen_status",
        "get_pipeline_report",
        "get_token_summary",
        "list_recipes",
        "load_recipe",
        "validate_recipe",
    }
    assert UNGATED_TOOLS == expected


def test_gate_state_default_disabled():
    from autoskillit.pipeline.gate import DefaultGateState

    gs = DefaultGateState()
    assert gs.enabled is False


def test_gate_state_can_be_enabled():
    from autoskillit.pipeline.gate import DefaultGateState

    gs = DefaultGateState(enabled=True)
    assert gs.enabled is True


def test_gate_error_result_is_valid_json():
    import json

    from autoskillit.pipeline.gate import gate_error_result

    raw = gate_error_result()
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)


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


def test_gate_has_no_internal_imports():
    """_gate.py must have zero autoskillit internal imports (L0 constraint)."""
    import ast
    from pathlib import Path

    src = (
        Path(__file__).parent.parent / "src" / "autoskillit" / "pipeline" / "gate.py"
    ).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "autoskillit" not in node.module, (
                    f"_gate.py must not import from autoskillit (L0 constraint): "
                    f"found 'from {node.module} import ...'"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "autoskillit" not in alias.name, (
                        "_gate.py must not import autoskillit (L0 constraint)"
                    )
