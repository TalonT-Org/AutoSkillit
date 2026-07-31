"""PreToolUse deny-mechanism guard efficacy probe harness.

These probes measure guard decision outcomes conditional on hook firing. Whether the
backend fires PreToolUse for MCP-class tools is an open question (B4v) requiring live
CLI probes (CODEX_SMOKE_TEST-gated). The strength matrix emitted by P1-A1-WP3 consumes
these results.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from autoskillit.execution.backends._codex_hooks import generate_codex_hooks_config
from autoskillit.hook_registry import HOOK_REGISTRY, HOOKS_DIR
from autoskillit.hooks._capture_contract import decode_capture_request
from tests.execution.backends.conftest import (
    _SKIP_CODEX,
    BACKENDS,
    EXPECTED_TOTAL_PROBE_COUNT,
    SESSION_MODES,
    TOOL_CLASSES,
    record_probe_row,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


SESSION_MODE_ENV: dict[str, dict[str, str]] = {
    "interactive": {},
    "headless-p": {
        "AUTOSKILLIT_HEADLESS": "1",
        "AUTOSKILLIT_SESSION_TYPE": "skill",
    },
    "subagent": {
        "AUTOSKILLIT_HEADLESS": "1",
        "AUTOSKILLIT_SESSION_TYPE": "skill",
    },
}

BACKEND_ENV: dict[str, dict[str, str]] = {
    "claude-code": {"AUTOSKILLIT_AGENT_BACKEND": "claude-code"},
    "codex": {"AUTOSKILLIT_AGENT_BACKEND": "codex"},
}

# Contract: TOOL_CLASS_PAYLOADS.keys() == TOOL_CLASSES.keys()
TOOL_CLASS_PAYLOADS: dict[str, dict] = {
    "Bash": {
        "tool_name": TOOL_CLASSES["Bash"],
        "tool_input": {
            "command": (
                "pip install -e . && pytest tests/ "
                "&& gh pr create && gh run download "
                "&& gh pr review 7 --comment --body probe "
                "&& git commit --amend"
            ),
            "run_in_background": True,
        },
    },
    "Write": {
        "tool_name": TOOL_CLASSES["Write"],
        "tool_input": {
            "file_path": "/worktree/.autoskillit/phases/non-canonical-name.md",
        },
    },
    "Edit": {
        "tool_name": TOOL_CLASSES["Edit"],
        "tool_input": {
            "file_path": "/worktree/.autoskillit/phases/non-canonical-name.md",
        },
    },
    "Grep": {
        "tool_name": TOOL_CLASSES["Grep"],
        "tool_input": {
            "pattern": r"foo\|bar",
            "path": "/worktree/src",
        },
    },
    "AskUserQuestion": {
        "tool_name": TOOL_CLASSES["AskUserQuestion"],
        "tool_input": {
            "question": "probe",
        },
    },
    "mcp_run_skill": {
        "tool_name": TOOL_CLASSES["mcp_run_skill"],
        "tool_input": {
            "skill_command": "non-slash-command",
            "step_name": "probe-step",
        },
    },
    "mcp_run_cmd": {
        "tool_name": TOOL_CLASSES["mcp_run_cmd"],
        "tool_input": {
            "command": (
                "pip install -e . && pytest tests/ "
                "&& gh pr create && gh run download "
                "&& gh pr review 7 --comment --body probe "
                "&& git commit --amend"
            ),
            "cmd": (
                "pip install -e . && pytest tests/ "
                "&& gh pr create && gh run download "
                "&& gh pr review 7 --comment --body probe "
                "&& git commit --amend"
            ),
            "cwd": "/worktree",
            "run_in_background": True,
        },
    },
    "mcp_run_python": {
        "tool_name": TOOL_CLASSES["mcp_run_python"],
        "tool_input": {
            "callable": "autoskillit.recipe.run_thing",
        },
    },
    "mcp_open_kitchen": {
        "tool_name": TOOL_CLASSES["mcp_open_kitchen"],
        "tool_input": {},
    },
    "mcp_remove_clone": {
        "tool_name": TOOL_CLASSES["mcp_remove_clone"],
        "tool_input": {
            "clone_path": "/nonexistent/path/that/does/not/exist",
        },
    },
    "mcp_merge_worktree": {
        "tool_name": TOOL_CLASSES["mcp_merge_worktree"],
        "tool_input": {
            "base_branch": "main",
        },
    },
    "mcp_push_to_remote": {
        "tool_name": TOOL_CLASSES["mcp_push_to_remote"],
        "tool_input": {
            "branch": "main",
        },
    },
    "mcp_dispatch_food_truck": {
        "tool_name": TOOL_CLASSES["mcp_dispatch_food_truck"],
        "tool_input": {},
    },
    "mcp_wait_for_ci": {
        "tool_name": TOOL_CLASSES["mcp_wait_for_ci"],
        "tool_input": {},
    },
    "mcp_reset_dispatch": {
        "tool_name": TOOL_CLASSES["mcp_reset_dispatch"],
        "tool_input": {},
    },
    "apply_patch": {
        "tool_name": TOOL_CLASSES["apply_patch"],
        "tool_input": {
            "patch": "--- a/foo\n+++ b/foo\n@@ -1 +1 @@\n-old\n+new\n",
        },
    },
}

# Verify the contract: TOOL_CLASS_PAYLOADS.keys() == TOOL_CLASSES.keys()
assert set(TOOL_CLASS_PAYLOADS.keys()) == set(TOOL_CLASSES.keys()), (
    "TOOL_CLASS_PAYLOADS keys must match TOOL_CLASSES keys exactly"
)


def _clean_env() -> dict[str, str]:
    """Strip AUTOSKILLIT_* keys from the test runner's environment."""
    return {k: v for k, v in os.environ.items() if not k.startswith("AUTOSKILLIT_")}


