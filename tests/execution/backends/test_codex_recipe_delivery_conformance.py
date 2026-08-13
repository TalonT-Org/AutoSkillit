"""Dedicated Codex recipe-delivery conformance and live retention probe."""

from __future__ import annotations

import hashlib
import json
import operator
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
    RECIPE_SECTION_PAGINATION_VERSION,
    RECIPE_SECTION_REGISTRY_DIGEST,
    RecipeDeliveryMode,
    RecipeDeliveryRequest,
    canonical_recipe_section_json,
    load_yaml,
    recipe_section_digest,
    recipe_section_element_digest,
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

_OVERSIZED_BASE_BRANCH = "probe-" + ("structured-warning-" * 1_200)
_PROBE_CALLER_OVERRIDES = {
    "adversarial_review_level": "none",
    "base_branch": "caller-branch",
    "dispatch_id": "caller-dispatch",
    "is_fleet_dispatch": "false",
    "local_review_rounds": "999",
}


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


def _expected_authority_warnings() -> list[str]:
    return [
        "Override for server-authoritative ingredient 'adversarial_review_level' "
        "ignored — server value 'full' (from config plan.adversarial_review_level) "
        "wins; set the config key and re-call open_kitchen to change it",
        "Override for server-authoritative ingredient 'base_branch' ignored — "
        f"server value '{_OVERSIZED_BASE_BRANCH}' (from config "
        "branching.default_base_branch) wins; set the config key and re-call "
        "open_kitchen to change it",
        "Override for server-authoritative ingredient 'dispatch_id' ignored — "
        "set by the dispatch runtime at session launch, not user-configurable",
        "Override for server-authoritative ingredient 'is_fleet_dispatch' ignored — "
        "set by the dispatch runtime at session launch, not user-configurable",
        "Override for server-authoritative ingredient 'local_review_rounds' ignored — "
        "server value '17' (from config review.local_review_rounds) wins; set the "
        "config key and re-call open_kitchen to change it",
    ]


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
            "AUTOSKILLIT_DISPATCH_ID": "probe-dispatch",
        }
    )
    venv_bin = Path(__file__).resolve().parents[3] / ".venv" / "bin"
    env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
    return env, codex_home


def _write_probe_project_config(workspace: Path) -> Path:
    config_dir = workspace / ".autoskillit"
    config_dir.mkdir()
    config_path = config_dir / "config.yaml"
    config_path.write_text(
        "branching:\n"
        f"  default_base_branch: {json.dumps(_OVERSIZED_BASE_BRANCH)}\n"
        "plan:\n"
        "  adversarial_review_level: full\n"
        "review:\n"
        "  local_review_rounds: 17\n",
        encoding="utf-8",
    )
    return config_path


def _probe_prompt(workspace: Path) -> str:
    caller_overrides = json.dumps(_PROBE_CALLER_OVERRIDES, separators=(",", ":"))
    return (
        "This is the dedicated recipe-delivery conformance probe. Use only AutoSkillit "
        "MCP tools. Call open_kitchen exactly once with name=remediation, "
        f"overrides={caller_overrides}, and omit delivery_request because no protected "
        "host values were supplied. The result must be a bounded recipe_pull envelope. "
        "Call get_recipe_section with section=content, part=0, and copy every immutable "
        "identity field from recipe_pull unchanged. While has_more is true, call it again "
        "with next_part and the same identity until every content page has been returned. "
        "Then repeat that complete pagination loop with section=warnings, starting at "
        "part=0 and following next_part until has_more is false. Then respond with exactly "
        "one line beginning "
        "RECIPE-PROBE-COMPLETE and include body_sha256=<the recipe_pull body_sha256>, "
        "has_more=<the warnings pull result has_more>, and "
        "protected_host_evidence=unavailable. "
        f"The workspace is {workspace}."
    )


