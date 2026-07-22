"""Dedicated Codex recipe-delivery conformance and live retention probe."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from autoskillit.core import (
    AGENT_BACKEND_CODEX,
    AGENT_BACKEND_DYNACONF_ENV_VAR,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    HEADLESS_AUTO_GATE_ENV_VAR,
    HEADLESS_ENV_VAR,
    MCP_CLIENT_BACKEND_ENV_VAR,
    RecipeDeliveryMode,
    RecipeDeliveryRequest,
)
from autoskillit.execution.backends import (
    BACKEND_REGISTRY,
    CODEX_RECIPE_DELIVERY_BUDGET,
    SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY,
)
from autoskillit.execution.backends._codex_config import ensure_codex_mcp_registered
from autoskillit.execution.backends._codex_hooks import sync_hooks_to_codex_config
from autoskillit.execution.backends._probe_cache import (
    CODEX_RECIPE_PROBE_MODEL_IDENTITY,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.large]

_AUTH_PATH = Path("~/.codex/auth.json").expanduser()
_skip_unless_live = pytest.mark.skipif(
    not os.environ.get("CODEX_SMOKE_TEST")
    or not shutil.which("codex")
    or (
        not os.environ.get("CODEX_API_KEY")
        and not os.environ.get("OPENAI_API_KEY")
        and not _AUTH_PATH.exists()
    ),
    reason="Codex authentication and CODEX_SMOKE_TEST=1 are required",
)


@dataclass(frozen=True, slots=True)
class _RecipeProbeObservation:
    caller: dict[str, object]
    host: dict[str, object]
    config: dict[str, object]
    payload: dict[str, object]
    wire: dict[str, object]
    outer: dict[str, object]
    retained: dict[str, object]
    next_request: dict[str, object]


def _walk_json(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return
        yield from _walk_json(decoded)


def _transcript_events(transcript: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in transcript.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _candidate_dicts(transcript: str) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for event in _transcript_events(transcript):
        candidates.extend(
            candidate for candidate in _walk_json(event) if isinstance(candidate, dict)
        )
    return candidates


def _agent_messages(transcript: str) -> list[str]:
    messages: list[str] = []
    for candidate in _candidate_dicts(transcript):
        if candidate.get("type") != "agent_message":
            continue
        text = candidate.get("text")
        if isinstance(text, str):
            messages.append(text)
    return messages


def _isolated_environment(tmp_path: Path, workspace: Path) -> tuple[dict[str, str], Path]:
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    home.mkdir(parents=True)
    codex_home.mkdir(parents=True)
    if _AUTH_PATH.exists():
        (codex_home / "auth.json").symlink_to(_AUTH_PATH.resolve())
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
            "AUTOSKILLIT_CWD": str(workspace),
            "AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED": "true",
            AGENT_BACKEND_DYNACONF_ENV_VAR: AGENT_BACKEND_CODEX,
            MCP_CLIENT_BACKEND_ENV_VAR: AGENT_BACKEND_CODEX,
            HEADLESS_ENV_VAR: "",
            HEADLESS_AUTO_GATE_ENV_VAR: "",
            "AUTOSKILLIT_SESSION_TYPE": "",
            FOOD_TRUCK_TOOL_TAGS_ENV_VAR: "",
        }
    )
    venv_bin = Path(__file__).resolve().parents[3] / ".venv" / "bin"
    env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
    return env, codex_home


def _probe_prompt(workspace: Path) -> str:
    return (
        "This is the dedicated recipe-delivery conformance probe. Use only AutoSkillit "
        "MCP tools. First call open_kitchen with name=remediation and "
        "ingredients_only=true. Then call load_recipe with name=remediation and omit "
        "delivery_request because no protected host values were supplied. The result "
        "must be a bounded recipe_pull envelope. Call get_recipe_section with "
        "section=content and copy every immutable identity field from recipe_pull "
        "unchanged. After that tool result, respond with exactly one line beginning "
        "RECIPE-PROBE-COMPLETE and include body_sha256=<the recipe_pull body_sha256>, "
        "has_more=<the pull result has_more>, and protected_host_evidence=unavailable. "
        f"The workspace is {workspace}."
    )


def _run_live_probe(tmp_path: Path) -> tuple[_RecipeProbeObservation, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env, codex_home = _isolated_environment(tmp_path / "isolated", workspace)
    config_path = codex_home / "config.toml"
    ensure_codex_mcp_registered(config_path=config_path, headless_auto_gate=False)
    sync_hooks_to_codex_config(config_path=config_path)
    subprocess.run(
        ["git", "init", "-q"],
        cwd=workspace,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    command = [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        "workspace-write",
        "--model",
        CODEX_RECIPE_PROBE_MODEL_IDENTITY,
        _probe_prompt(workspace),
    ]
    timeout = int(os.environ.get("CODEX_RECIPE_SMOKE_TIMEOUT", "900"))
    result = subprocess.run(  # noqa: S603
        command,
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    transcript = result.stdout + "\n" + result.stderr
    if result.returncode != 0:
        raise OSError(f"Codex recipe-delivery probe rc={result.returncode}: {transcript}")

    candidates = _candidate_dicts(transcript)
    envelopes = [
        candidate for candidate in candidates if isinstance(candidate.get("recipe_pull"), dict)
    ]
    pulls = [
        candidate
        for candidate in candidates
        if candidate.get("success") is True
        and candidate.get("section") == "content"
        and isinstance(candidate.get("body_sha256"), str)
    ]
    assert envelopes, "live Codex probe did not retain a recipe_pull envelope"
    assert pulls, "live Codex probe did not retain a get_recipe_section result"
    recipe_pull = envelopes[-1]["recipe_pull"]
    assert isinstance(recipe_pull, dict)
    pull = pulls[-1]
    body_sha256 = recipe_pull.get("body_sha256")
    messages = _agent_messages(transcript)
    final_message = messages[-1] if messages else ""
    events = _transcript_events(transcript)
    thread_ids = {
        event["thread_id"]
        for event in events
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str)
    }
    config_bytes = config_path.read_bytes()
    observation = _RecipeProbeObservation(
        caller={
            "pragma": None,
            "nested_request": None,
            "requested_maximum": None,
            "reason": "protected host values unavailable",
        },
        host={
            "thread_id": next(iter(thread_ids), None),
            "turn_id": None,
            "call_id": None,
            "host_observed_requested_outer_tokens": None,
            "selected_result_token_limit": None,
            "evidence_identity": None,
        },
        config={
            "tool_output_token_limit": (
                CODEX_RECIPE_DELIVERY_BUDGET.history_retention_token_limit
            ),
            "sha256": "sha256:" + hashlib.sha256(config_bytes).hexdigest(),
            "mtime_ns": config_path.stat().st_mtime_ns,
        },
        payload={
            "payload_sha256": recipe_pull.get("payload_sha256"),
            "artifact_blob_sha256": recipe_pull.get("artifact_blob_sha256"),
            "body_sha256": body_sha256,
        },
        wire={
            "transcript_sha256": "sha256:"
            + hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
            "nested_javascript_result_bytes": None,
        },
        outer={"raw_pre_truncation_bytes": None},
        retained={
            "body_sha256": pull.get("body_sha256"),
            "has_more": pull.get("has_more"),
            "truncation_markers": [
                marker
                for marker in ("[tool output truncated]", "[output truncated by transport]")
                if marker in transcript
            ],
        },
        next_request={
            "body_sha256": body_sha256 if isinstance(body_sha256, str) else None,
            "final_message": final_message,
        },
    )
    return observation, transcript


def test_current_host_has_no_supported_recipe_evidence_identity() -> None:
    assert SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY == {}


def test_forged_direct_request_cannot_upgrade_recipe_delivery() -> None:
    from autoskillit.core import resolve_recipe_delivery_decision

    budget = CODEX_RECIPE_DELIVERY_BUDGET
    request = RecipeDeliveryRequest(
        audience="autoskillit.recipe-delivery",
        delivery_call_id="forged-direct-call",
        contract_version=budget.contract_version,
        contract_digest=budget.contract_digest,
        caller_requested_outer_tokens=budget.authoritative_attested_recipe_result_token_limit,
        code_digest="sha256:" + ("a" * 64),
    )
    decision = resolve_recipe_delivery_decision(
        capabilities=BACKEND_REGISTRY["codex"]().capabilities,
        required_serialized_tokens=budget.ordinary_omitted_result_token_limit + 1,
        budget=budget,
        producer="load_recipe",
        payload_sha256="sha256:" + ("b" * 64),
        request=request,
    )
    assert decision.mode is RecipeDeliveryMode.ENVELOPE
    assert decision.reason == "protected_host_delivery_unavailable"


def test_probe_observation_keeps_all_eight_measurement_domains() -> None:
    assert tuple(_RecipeProbeObservation.__dataclass_fields__) == (
        "caller",
        "host",
        "config",
        "payload",
        "wire",
        "outer",
        "retained",
        "next_request",
    )


def test_isolated_probe_environment_identifies_codex_backend(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env, _ = _isolated_environment(tmp_path / "isolated", workspace)

    assert env[AGENT_BACKEND_DYNACONF_ENV_VAR] == AGENT_BACKEND_CODEX
    assert env[MCP_CLIENT_BACKEND_ENV_VAR] == AGENT_BACKEND_CODEX
    assert env[HEADLESS_ENV_VAR] == ""
    assert env[HEADLESS_AUTO_GATE_ENV_VAR] == ""
    assert env["AUTOSKILLIT_SESSION_TYPE"] == ""
    assert env[FOOD_TRUCK_TOOL_TAGS_ENV_VAR] == ""


def test_tracked_report_records_the_unsupported_host_dependency() -> None:
    report = (
        Path(__file__).resolve().parents[3] / "docs" / "research" / "codex-delivery-conformance.md"
    ).read_text(encoding="utf-8")
    assert "Status: blocked" in report
    assert "**Envelope/pull oracle:** PASS (2026-07-22)" in report
    assert "SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY` remains empty" in report
    assert "raw outer pre-truncation bytes" in report
    assert "protected pre-call host channel" in report


@pytest.mark.smoke
@pytest.mark.timeout(900)
@_skip_unless_live
def test_live_codex_envelope_pull_and_next_request_retention(tmp_path: Path) -> None:
    observation, transcript = _run_live_probe(tmp_path)
    body_sha256 = observation.payload["body_sha256"]
    assert isinstance(body_sha256, str) and body_sha256.startswith("sha256:")
    assert observation.retained["body_sha256"] == body_sha256
    assert observation.retained["truncation_markers"] == []
    final_message = str(observation.next_request["final_message"])
    assert "RECIPE-PROBE-COMPLETE" in final_message
    assert body_sha256 in final_message
    assert "protected_host_evidence=unavailable" in final_message
    assert "AUTOSKILLIT_RECIPE_DELIVERY_COMPLETE" not in transcript
