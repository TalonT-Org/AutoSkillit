"""Tests for CodexAppServerDriver's JSON-RPC line-driven handshake state machine."""

from __future__ import annotations

import json
import subprocess

import pytest

from autoskillit.core import CodexAppServerPlan
from autoskillit.execution.backends._codex.app_server import (
    CodexAppServerDriver,
    _parse_user_agent_version,
)
from autoskillit.execution.backends._codex_discovery import CODEX_SKILL_DISCOVERY_CONTRACT

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_SESSION_HOME = "/tmp/session"
_CATALOG_ROOT = "/tmp/session/add-dir/skills"
_CWD = "/tmp/session"
_MIN_VERSION = CODEX_SKILL_DISCOVERY_CONTRACT.extra_roots_min_version


def _make_plan(**overrides: object) -> CodexAppServerPlan:
    defaults: dict[str, object] = dict(
        session_home=_SESSION_HOME,
        catalog_root=_CATALOG_ROOT,
        expected_skill_names=frozenset({"foo"}),
        expected_skill_entries=(("foo", "foo/SKILL.md"),),
        cwd=_CWD,
        prompt="do the thing",
        model="gpt-5.6-sol",
        sandbox="workspace-write",
        approval_policy="never",
        bypass_hook_trust=True,
        developer_instructions=None,
        config_overrides={"model_reasoning_effort": "high"},
        client_version="0.10.1109",
    )
    defaults.update(overrides)
    return CodexAppServerPlan(**defaults)  # type: ignore[arg-type]


def _response(id_: int, *, result: object = None, error: dict[str, object] | None = None) -> str:
    obj: dict[str, object] = {"id": id_}
    if error is not None:
        obj["error"] = error
    else:
        obj["result"] = {} if result is None else result
    return json.dumps(obj)


def _notification(method: str, params: dict[str, object] | None = None) -> str:
    obj: dict[str, object] = {"method": method}
    if params is not None:
        obj["params"] = params
    return json.dumps(obj)


def _initialize_result(
    *, codex_home: str = _SESSION_HOME, user_agent: str = f"autoskillit/{_MIN_VERSION} (codex-cli)"
) -> dict[str, object]:
    return {"codexHome": codex_home, "userAgent": user_agent}


def _skills_list_result(
    *,
    cwd: str = _CWD,
    skills: list[dict[str, object]] | None = None,
    errors: list[str] | None = None,
) -> dict[str, object]:
    default_skills = [
        {"name": "foo", "path": f"{_CATALOG_ROOT}/foo/SKILL.md", "enabled": True},
    ]
    # "data" is the real wire key (confirmed live against codex-cli 0.153.4's
    # app-server) -- see TestSkillsList.test_results_keyed_response_is_not_accepted
    # for the regression proving the driver does not also accept "results".
    return {
        "data": [
            {
                "cwd": cwd,
                "skills": default_skills if skills is None else skills,
                "errors": errors or [],
            }
        ]
    }


def _thread_result(thread_id: str = "thread-1") -> dict[str, object]:
    return {"thread": {"id": thread_id}}


def _advance_to_thread_request(driver: CodexAppServerDriver) -> None:
    """Drive a fresh driver through initialize -> extraRoots -> skills/list."""
    driver.on_line(_response(1, result=_initialize_result()))
    driver.on_line(_response(2, result={}))
    driver.on_line(_response(3, result=_skills_list_result()))


