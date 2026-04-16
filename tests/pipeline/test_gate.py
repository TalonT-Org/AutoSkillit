# test_gate.py — unit tests for _gate.py constants and functions

import pytest

pytestmark = [pytest.mark.layer("pipeline")]


def test_gated_tools_contains_expected_names():
    from autoskillit.pipeline.gate import GATED_TOOLS

    expected = {
        "run_cmd",
        "run_python",
        "read_db",
        "run_skill",
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
        "wait_for_ci",
        "create_unique_branch",
        "write_telemetry_files",
        "get_pr_reviews",
        "bulk_close_issues",
        "check_pr_mergeable",
        "set_commit_status",
        "wait_for_merge_queue",
        "check_repo_merge_state",
        "toggle_auto_merge",
        # formerly ungated — now kitchen-gated:
        "fetch_github_issue",
        "get_issue_title",
        "get_ci_status",
        "get_pipeline_report",
        "get_quota_events",
        "get_timing_summary",
        "get_token_summary",
        "kitchen_status",
        "list_recipes",
        "load_recipe",
        "validate_recipe",
        "register_clone_status",
        "batch_cleanup_clones",
    }
    assert GATED_TOOLS == expected


def test_check_quota_not_in_ungated_tools():
    """check_quota must not be in UNGATED_TOOLS.
    UNGATED_TOOLS contains tools agents legitimately call.
    check_quota enforcement is structural (hook), not agent-invoked."""
    from autoskillit.pipeline.gate import UNGATED_TOOLS

    assert "check_quota" not in UNGATED_TOOLS


def test_ungated_tools_contains_expected_names():
    from autoskillit.pipeline.gate import UNGATED_TOOLS

    expected = {"open_kitchen", "close_kitchen", "disable_quota_guard"}
    assert UNGATED_TOOLS == expected


def test_gate_file_lease_symbols_not_exported():
    """Lease mechanism symbols must not be importable from pipeline.gate."""
    import autoskillit.pipeline.gate as gate_module

    for symbol in (
        "gate_file_path",
        "hook_config_path",
        "GATE_FILENAME",
        "GATE_DIR_COMPONENTS",
        "LEASE_FIELDS",
        "verify_lease",
        "LeaseStatus",
        "is_pid_alive",
        "read_starttime_ticks",
        "read_boot_id",
    ):
        assert not hasattr(gate_module, symbol), f"gate.py still exports {symbol!r}"


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


def test_gated_tools_does_not_contain_run_recipe():
    from autoskillit.pipeline.gate import GATED_TOOLS

    assert "run_recipe" not in GATED_TOOLS


def test_headless_tools_contains_expected_names():
    from autoskillit.core.types import HEADLESS_TOOLS

    assert HEADLESS_TOOLS == {"test_check"}


def test_free_range_tools_contains_expected_names():
    from autoskillit.core.types import FREE_RANGE_TOOLS

    assert FREE_RANGE_TOOLS == {"open_kitchen", "close_kitchen", "disable_quota_guard"}


def test_ungated_tools_equals_free_range_tools():
    from autoskillit.core.types import FREE_RANGE_TOOLS, UNGATED_TOOLS

    assert UNGATED_TOOLS == FREE_RANGE_TOOLS


def test_all_tool_sets_disjoint_and_complete():
    from autoskillit.core.types import HEADLESS_TOOLS
    from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS

    assert GATED_TOOLS.isdisjoint(UNGATED_TOOLS)
    assert GATED_TOOLS.isdisjoint(HEADLESS_TOOLS)
    assert UNGATED_TOOLS.isdisjoint(HEADLESS_TOOLS)


def test_worker_tools_removed_from_core():
    import autoskillit.core.types as t

    assert not hasattr(t, "WORKER_TOOLS"), "WORKER_TOOLS must be removed"
    assert not hasattr(t, "HEADLESS_BLOCKED_UNGATED_TOOLS"), (
        "HEADLESS_BLOCKED_UNGATED_TOOLS must be removed"
    )


def test_headless_error_result_fields():
    import json

    from autoskillit.pipeline.gate import headless_error_result

    parsed = json.loads(headless_error_result())
    assert parsed["success"] is False
    assert parsed["is_error"] is True
    assert parsed["subtype"] == "headless_error"
    assert parsed["exit_code"] == -1
    assert parsed["needs_retry"] is False
    assert parsed["retry_reason"] == "none"
    assert "session_id" in parsed
    assert "stderr" in parsed
    assert "token_usage" in parsed


def test_headless_error_result_field_parity():
    import json

    from autoskillit.pipeline.gate import gate_error_result, headless_error_result

    gate = json.loads(gate_error_result())
    headless = json.loads(headless_error_result())
    assert set(gate.keys()) == set(headless.keys())
    assert headless["token_usage"] is None
    assert headless["cli_subtype"] == ""
    assert headless["write_path_warnings"] == []


def test_headless_error_result_accepts_custom_message():
    import json

    from autoskillit.pipeline.gate import headless_error_result

    parsed = json.loads(headless_error_result("Custom headless error"))
    assert parsed["result"] == "Custom headless error"
    assert parsed["subtype"] == "headless_error"
