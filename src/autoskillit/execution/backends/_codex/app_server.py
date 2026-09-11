"""JSON-RPC line driver for managed Codex `app-server` skill sessions.

``CodexAppServerDriver`` implements ``LineDriver`` (see
``core.types._type_subprocess``) as a strict five-request state machine over
``codex app-server --listen stdio://``'s newline-delimited JSON-RPC wire
format. The transport uses JSON-RPC 2.0 request/response/notification
semantics but omits the ``"jsonrpc":"2.0"`` member on every emitted line —
every envelope this driver builds follows that convention.

One driver instance is bound to exactly one ``CodexAppServerPlan`` and is
discarded after the managed launch it drove. The runner feeds it every
decoded stdout line via ``on_line`` and writes whatever lines it returns
back to the child's stdin (see ``execution.process._process_io``). Request
ids 1 through 5 correspond to the five requests the handshake ever sends:
``initialize``, ``skills/extraRoots/set``, ``skills/list``,
``thread/start``/``thread/resume``, and ``turn/start``. Completion is
signalled by the server's ``turn/completed`` notification, not by a
response to any of these requests.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from enum import Enum, auto
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from autoskillit.core import CodexAppServerPlan
from autoskillit.execution.backends._codex_discovery import CODEX_SKILL_DISCOVERY_CONTRACT

#: JSON-RPC 2.0 "Method not found" — the fixed code for every unsupported
#: server-to-client request this driver refuses (approval/elicitation/
#: user-input requests have no answering channel in a headless session).
_UNSUPPORTED_METHOD_ERROR_CODE = -32601

_INITIALIZE_ID = 1
_EXTRA_ROOTS_SET_ID = 2
_SKILLS_LIST_ID = 3
_THREAD_ID = 4
_TURN_START_ID = 5


class _Phase(Enum):
    """Which of the driver's own outstanding requests it is waiting on."""

    AWAITING_INITIALIZE = auto()
    AWAITING_EXTRA_ROOTS_SET = auto()
    AWAITING_SKILLS_LIST = auto()
    AWAITING_THREAD_RESPONSE = auto()
    AWAITING_TURN_START_RESPONSE = auto()
    AWAITING_TURN_COMPLETED = auto()


_PHASE_METHOD: dict[_Phase, str] = {
    _Phase.AWAITING_INITIALIZE: "initialize",
    _Phase.AWAITING_EXTRA_ROOTS_SET: "skills/extraRoots/set",
    _Phase.AWAITING_SKILLS_LIST: "skills/list",
    _Phase.AWAITING_THREAD_RESPONSE: "thread/start-or-resume",
    _Phase.AWAITING_TURN_START_RESPONSE: "turn/start",
}


def _encode(obj: Mapping[str, Any]) -> str:
    return json.dumps(obj, separators=(",", ":"))


def _parse_user_agent_version(user_agent: object) -> str | None:
    """Extract the server build version from an ``initialize`` ``userAgent``.

    The first whitespace-delimited token is ``<name>/<version>`` — the name
    echoes our own ``clientInfo.name``; the version is the server's own
    build, never the ``client_version`` we supplied.
    """
    if not isinstance(user_agent, str) or not user_agent:
        return None
    first_token = user_agent.split(" ", 1)[0]
    if "/" not in first_token:
        return None
    _, _, version = first_token.partition("/")
    return version or None