class TestInitialLines:
    def test_yields_only_initialize(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        lines = driver.initial_lines()
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["id"] == 1
        assert obj["method"] == "initialize"
        assert "jsonrpc" not in obj
        assert obj["params"]["clientInfo"]["version"] == "0.10.1109"
        assert obj["params"]["capabilities"]["experimentalApi"] is True


class TestInitializeHandshake:
    def test_successful_response_emits_initialized_then_extra_roots_set(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        lines = driver.on_line(_response(1, result=_initialize_result()))
        assert driver.failure is None
        assert len(lines) == 2
        initialized = json.loads(lines[0])
        assert initialized == {"method": "initialized"}
        assert "id" not in initialized
        assert "params" not in initialized
        extra_roots = json.loads(lines[1])
        assert extra_roots["id"] == 2
        assert extra_roots["method"] == "skills/extraRoots/set"
        assert extra_roots["params"] == {"extraRoots": [_CATALOG_ROOT]}
        assert "jsonrpc" not in extra_roots

    def test_parses_version_independent_of_supplied_client_version(self) -> None:
        driver = CodexAppServerDriver(_make_plan(client_version="9.9.9"))
        driver.on_line(
            _response(
                1,
                result=_initialize_result(
                    user_agent=f"autoskillit-dry-walk/{_MIN_VERSION} (codex-cli 0.153.4)"
                ),
            )
        )
        assert driver.failure is None

    def test_codex_home_mismatch_sets_failure_naming_both_paths(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(1, result=_initialize_result(codex_home="/other/home")))
        assert driver.failure is not None
        assert _SESSION_HOME in driver.failure
        assert "/other/home" in driver.failure

    def test_below_minimum_version_sets_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(
            _response(1, result=_initialize_result(user_agent="autoskillit/0.100.0 (codex-cli)"))
        )
        assert driver.failure is not None
        assert "0.100.0" in driver.failure

    def test_unparseable_user_agent_sets_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(1, result=_initialize_result(user_agent="garbage-no-slash")))
        assert driver.failure is not None

    def test_unparseable_version_component_sets_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(
            _response(1, result=_initialize_result(user_agent="autoskillit/not-a-version"))
        )
        assert driver.failure is not None

    def test_wrong_response_id_sets_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(99, result=_initialize_result()))
        assert driver.failure is not None
        assert "99" in driver.failure


class TestParseUserAgentVersion:
    @pytest.mark.parametrize(
        ("user_agent", "expected"),
        [
            ("autoskillit/0.153.4 (codex-cli 0.153.4)", "0.153.4"),
            ("autoskillit-dry-walk/0.136.0 (x)", "0.136.0"),
            ("no-slash-token", None),
            ("", None),
            (None, None),
        ],
    )
    def test_extracts_version_or_none(self, user_agent: str | None, expected: str | None) -> None:
        assert _parse_user_agent_version(user_agent) == expected


class TestSkillsList:
    def test_success_emits_thread_start(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(1, result=_initialize_result()))
        driver.on_line(_response(2, result={}))
        lines = driver.on_line(_response(3, result=_skills_list_result()))
        assert driver.failure is None
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["id"] == 4
        assert obj["method"] == "thread/start"
        assert obj["params"]["cwd"] == _CWD
        assert obj["params"]["model"] == "gpt-5.6-sol"
        assert obj["params"]["sandbox"] == "workspace-write"
        assert obj["params"]["approvalPolicy"] == "never"
        assert obj["params"]["developerInstructions"] is None
        assert obj["params"]["ephemeral"] is False
        assert obj["params"]["config"]["bypass_hook_trust"] is True
        assert obj["params"]["config"]["model_reasoning_effort"] == "high"

    def test_resume_emits_thread_resume(self) -> None:
        driver = CodexAppServerDriver(_make_plan(resume_thread_id="thread-9"))
        driver.on_line(_response(1, result=_initialize_result()))
        driver.on_line(_response(2, result={}))
        lines = driver.on_line(_response(3, result=_skills_list_result()))
        obj = json.loads(lines[0])
        assert obj["method"] == "thread/resume"
        assert obj["params"]["threadId"] == "thread-9"
        assert "ephemeral" not in obj["params"]

    def test_loader_errors_set_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(1, result=_initialize_result()))
        driver.on_line(_response(2, result={}))
        driver.on_line(_response(3, result=_skills_list_result(errors=["boom"])))
        assert driver.failure is not None
        assert "boom" in driver.failure

    def test_wrong_cwd_sets_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(1, result=_initialize_result()))
        driver.on_line(_response(2, result={}))
        driver.on_line(_response(3, result=_skills_list_result(cwd="/somewhere/else")))
        assert driver.failure is not None
        assert _CWD in driver.failure

    def test_missing_expected_name_sets_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(1, result=_initialize_result()))
        driver.on_line(_response(2, result={}))
        driver.on_line(_response(3, result=_skills_list_result(skills=[])))
        assert driver.failure is not None
        assert "foo" in driver.failure

    def test_wrong_canonical_path_sets_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(1, result=_initialize_result()))
        driver.on_line(_response(2, result={}))
        driver.on_line(
            _response(
                3,
                result=_skills_list_result(
                    skills=[{"name": "foo", "path": "/native/root/foo/SKILL.md", "enabled": True}]
                ),
            )
        )
        assert driver.failure is not None
        assert "/native/root/foo/SKILL.md" in driver.failure

    def test_disabled_expected_row_fails_even_with_matching_name_and_path(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(1, result=_initialize_result()))
        driver.on_line(_response(2, result={}))
        driver.on_line(
            _response(
                3,
                result=_skills_list_result(
                    skills=[
                        {"name": "foo", "path": f"{_CATALOG_ROOT}/foo/SKILL.md", "enabled": False}
                    ]
                ),
            )
        )
        assert driver.failure is not None
        assert "disabled" in driver.failure

    def test_results_keyed_response_is_not_accepted(self) -> None:
        """ "data" is the only real wire key for skills/list's per-cwd
        entries (confirmed live against codex-cli 0.153.4's app-server) --
        a response shaped with the old assumed "results" key instead must
        not be silently treated as an equivalent substitute. It is read as
        carrying zero entries and fails the same way any other response
        missing the cwd would."""
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(1, result=_initialize_result()))
        driver.on_line(_response(2, result={}))
        legacy_shaped_result = {
            "results": [
                {
                    "cwd": _CWD,
                    "skills": [
                        {"name": "foo", "path": f"{_CATALOG_ROOT}/foo/SKILL.md", "enabled": True}
                    ],
                    "errors": [],
                }
            ]
        }
        driver.on_line(_response(3, result=legacy_shaped_result))
        assert driver.failure is not None
        assert _CWD in driver.failure