def _invoke_guard(
    script_path: Path,
    stdin_payload: dict,
    env_overrides: dict[str, str],
    tmp_path: Path,
) -> dict:
    """Invoke a guard script as a subprocess and parse its JSON output.

    Asserts exit code 0 (fail-open contract — guards must exit 0 on either decision).
    Returns the parsed stdout JSON dict, or empty dict on empty stdout.
    """
    merged_env = {**_clean_env(), **env_overrides}
    result = subprocess.run(  # noqa: S603  (intentional subprocess probe)
        [sys.executable, str(script_path)],
        input=json.dumps(stdin_payload),
        env=merged_env,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=5,
    )
    assert result.returncode == 0, (
        f"guard {script_path.name} exited {result.returncode}: {result.stderr}"
    )
    stdout = result.stdout.strip()
    if not stdout:
        return {}
    return json.loads(stdout)


_OUTPUT_BUDGET_PROBE_COMMAND = "rg -n output_budget_probe ."


def _build_payload(tool_class: str, session_mode: str) -> dict:
    """Deep-copy the deny-triggering payload for a tool class, plus subagent fields."""
    payload = copy.deepcopy(TOOL_CLASS_PAYLOADS[tool_class])
    if session_mode == "subagent":
        payload["agent_id"] = "probe-subagent-001"
        payload["agent_type"] = "general-purpose"
    return payload


def _collect_probe_params() -> list[Any]:
    """Yield pytest.param entries for every PreToolUse+deny combination.

    Cross-product: (script) × (matching tool classes) × SESSION_MODES × BACKENDS.
    Co-derivation contract: every yielded ``script`` satisfies
    ``script in HOOK_REGISTRY[hookdef_idx].scripts``.
    Yields ALL combinations including inert codex rows (inert check is in test body).
    """
    params: list[Any] = []
    for hookdef_idx, hd in enumerate(HOOK_REGISTRY):
        if hd.event_type != "PreToolUse" or hd.mechanism != "deny":
            continue
        for tc_key, tc_value in TOOL_CLASSES.items():
            if not re.fullmatch(hd.matcher, tc_value):
                continue
            for script in hd.scripts:
                for session_mode in SESSION_MODES:
                    for backend in BACKENDS:
                        params.append(
                            pytest.param(
                                hookdef_idx,
                                script,
                                tc_key,
                                session_mode,
                                backend,
                                id=(f"{Path(script).stem}__{tc_key}__{session_mode}__{backend}"),
                            )
                        )
    return params


