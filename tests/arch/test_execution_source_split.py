"""Arch guards for execution layer source splits (P8-F1, P8-F3, P8-F4 audit fixes)."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC_EXECUTION = Path(__file__).parent.parent.parent / "src" / "autoskillit" / "execution"

NEW_HEADLESS_MODULES = [
    "_headless_recovery.py",
    "_headless_path_tokens.py",
    "_headless_result.py",
]
HEADLESS_SIZE_BUDGETS = {
    "headless/__init__.py": 860,
    "headless/_headless_recovery.py": 320,
    "headless/_headless_path_tokens.py": 175,
    "headless/_headless_result.py": 616,
}


def test_new_headless_modules_exist():
    for name in NEW_HEADLESS_MODULES:
        assert (SRC_EXECUTION / "headless" / name).exists(), f"Missing: headless/{name}"


def test_headless_facade_under_budget():
    for name, limit in HEADLESS_SIZE_BUDGETS.items():
        lines = len((SRC_EXECUTION / name).read_text().splitlines())
        assert lines <= limit, f"{name}: {lines} lines exceeds budget of {limit}"


def test_headless_facade_does_not_define_build_skill_result():
    src = (SRC_EXECUTION / "headless" / "__init__.py").read_text()
    assert "def _build_skill_result" not in src, (
        "_build_skill_result must live in _headless_result.py, not headless/__init__.py"
    )


def test_headless_facade_does_not_define_recovery_functions():
    src = (SRC_EXECUTION / "headless" / "__init__.py").read_text()
    for fn in (
        "_recover_from_separate_marker",
        "_recover_block_from_assistant_messages",
        "_synthesize_from_write_artifacts",
        "_extract_missing_token_hints",
        "_attempt_contract_nudge",
    ):
        assert f"def {fn}" not in src, f"{fn} must live in _headless_recovery.py"


def test_headless_facade_does_not_define_path_token_functions():
    src = (SRC_EXECUTION / "headless" / "__init__.py").read_text()
    for fn in (
        "_build_path_token_set",
        "_extract_output_paths",
        "_validate_output_paths",
        "_extract_worktree_path",
    ):
        assert f"def {fn}" not in src, f"{fn} must live in _headless_path_tokens.py"


# ---------------------------------------------------------------------------
# P8-F3: session.py split guards
# ---------------------------------------------------------------------------

NEW_SESSION_MODULES = ["_session_model.py", "_session_content.py"]
NEW_MQ_MODULES = ["_merge_queue_classifier.py", "_merge_queue_repo_state.py"]

SESSION_SIZE_BUDGETS = {
    "session/__init__.py": 65,  # was 420; facade is ~40 lines after P2
    "session/_session_model.py": 435,
    "session/_session_content.py": 200,
}
NEW_SESSION_FSM_MODULES = ["_retry_fsm.py", "_session_outcome.py"]
SESSION_FSM_SIZE_BUDGETS = {
    "session/_retry_fsm.py": 200,
    "session/_session_outcome.py": 260,
}
MQ_SIZE_BUDGETS = {
    "merge_queue/__init__.py": 500,
    "merge_queue/_merge_queue_classifier.py": 175,
    "merge_queue/_merge_queue_repo_state.py": 280,
}


def test_new_session_modules_exist():
    for name in NEW_SESSION_MODULES:
        assert (SRC_EXECUTION / "session" / name).exists(), f"Missing: session/{name}"


def test_new_mq_modules_exist():
    for name in NEW_MQ_MODULES:
        assert (SRC_EXECUTION / "merge_queue" / name).exists(), f"Missing: merge_queue/{name}"


def test_session_and_mq_under_budget():
    for budgets in (SESSION_SIZE_BUDGETS, MQ_SIZE_BUDGETS):
        for name, limit in budgets.items():
            lines = len((SRC_EXECUTION / name).read_text().splitlines())
            assert lines <= limit, f"{name}: {lines} lines exceeds budget of {limit}"


def test_session_facade_does_not_define_model_types():
    src = (SRC_EXECUTION / "session" / "__init__.py").read_text()
    for sym in (
        "class ClaudeSessionResult",
        "class ContentState",
        "class _ParseAccumulator",
        "def extract_token_usage",
        "def parse_session_result",
    ):
        assert sym not in src, f"{sym} must live in _session_model.py"


def test_session_facade_does_not_define_content_functions():
    src = (SRC_EXECUTION / "session" / "__init__.py").read_text()
    for sym in (
        "def _check_expected_patterns",
        "def _check_session_content",
        "def _evaluate_content_state",
        "def _strip_markdown_from_tokens",
    ):
        assert sym not in src, f"{sym} must live in _session_content.py"


# ---------------------------------------------------------------------------
# P2: session.py FSM/outcome sub-module guards
# ---------------------------------------------------------------------------


def test_new_session_fsm_modules_exist():
    for name in NEW_SESSION_FSM_MODULES:
        assert (SRC_EXECUTION / "session" / name).exists(), f"Missing: session/{name}"


def test_session_fsm_modules_under_budget():
    for name, limit in SESSION_FSM_SIZE_BUDGETS.items():
        lines = len((SRC_EXECUTION / name).read_text().splitlines())
        assert lines <= limit, f"{name}: {lines} lines exceeds budget of {limit}"


def test_session_facade_does_not_define_retry_outcome():
    src = (SRC_EXECUTION / "session" / "__init__.py").read_text()
    for sym in (
        "_KILL_ANOMALY_SUBTYPES: frozenset",
        "def _is_kill_anomaly",
        "def _compute_retry",
        "def _compute_success",
        "def _compute_outcome",
    ):
        assert sym not in src, f"{sym} must live in _retry_fsm.py or _session_outcome.py"


# ---------------------------------------------------------------------------
# P8-F4: merge_queue.py split guards
# ---------------------------------------------------------------------------


def test_merge_queue_facade_does_not_define_classifier():
    src = (SRC_EXECUTION / "merge_queue" / "__init__.py").read_text()
    for sym in (
        "def _classify_pr_state",
        "class ClassifierInconclusive",
        "class ClassificationResult",
        "class PRFetchState",
    ):
        assert sym not in src, f"{sym} must live in _merge_queue_classifier.py"


def test_merge_queue_facade_does_not_define_repo_state():
    src = (SRC_EXECUTION / "merge_queue" / "__init__.py").read_text()
    for sym in (
        "async def fetch_repo_merge_state",
        "def _text_has_push_trigger",
        "def _has_merge_group_trigger",
    ):
        assert sym not in src, f"{sym} must live in _merge_queue_repo_state.py"