class TestThreadAndTurn:
    def test_thread_start_success_emits_turn_start(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        _advance_to_thread_request(driver)
        lines = driver.on_line(_response(4, result=_thread_result("thread-abc")))
        assert driver.failure is None
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["id"] == 5
        assert obj["method"] == "turn/start"
        assert obj["params"]["threadId"] == "thread-abc"
        assert obj["params"]["input"] == [{"type": "text", "text": "do the thing"}]

    def test_missing_thread_id_sets_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        _advance_to_thread_request(driver)
        driver.on_line(_response(4, result={"thread": {}}))
        assert driver.failure is not None

    def test_resume_mismatched_thread_id_sets_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan(resume_thread_id="thread-9"))
        _advance_to_thread_request(driver)
        driver.on_line(_response(4, result=_thread_result("thread-other")))
        assert driver.failure is not None
        assert "thread-9" in driver.failure
        assert "thread-other" in driver.failure

    def test_resume_matching_thread_id_proceeds(self) -> None:
        driver = CodexAppServerDriver(_make_plan(resume_thread_id="thread-9"))
        _advance_to_thread_request(driver)
        lines = driver.on_line(_response(4, result=_thread_result("thread-9")))
        assert driver.failure is None
        assert json.loads(lines[0])["params"]["threadId"] == "thread-9"

    def test_turn_completed_notification_finishes_driver(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        _advance_to_thread_request(driver)
        driver.on_line(_response(4, result=_thread_result()))
        assert not driver.finished
        lines = driver.on_line(_response(5, result={}))
        assert lines == ()
        assert not driver.finished
        lines = driver.on_line(_notification("turn/completed", {"status": "completed"}))
        assert lines == ()
        assert driver.finished
        assert driver.failure is None

    def test_unrelated_notifications_are_ignored_and_do_not_advance_or_fail(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        _advance_to_thread_request(driver)
        lines = driver.on_line(_notification("item/started", {"item": {}}))
        assert lines == ()
        assert driver.failure is None
        assert not driver.finished
        # the handshake still proceeds correctly afterward
        lines = driver.on_line(_response(4, result=_thread_result()))
        assert driver.failure is None
        assert json.loads(lines[0])["method"] == "turn/start"

    def test_completion_not_accepted_after_prior_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(1, result=_initialize_result(codex_home="/wrong")))
        assert driver.failure is not None
        driver.on_line(_notification("turn/completed", {"status": "completed"}))
        assert not driver.finished


class TestUnsupportedServerRequests:
    @pytest.mark.parametrize(
        "method",
        [
            "execCommandApproval/request",
            "applyPatchApproval/request",
            "userInput/request",
            "elicitation/create",
            "totally/unknown/method",
        ],
    )
    def test_unsupported_request_gets_correlated_error_and_fails(self, method: str) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(1, result=_initialize_result()))
        lines = driver.on_line(json.dumps({"id": "srv-1", "method": method, "params": {}}))
        assert len(lines) == 1
        response = json.loads(lines[0])
        assert response["id"] == "srv-1"
        assert response["error"]["code"] == -32601
        assert "jsonrpc" not in response
        assert driver.failure is not None
        assert method in driver.failure
        assert "srv-1" in driver.failure

    def test_valid_notification_never_receives_a_response(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        lines = driver.on_line(_notification("thread/started", {"thread": {"id": "t1"}}))
        assert lines == ()
        assert driver.failure is None


class TestErrorResponses:
    @pytest.mark.parametrize("code", [-32700, -32600, -32601, -32602, -32603, -99999])
    def test_error_response_preserves_id_method_phase_code_message(self, code: int) -> None:
        driver = CodexAppServerDriver(_make_plan())
        lines = driver.on_line(
            _response(1, error={"code": code, "message": "something went wrong"})
        )
        assert lines == ()
        assert driver.failure is not None
        assert str(code) in driver.failure
        assert "something went wrong" in driver.failure
        assert "initialize" in driver.failure
        assert "1" in driver.failure


class TestMalformedAndOutOfOrderFrames:
    def test_malformed_json_sets_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line("{not json")
        assert driver.failure is not None

    def test_non_object_json_sets_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line("[1, 2, 3]")
        assert driver.failure is not None

    def test_blank_line_is_ignored(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        lines = driver.on_line("   ")
        assert lines == ()
        assert driver.failure is None

    def test_response_with_neither_result_nor_error_sets_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(json.dumps({"id": 1}))
        assert driver.failure is not None

    def test_duplicate_response_after_advance_sets_failure(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(1, result=_initialize_result()))
        # id=1 duplicated while phase now expects id=2
        driver.on_line(_response(1, result=_initialize_result()))
        assert driver.failure is not None

    def test_response_after_full_completion_handshake_fails(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        _advance_to_thread_request(driver)
        driver.on_line(_response(4, result=_thread_result()))
        driver.on_line(_response(5, result={}))
        # driver is now purely waiting for turn/completed; any further response
        # (however numbered) is out-of-order.
        driver.on_line(_response(5, result={}))
        assert driver.failure is not None

    def test_further_lines_after_failure_are_no_ops(self) -> None:
        driver = CodexAppServerDriver(_make_plan())
        driver.on_line(_response(1, result=_initialize_result(codex_home="/wrong")))
        failure_before = driver.failure
        lines = driver.on_line(_response(2, result={}))
        assert lines == ()
        assert driver.failure == failure_before


class TestNoSubprocessOrNetworkIO:
    def test_driver_module_imports_no_subprocess_or_socket(self) -> None:
        import autoskillit.execution.backends._codex.app_server as module

        source = module.__file__
        assert source is not None
        text = open(source, encoding="utf-8").read()
        for banned in ("import subprocess", "import socket", "asyncio.create_subprocess"):
            assert banned not in text, f"driver must perform no subprocess/network I/O: {banned}"

    def test_no_autoskillit_process_imports(self) -> None:
        import autoskillit.execution.backends._codex.app_server as module

        source = module.__file__
        assert source is not None
        text = open(source, encoding="utf-8").read()
        assert "subprocess.run" not in text
        assert "subprocess.Popen" not in text


class TestSubprocessRunUnused:
    """Guards the module truly never shells out (belt-and-suspenders on the above)."""

    def test_module_has_no_subprocess_attribute_usage(self) -> None:
        import autoskillit.execution.backends._codex.app_server as module

        assert not hasattr(module, "subprocess")
        # sanity: the stdlib subprocess module itself is unaffected by this guard
        assert subprocess.run is not None