_RANGE_FIELDS = frozenset(
    {
        "byte_start",
        "byte_end",
        "byte_total",
        "element_start",
        "element_end",
        "element_total",
        "scalar_byte_start",
        "scalar_byte_end",
        "scalar_byte_total",
        "element_index",
        "element_sha256",
        "fragment_index",
        "fragment_count",
        "fragment_byte_start",
        "fragment_byte_end",
        "fragment_byte_total",
    }
)
_FORMAT_RANGE_FIELDS = {
    "raw-text": frozenset({"byte_start", "byte_end", "byte_total"}),
    "json-array-page": frozenset({"element_start", "element_end", "element_total"}),
    "json-scalar-page": frozenset({"scalar_byte_start", "scalar_byte_end", "scalar_byte_total"}),
    "json-element-fragment": frozenset(
        {
            "element_index",
            "element_sha256",
            "fragment_index",
            "fragment_count",
            "fragment_byte_start",
            "fragment_byte_end",
            "fragment_byte_total",
        }
    ),
}


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


def _validated_section_pages(
    candidates: list[dict[str, object]],
    recipe_pull: dict[str, object],
    *,
    section: str,
) -> list[dict[str, object]]:
    matching = [
        candidate
        for candidate in candidates
        if candidate.get("success") is True
        and candidate.get("section") == section
        and candidate.get("body_sha256") == recipe_pull.get("body_sha256")
        and candidate.get("payload_sha256") == recipe_pull.get("payload_sha256")
    ]
    by_part: dict[int, dict[str, object]] = {}
    for candidate in matching:
        part = candidate.get("part")
        if not isinstance(part, int):
            continue
        previous = by_part.get(part)
        assert previous is None, f"duplicate {section} part returned"
        by_part[part] = candidate
    assert by_part, f"live Codex probe did not retain {section} pages"

    pages = [by_part[part] for part in sorted(by_part)]
    assert sorted(by_part) == list(range(len(pages)))
    identity_keys = (
        "pagination_version",
        "section_registry_sha256",
        "section_sha256",
        "page_plan_sha256",
        "payload_sha256",
        "body_sha256",
    )
    expected_identity = {key: pages[0].get(key) for key in identity_keys}
    assert expected_identity["pagination_version"] == RECIPE_SECTION_PAGINATION_VERSION
    assert expected_identity["section_registry_sha256"] == RECIPE_SECTION_REGISTRY_DIGEST
    assert _is_sha256(expected_identity["section_sha256"])
    assert _is_sha256(expected_identity["page_plan_sha256"])

    for part, page in enumerate(pages):
        assert page.get("part") == part
        assert page.get("total_parts") == len(pages)
        assert {key: page.get(key) for key in identity_keys} == expected_identity
        content_format = page.get("content_format")
        assert isinstance(content_format, str)
        assert content_format in _FORMAT_RANGE_FIELDS
        assert _RANGE_FIELDS & page.keys() == _FORMAT_RANGE_FIELDS[content_format]
        if part < len(pages) - 1:
            assert page.get("has_more") is True
            assert page.get("next_part") == part + 1
        else:
            assert page.get("has_more") is False
            assert "next_part" not in page
    return pages


def _reconstruct_raw_section(pages: list[dict[str, object]]) -> str:
    chunks: list[str] = []
    byte_start = 0
    byte_total: int | None = None
    for page in pages:
        assert page["content_format"] == "raw-text"
        content = page["content"]
        assert isinstance(content, str)
        assert page["byte_start"] == byte_start
        byte_start += len(content.encode("utf-8"))
        assert page["byte_end"] == byte_start
        if byte_total is None:
            assert isinstance(page["byte_total"], int)
            byte_total = page["byte_total"]
        assert page["byte_total"] == byte_total
        chunks.append(content)
    assert byte_start == byte_total
    reconstructed = "".join(chunks)
    assert recipe_section_digest(reconstructed, raw=True) == pages[0]["section_sha256"]
    return reconstructed