class TestHookDenyEfficacyProbe:
    """Probe each deny-mechanism guard script as a subprocess across the matrix."""

    @pytest.mark.parametrize(
        "hookdef_idx, script, tool_class, session_mode, backend",
        _collect_probe_params(),
    )
    def test_probe(
        self,
        hookdef_idx: int,
        script: str,
        tool_class: str,
        session_mode: str,
        backend: str,
        tmp_path: Path,
    ) -> None:
        hookdef = HOOK_REGISTRY[hookdef_idx]

        # --- Inert check for codex backend ---
        # Mirrors generate_codex_hooks_config() at _codex_hooks.py:52-55: skip
        # interactive_only session_scope OR codex_status in {fix-required, not-applicable}.
        if backend == "codex":
            if hookdef.session_scope == "interactive_only" or hookdef.codex_status in _SKIP_CODEX:
                codex_config = generate_codex_hooks_config()
                script_stem = Path(script).stem
                for entries in codex_config.values():
                    for entry in entries:
                        for hook in entry.get("hooks", []):
                            assert script_stem not in hook.get("command", ""), (
                                f"{script_stem} found in codex config but should be excluded"
                            )
                return  # Inert — do not record to matrix

        # --- Build env and payload ---
        env = {**SESSION_MODE_ENV[session_mode], **BACKEND_ENV[backend]}
        env["AUTOSKILLIT_ALLOWED_WRITE_PREFIXES"] = str(tmp_path)
        env["AUTOSKILLIT_CWD"] = str(tmp_path)
        payload = _build_payload(tool_class, session_mode)

        # --- Set up minimal filesystem state where needed ---
        (tmp_path / ".autoskillit" / "temp").mkdir(parents=True, exist_ok=True)

        # --- Invoke guard ---
        script_path = HOOKS_DIR / script
        result = _invoke_guard(script_path, payload, env, tmp_path)

        # --- Classify outcome ---
        hook_output = result.get("hookSpecificOutput", {})
        decision = hook_output.get("permissionDecision")

        if not result:
            strength = "none"
        elif decision == "deny":
            strength = "hard"
        else:
            strength = "soft"

        # --- Record to matrix ---
        record_probe_row(
            {
                "tool_class": tool_class,
                "session_mode": session_mode,
                "backend": backend,
                "hook": Path(script).stem,
                "observed_decision": decision or "allow",
                "codex_status_claimed": hookdef.codex_status,
                "strength": strength,
            }
        )


def test_probe_collection_count() -> None:
    """Meta-test: parametrized probe count must match the registry-derived total.

    Uses ``EXPECTED_TOTAL_PROBE_COUNT`` (total including inert), since
    ``_collect_probe_params()`` yields all combinations. Adding a new
    deny-mechanism hook automatically fails this test until the matrix adapts.
    """
    assert len(_collect_probe_params()) == EXPECTED_TOTAL_PROBE_COUNT


def test_shell_capture_hook_is_input_rewrite_and_excluded_from_deny_matrix(
    tmp_path: Path,
) -> None:
    script = "shell_capture_hook.py"
    hookdef_idx, hookdef = next(
        (idx, hookdef) for idx, hookdef in enumerate(HOOK_REGISTRY) if script in hookdef.scripts
    )
    assert hookdef.mechanism == "input-rewrite"

    # An input-rewrite hook emits allow+updatedInput, never a JSON deny, so it
    # is not part of the deny-mechanism probe matrix collected by
    # _collect_probe_params() (which filters on mechanism == "deny").
    actual_rows = {
        (values[2], values[3], values[4])
        for parameter in _collect_probe_params()
        if (values := parameter.values)[0] == hookdef_idx and values[1] == script
    }
    assert actual_rows == set()

    codex_entries = generate_codex_hooks_config()["PreToolUse"]
    codex_entry = next(entry for entry in codex_entries if entry["matcher"] == hookdef.matcher)
    codex_hook = next(
        hook for hook in codex_entry["hooks"] if "shell_capture_hook" in hook["command"]
    )
    assert codex_hook["trusted_hash"]

    env = {
        **BACKEND_ENV["codex"],
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES": str(tmp_path),
        "AUTOSKILLIT_CWD": str(tmp_path),
    }
    (tmp_path / ".autoskillit" / "temp").mkdir(parents=True)
    payload = _build_payload("Bash", "interactive")
    payload["tool_input"]["command"] = _OUTPUT_BUDGET_PROBE_COMMAND
    payload["cwd"] = str(tmp_path)
    payload["turn_id"] = "probe-turn"
    result = _invoke_guard(
        HOOKS_DIR / script,
        payload,
        env,
        tmp_path,
    )
    hook_output = result["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "allow"
    updated_command = hook_output["updatedInput"]["command"]
    assert "autoskillit-shell-capture" in updated_command
    assert _OUTPUT_BUDGET_PROBE_COMMAND in updated_command

    argv = shlex.split(updated_command.splitlines()[-1])
    runner_index = next(
        index for index, value in enumerate(argv) if value.endswith("_capture_artifacts.py")
    )
    assert argv[runner_index - 2] == sys.executable
    assert argv[runner_index - 1] == "-I"
    assert runner_index + 2 == len(argv)
    request = decode_capture_request(argv[runner_index + 1])
    assert request.action == "run"
    assert request.command == _OUTPUT_BUDGET_PROBE_COMMAND
    assert request.cwd == str(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{16}", request.capture_id)