class CodexAppServerDriver:
    """Drives one managed Codex `app-server` session's JSON-RPC handshake."""

    def __init__(self, plan: CodexAppServerPlan) -> None:
        self._plan = plan
        self._phase = _Phase.AWAITING_INITIALIZE
        self._thread_id = ""
        self._observed_server_version: str | None = None
        self.finished = False
        self.failure: str | None = None
        self._response_handlers: dict[
            _Phase, tuple[int, Callable[[Mapping[str, Any]], tuple[str, ...]]]
        ] = {
            _Phase.AWAITING_INITIALIZE: (_INITIALIZE_ID, self._accept_initialize),
            _Phase.AWAITING_EXTRA_ROOTS_SET: (_EXTRA_ROOTS_SET_ID, self._accept_extra_roots_set),
            _Phase.AWAITING_SKILLS_LIST: (_SKILLS_LIST_ID, self._accept_skills_list),
            _Phase.AWAITING_THREAD_RESPONSE: (_THREAD_ID, self._accept_thread_response),
            _Phase.AWAITING_TURN_START_RESPONSE: (
                _TURN_START_ID,
                self._accept_turn_start_response,
            ),
        }

    # -- LineDriver protocol -------------------------------------------------

    def initial_lines(self) -> tuple[str, ...]:
        return (_encode(self._initialize_request()),)

    def on_line(self, line: str) -> tuple[str, ...]:
        if self.failure is not None or self.finished:
            return ()
        stripped = line.strip()
        if not stripped:
            return ()
        try:
            obj = json.loads(stripped)
        except ValueError:
            self._fail(f"malformed JSON-RPC frame: {line!r}")
            return ()
        if not isinstance(obj, dict):
            self._fail(f"malformed JSON-RPC frame (not an object): {line!r}")
            return ()
        method = obj.get("method")
        if method is not None and "id" in obj:
            return self._handle_server_request(obj)
        if method is not None:
            return self._handle_notification(obj)
        return self._handle_response(obj)

    # -- outbound request construction --------------------------------------

    def _initialize_request(self) -> dict[str, Any]:
        return {
            "id": _INITIALIZE_ID,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "autoskillit",
                    "title": "AutoSkillit",
                    "version": self._plan.client_version,
                },
                "capabilities": {"experimentalApi": True},
            },
        }

    @staticmethod
    def _initialized_notification() -> dict[str, Any]:
        return {"method": "initialized"}

    def _extra_roots_set_request(self) -> dict[str, Any]:
        return {
            "id": _EXTRA_ROOTS_SET_ID,
            "method": "skills/extraRoots/set",
            "params": {"extraRoots": [self._plan.catalog_root]},
        }

    def _skills_list_request(self) -> dict[str, Any]:
        return {
            "id": _SKILLS_LIST_ID,
            "method": "skills/list",
            "params": {"cwds": [self._plan.cwd], "forceReload": True},
        }

    def _thread_config(self) -> dict[str, object]:
        # bypass_hook_trust is the driver's own field to place — config_overrides
        # (built by build_skill_session_cmd) supplies the remaining, model-owned
        # keys (model_reasoning_effort, sandbox_workspace_write.network_access).
        return {
            "bypass_hook_trust": self._plan.bypass_hook_trust,
            **dict(self._plan.config_overrides),
        }

    def _thread_request(self) -> dict[str, Any]:
        if self._plan.resume_thread_id:
            return {
                "id": _THREAD_ID,
                "method": "thread/resume",
                "params": {
                    "threadId": self._plan.resume_thread_id,
                    "cwd": self._plan.cwd,
                    "model": self._plan.model,
                    "sandbox": self._plan.sandbox,
                    "approvalPolicy": self._plan.approval_policy,
                    "developerInstructions": self._plan.developer_instructions,
                    "config": self._thread_config(),
                },
            }
        return {
            "id": _THREAD_ID,
            "method": "thread/start",
            "params": {
                "cwd": self._plan.cwd,
                "model": self._plan.model,
                "sandbox": self._plan.sandbox,
                "approvalPolicy": self._plan.approval_policy,
                "developerInstructions": self._plan.developer_instructions,
                "config": self._thread_config(),
                "ephemeral": False,
            },
        }

    def _turn_start_request(self) -> dict[str, Any]:
        return {
            "id": _TURN_START_ID,
            "method": "turn/start",
            "params": {
                "threadId": self._thread_id,
                "input": [{"type": "text", "text": self._plan.prompt}],
            },
        }

    # -- inbound dispatch -----------------------------------------------------

    def _handle_server_request(self, obj: Mapping[str, Any]) -> tuple[str, ...]:
        method = obj.get("method")
        request_id = obj.get("id")
        response = _encode(
            {
                "id": request_id,
                "error": {
                    "code": _UNSUPPORTED_METHOD_ERROR_CODE,
                    "message": f"Method not found: {method}",
                },
            }
        )
        self._fail(f"unsupported server request {method!r} (id={request_id!r})")
        return (response,)

    def _handle_notification(self, obj: Mapping[str, Any]) -> tuple[str, ...]:
        if obj.get("method") == "turn/completed":
            self.finished = True
        return ()

    def _handle_response(self, obj: Mapping[str, Any]) -> tuple[str, ...]:
        pending = self._response_handlers.get(self._phase)
        if pending is None:
            self._fail(f"unexpected response after completion handshake: {obj!r}")
            return ()
        expected_id, handler = pending
        response_id = obj.get("id")
        if response_id != expected_id:
            self._fail(
                f"unexpected response id={response_id!r}, expected id={expected_id!r} "
                f"for method={_PHASE_METHOD[self._phase]!r} phase={self._phase.name}"
            )
            return ()
        if "error" in obj:
            error = obj.get("error") or {}
            self._fail(
                f"server error for id={expected_id} method={_PHASE_METHOD[self._phase]!r} "
                f"phase={self._phase.name}: code={error.get('code')!r} "
                f"message={error.get('message')!r}"
            )
            return ()
        if "result" not in obj:
            self._fail(f"malformed response (neither result nor error present): {obj!r}")
            return ()
        return handler(obj["result"])

    # -- per-phase acceptance -------------------------------------------------

    def _accept_initialize(self, result: Mapping[str, Any]) -> tuple[str, ...]:
        codex_home = result.get("codexHome")
        if codex_home != self._plan.session_home:
            self._fail(
                f"initialize codexHome {codex_home!r} does not match session home "
                f"{self._plan.session_home!r}"
            )
            return ()
        user_agent = result.get("userAgent")
        version = _parse_user_agent_version(user_agent)
        if version is None:
            self._fail(f"could not parse server version from userAgent {user_agent!r}")
            return ()
        self._observed_server_version = version
        min_version = CODEX_SKILL_DISCOVERY_CONTRACT.extra_roots_min_version
        try:
            below_minimum = Version(version) < Version(min_version)
        except InvalidVersion:
            self._fail(f"unparseable server version {version!r} in userAgent {user_agent!r}")
            return ()
        if below_minimum:
            self._fail(
                f"codex app-server build {version} is below the supported minimum {min_version}"
            )
            return ()
        self._phase = _Phase.AWAITING_EXTRA_ROOTS_SET
        return (
            _encode(self._initialized_notification()),
            _encode(self._extra_roots_set_request()),
        )

    def _accept_extra_roots_set(self, result: Mapping[str, Any]) -> tuple[str, ...]:
        del result  # presence of a (non-error) result is the acknowledgement
        self._phase = _Phase.AWAITING_SKILLS_LIST
        return (_encode(self._skills_list_request()),)

    def _accept_skills_list(self, result: Mapping[str, Any]) -> tuple[str, ...]:
        entries = result.get("results") or []
        matched: Mapping[str, Any] | None = None
        for entry in entries:
            if isinstance(entry, dict) and entry.get("cwd") == self._plan.cwd:
                matched = entry
                break
        if matched is None:
            self._fail(f"skills/list result missing an entry for cwd {self._plan.cwd!r}")
            return ()
        loader_errors = matched.get("errors") or []
        if loader_errors:
            self._fail(
                f"skills/list reported loader errors for cwd {self._plan.cwd!r}: {loader_errors!r}"
            )
            return ()
        rows_by_name: dict[str, Mapping[str, Any]] = {}
        for row in matched.get("skills") or []:
            if isinstance(row, dict) and isinstance(row.get("name"), str):
                rows_by_name[row["name"]] = row
        for name, relative_path in self._plan.expected_skill_entries:
            row = rows_by_name.get(name)
            if row is None:
                self._fail(
                    f"skills/list missing expected skill {name!r} for cwd {self._plan.cwd!r}"
                )
                return ()
            if row.get("enabled") is False:
                self._fail(f"skills/list reports expected skill {name!r} as disabled")
                return ()
            expected_path = str(Path(self._plan.catalog_root) / relative_path)
            observed_path = row.get("path")
            if observed_path != expected_path:
                self._fail(
                    f"skills/list resolved {name!r} to {observed_path!r}, "
                    f"expected canonical path {expected_path!r}"
                )
                return ()
        self._phase = _Phase.AWAITING_THREAD_RESPONSE
        return (_encode(self._thread_request()),)

    def _accept_thread_response(self, result: Mapping[str, Any]) -> tuple[str, ...]:
        thread = result.get("thread") or {}
        thread_id = thread.get("id")
        if not thread_id:
            self._fail("thread response carried no thread id")
            return ()
        if self._plan.resume_thread_id and thread_id != self._plan.resume_thread_id:
            self._fail(
                f"thread/resume returned mismatched thread id {thread_id!r}, "
                f"expected {self._plan.resume_thread_id!r}"
            )
            return ()
        self._thread_id = thread_id
        self._phase = _Phase.AWAITING_TURN_START_RESPONSE
        return (_encode(self._turn_start_request()),)

    def _accept_turn_start_response(self, result: Mapping[str, Any]) -> tuple[str, ...]:
        del result  # the turn's own completion arrives later as a notification
        self._phase = _Phase.AWAITING_TURN_COMPLETED
        return ()

    # -- diagnostics -----------------------------------------------------------

    def _fail(self, diagnostic: str) -> None:
        contract = CODEX_SKILL_DISCOVERY_CONTRACT
        self.failure = (
            f"{diagnostic} (contract.extra_roots_min_version="
            f"{contract.extra_roots_min_version!r}, observed_binary_version="
            f"{self._observed_server_version!r})"
        )