def _reconstruct_array_section(
    pages: list[dict[str, object]],
) -> tuple[list[object], set[str], set[int]]:
    values: list[object] = []
    formats: set[str] = set()
    fragmented_element_indices: set[int] = set()
    element_total: int | None = None
    fragment_chunks: list[str] = []
    fragment_count = 0
    fragment_byte_end = 0
    fragment_byte_total = 0
    fragment_element_sha256 = ""

    for page in pages:
        content = page["content"]
        content_format = page["content_format"]
        assert isinstance(content_format, str)
        formats.add(content_format)

        if content_format == "json-array-page":
            assert isinstance(content, list)
            decoded = content  # already parsed
            assert not fragment_chunks
            assert isinstance(decoded, list)
            assert page["element_start"] == len(values)
            values.extend(decoded)
            assert page["element_end"] == len(values)
            if element_total is None:
                assert isinstance(page["element_total"], int)
                element_total = page["element_total"]
            assert page["element_total"] == element_total
            continue

        assert content_format == "json-element-fragment"
        assert isinstance(content, str)
        decoded = json.loads(content)
        assert isinstance(decoded, str)
        element_index = page["element_index"]
        assert element_index == len(values)
        assert isinstance(element_index, int)
        fragmented_element_indices.add(element_index)
        if not fragment_chunks:
            assert page["fragment_index"] == 0
            assert page["fragment_byte_start"] == 0
            assert isinstance(page["fragment_count"], int)
            assert isinstance(page["fragment_byte_total"], int)
            assert isinstance(page["element_sha256"], str)
            fragment_count = page["fragment_count"]
            fragment_byte_total = page["fragment_byte_total"]
            fragment_element_sha256 = page["element_sha256"]
        assert page["fragment_index"] == len(fragment_chunks)
        assert page["fragment_count"] == fragment_count
        assert page["fragment_byte_start"] == fragment_byte_end
        fragment_byte_end += len(decoded.encode("utf-8"))
        assert page["fragment_byte_end"] == fragment_byte_end
        assert page["fragment_byte_total"] == fragment_byte_total
        assert page["element_sha256"] == fragment_element_sha256
        fragment_chunks.append(decoded)

        if len(fragment_chunks) == fragment_count:
            assert fragment_byte_end == fragment_byte_total
            canonical_element = "".join(fragment_chunks)
            element = json.loads(canonical_element)
            assert canonical_recipe_section_json(element) == canonical_element
            assert recipe_section_element_digest(element) == fragment_element_sha256
            values.append(element)
            fragment_chunks = []
            fragment_count = 0
            fragment_byte_end = 0
            fragment_byte_total = 0
            fragment_element_sha256 = ""

    assert not fragment_chunks
    if element_total is not None:
        assert len(values) == element_total
    assert recipe_section_digest(values, raw=False) == pages[0]["section_sha256"]
    return values, formats, fragmented_element_indices


def _run_live_probe(tmp_path: Path) -> tuple[_RecipeProbeObservation, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_probe_project_config(workspace)
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
    envelopes: list[dict[str, object]] = []
    for candidate in candidates:
        candidate_pull = candidate.get("recipe_pull")
        if (
            isinstance(candidate_pull, dict)
            and candidate_pull.get("producer_tool") == "open_kitchen"
        ):
            envelopes.append(candidate)
    assert len(envelopes) == 1, (
        f"live Codex probe retained {len(envelopes)} open_kitchen pull envelopes"
    )
    recipe_pull = envelopes[0]["recipe_pull"]
    assert isinstance(recipe_pull, dict)
    body_sha256 = recipe_pull.get("body_sha256")
    content_pages = _validated_section_pages(candidates, recipe_pull, section="content")
    reconstructed_content = _reconstruct_raw_section(content_pages)
    warning_pages = _validated_section_pages(candidates, recipe_pull, section="warnings")
    reconstructed_warnings, warning_formats, fragmented_element_indices = (
        _reconstruct_array_section(warning_pages)
    )
    assert reconstructed_warnings == _expected_authority_warnings()
    assert warning_formats == {"json-array-page", "json-element-fragment"}
    assert fragmented_element_indices == {1}

    terminal_content_page = content_pages[-1]
    terminal_warning_page = warning_pages[-1]
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
            "body_sha256": terminal_content_page.get("body_sha256"),
            "reconstructed_body_sha256": "sha256:"
            + hashlib.sha256(reconstructed_content.encode("utf-8")).hexdigest(),
            "content_section_sha256": terminal_content_page.get("section_sha256"),
            "reconstructed_content_section_sha256": recipe_section_digest(
                reconstructed_content,
                raw=True,
            ),
            "content_bytes": len(reconstructed_content.encode("utf-8")),
            "has_more": terminal_warning_page.get("has_more"),
            "warnings": reconstructed_warnings,
            "warning_formats": sorted(warning_formats),
            "warning_fragmented_element_indices": sorted(fragmented_element_indices),
            "warning_section_sha256": terminal_warning_page.get("section_sha256"),
            "warning_page_plan_sha256": terminal_warning_page.get("page_plan_sha256"),
            "warning_section_registry_sha256": terminal_warning_page.get(
                "section_registry_sha256"
            ),
            "warning_terminal_has_next_part": "next_part" in terminal_warning_page,
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
    with pytest.raises(TypeError):
        operator.setitem(
            SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY,
            "unattested-runtime-mutation",
            object(),
        )


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


def test_probe_project_config_pins_server_authority_values(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    config = load_yaml(_write_probe_project_config(workspace))

    assert config == {
        "branching": {"default_base_branch": _OVERSIZED_BASE_BRANCH},
        "plan": {"adversarial_review_level": "full"},
        "review": {"local_review_rounds": 17},
    }


def test_probe_prompt_pins_one_envelope_producer_call(tmp_path: Path) -> None:
    prompt = _probe_prompt(tmp_path)

    assert "Call open_kitchen exactly once with name=remediation" in prompt
    assert "ingredients_only=true" not in prompt
    assert "load_recipe" not in prompt


def test_tracked_report_records_the_unsupported_host_dependency() -> None:
    report = (
        Path(__file__).resolve().parents[3] / "docs" / "research" / "codex-delivery-conformance.md"
    ).read_text(encoding="utf-8")
    assert "Status: blocked" in report
    assert "**Envelope/pull oracle:** PASS (2026-07-22)" in report
    assert "SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY` remains empty" in report
    assert "raw outer pre-truncation bytes" in report
    assert "protected pre-call host channel" in report
    for required in (
        "codex-recipe-delivery-v2",
        "server-authoritative",
        "`warnings`",
        "`json-array-page`",
        "`json-element-fragment`",
        "`section_sha256`",
        "`element_sha256`",
        "`page_plan_sha256`",
        "terminal omission",
    ):
        assert required in report


@pytest.mark.smoke
@pytest.mark.timeout(900)
@_skip_unless_live
def test_live_codex_envelope_pull_and_next_request_retention(tmp_path: Path) -> None:
    observation, transcript = _run_live_probe(tmp_path)
    body_sha256 = observation.payload["body_sha256"]
    assert isinstance(body_sha256, str) and body_sha256.startswith("sha256:")
    assert observation.retained["body_sha256"] == body_sha256
    assert observation.retained["reconstructed_body_sha256"] == body_sha256
    assert (
        observation.retained["reconstructed_content_section_sha256"]
        == observation.retained["content_section_sha256"]
    )
    assert observation.retained["content_section_sha256"] != body_sha256
    assert int(observation.retained["content_bytes"]) > 0
    assert observation.retained["has_more"] is False
    assert observation.retained["warnings"] == _expected_authority_warnings()
    assert observation.retained["warning_formats"] == [
        "json-array-page",
        "json-element-fragment",
    ]
    assert observation.retained["warning_fragmented_element_indices"] == [1]
    assert _is_sha256(observation.retained["warning_section_sha256"])
    assert _is_sha256(observation.retained["warning_page_plan_sha256"])
    assert (
        observation.retained["warning_section_registry_sha256"] == RECIPE_SECTION_REGISTRY_DIGEST
    )
    assert observation.retained["warning_terminal_has_next_part"] is False
    assert observation.retained["truncation_markers"] == []
    final_message = str(observation.next_request["final_message"])
    assert "RECIPE-PROBE-COMPLETE" in final_message
    assert body_sha256 in final_message
    assert "protected_host_evidence=unavailable" in final_message
    assert "AUTOSKILLIT_RECIPE_DELIVERY_COMPLETE" not in transcript
