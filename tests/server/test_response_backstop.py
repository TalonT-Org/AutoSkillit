"""Tests for the universal MCP response output-budget backstop."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import structlog

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import RESPONSE_BACKSTOP_EXEMPTION_REGISTRY
from autoskillit.execution import resolve_worst_case_delivery_bound
from autoskillit.server._response_budget import (
    RESPONSE_SPILL_METADATA_KEY,
    RESPONSE_SPILL_METADATA_KEYS,
    enforce_response_budget,
    shape_json_response,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _config() -> OutputBudgetConfig:
    return OutputBudgetConfig(
        inline_max_chars=180,
        head_chars=90,
        tail_chars=90,
        response_max_bytes=1400,
    )


def test_small_response_is_byte_identical(tmp_path):
    original = json.dumps({"success": True, "result": "small"})
    assert (
        enforce_response_budget(
            original,
            tool_name="run_skill",
            artifact_dir=tmp_path,
            config=_config(),
        )
        == original
    )
    assert list(tmp_path.iterdir()) == []


def test_oversized_json_preserves_routing_shape_and_full_artifact(tmp_path):
    original = json.dumps(
        {
            "success": False,
            "needs_retry": True,
            "session_id": "session-1",
            "result": "middle-sentinel" + ("x" * 10_000),
        }
    )
    shaped = enforce_response_budget(
        original,
        tool_name="run_skill",
        artifact_dir=tmp_path,
        config=_config(),
    )

    assert isinstance(shaped, str)
    assert len(shaped.encode()) <= _config().response_max_bytes
    data = json.loads(shaped)
    assert data["success"] is False
    assert data["needs_retry"] is True
    assert data["session_id"] == "session-1"
    metadata = data[RESPONSE_SPILL_METADATA_KEY]
    assert open(metadata["artifact_path"], encoding="utf-8").read() == original
    assert metadata["original_utf8_bytes"] == len(original.encode())
    assert set(metadata) == RESPONSE_SPILL_METADATA_KEYS
    assert metadata["projected_utf8_bytes"] == len(shaped.encode("utf-8"))


def test_plain_text_response_uses_same_type_envelope(tmp_path):
    original = "head" + ("x" * 10_000) + "tail"
    shaped = enforce_response_budget(
        original,
        tool_name="plain_tool",
        artifact_dir=tmp_path,
        config=_config(),
    )

    assert isinstance(shaped, str)
    data = json.loads(shaped)
    artifact_path = data[RESPONSE_SPILL_METADATA_KEY]["artifact_path"]
    assert open(artifact_path, encoding="utf-8").read() == original
    assert len(shaped.encode()) <= _config().response_max_bytes
    assert data[RESPONSE_SPILL_METADATA_KEY]["projected_utf8_bytes"] == len(shaped.encode("utf-8"))


def test_artifact_failure_is_fail_closed(tmp_path, monkeypatch):
    from autoskillit.server import _response_budget

    monkeypatch.setattr(
        _response_budget,
        "atomic_write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("ENOSPC")),
    )
    secret = "never-inline-when-spill-fails" * 1000
    shaped = enforce_response_budget(
        secret,
        tool_name="run_skill",
        artifact_dir=tmp_path,
        config=_config(),
    )
    assert secret not in shaped
    assert "artifact_publication_failed" in shaped


@pytest.mark.parametrize("kind", ["json", "plain"])
def test_projected_utf8_bytes_is_exact_with_multibyte_digit_boundaries(
    tmp_path, monkeypatch, kind
):
    from autoskillit.server import _response_budget

    artifact = tmp_path / "fixed-α-artifact.log"
    monkeypatch.setattr(
        _response_budget,
        "_artifact_path",
        lambda _artifact_dir, _tool_name, _content_hash=None: artifact,
    )
    original = (
        json.dumps({"success": True, "result": "界" * 10_000}, ensure_ascii=False)
        if kind == "json"
        else "界" * 10_000
    )
    config = OutputBudgetConfig(
        inline_max_chars=197,
        head_chars=90,
        tail_chars=90,
        response_max_bytes=1001,
    )

    first = enforce_response_budget(
        original,
        tool_name="deterministic_tool",
        artifact_dir=tmp_path,
        config=config,
    )
    second = enforce_response_budget(
        original,
        tool_name="deterministic_tool",
        artifact_dir=tmp_path,
        config=config,
    )

    assert first == second
    assert isinstance(first, str)
    metadata = json.loads(first)[RESPONSE_SPILL_METADATA_KEY]
    assert metadata["projected_utf8_bytes"] == len(first.encode("utf-8"))
    assert len(first.encode("utf-8")) <= config.response_max_bytes


def test_minimal_projection_has_exact_bytes_and_omission_aggregates(tmp_path, monkeypatch):
    from autoskillit.server import _response_budget

    monkeypatch.setattr(
        _response_budget,
        "_artifact_path",
        lambda _artifact_dir, _tool_name, _content_hash=None: tmp_path / "fixed.log",
    )
    original_data = {f"route_{index}": "界" * 120 for index in range(8)}
    original = json.dumps(original_data, ensure_ascii=False)
    config = OutputBudgetConfig(
        inline_max_chars=64,
        head_chars=32,
        tail_chars=32,
        response_max_bytes=620,
    )

    shaped = enforce_response_budget(
        original,
        tool_name="minimal_tool",
        artifact_dir=tmp_path,
        config=config,
    )
    repeated = enforce_response_budget(
        original,
        tool_name="minimal_tool",
        artifact_dir=tmp_path,
        config=config,
    )

    assert isinstance(shaped, str)
    assert shaped == repeated
    metadata = json.loads(shaped)[RESPONSE_SPILL_METADATA_KEY]
    assert metadata["reason"] == "minimal_projection"
    assert metadata["omitted_chars"] == 8 * 120
    assert metadata["omitted_items"] == 0
    assert metadata["artifact_path"] == str((tmp_path / "fixed.log").resolve())
    assert metadata["projected_utf8_bytes"] == len(shaped.encode("utf-8"))


def test_reserved_metadata_collision_fails_closed_with_complete_artifact(tmp_path):
    original = json.dumps({RESPONSE_SPILL_METADATA_KEY: {"forged": True}, "secret": "x" * 10_000})
    shaped = enforce_response_budget(
        original,
        tool_name="collision_tool",
        artifact_dir=tmp_path,
        config=_config(),
    )

    assert isinstance(shaped, str)
    failure = json.loads(shaped)
    assert failure["error"] == "response_budget_irreducible_shape"
    assert Path(failure["artifact_path"]).read_text() == original
    assert "x" * 100 not in shaped


def test_missing_context_preserves_small_and_fails_closed_for_large():
    small = "small"
    assert (
        enforce_response_budget(
            small,
            tool_name="missing_context_tool",
            artifact_dir=None,
            config=_config(),
        )
        == small
    )
    secret = "secret-path-/private/project" * 1000
    shaped = enforce_response_budget(
        secret,
        tool_name="missing_context_tool",
        artifact_dir=None,
        config=_config(),
    )
    assert secret not in shaped
    assert "/private/project" not in shaped
    assert "context_unavailable" in shaped


def test_nonserializable_result_fails_closed_and_emits_exact_failure(tmp_path):
    result = {"secret": object()}
    with structlog.testing.capture_logs() as logs:
        shaped = enforce_response_budget(
            result,
            tool_name="nonserializable_tool",
            artifact_dir=tmp_path,
            config=_config(),
        )

    assert shaped["success"] is False
    event = next(log for log in logs if log["event"] == "response_budget_failure")
    assert {key for key in event if key not in {"event", "log_level", "logger"}} == {
        "tool_name",
        "cause",
        "original_utf8_bytes",
    }
    assert event["cause"] == "serialization_failed"


def test_deeply_nested_object_serialization_fails_closed(tmp_path):
    payload = {"leaf": True}
    for _ in range(4000):
        payload = {"nested": payload}

    shaped = enforce_response_budget(
        payload,
        tool_name="deep_tool",
        artifact_dir=tmp_path,
        config=_config(),
    )

    assert shaped["success"] is False
    assert "serialization_failed" in shaped["error"]
    assert list(tmp_path.iterdir()) == []


def test_deeply_nested_json_string_degrades_to_plain_spill_with_artifact(tmp_path):
    depth = 4000
    original = "[" * depth + '"x"' + "]" * depth

    shaped = enforce_response_budget(
        original,
        tool_name="run_skill",
        artifact_dir=tmp_path,
        config=_config(),
    )

    assert isinstance(shaped, str)
    assert len(shaped.encode()) <= _config().response_max_bytes
    metadata = json.loads(shaped)[RESPONSE_SPILL_METADATA_KEY]
    assert metadata["reason"] == "plain_text"
    assert open(metadata["artifact_path"], encoding="utf-8").read() == original


def test_projection_recursion_failure_fails_closed_with_artifact_path(tmp_path, monkeypatch):
    from autoskillit.server import _response_budget

    def _stack_exhausted(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(_response_budget, "_project_json_object", _stack_exhausted)
    original = json.dumps({"success": True, "result": "x" * 10_000})

    shaped = enforce_response_budget(
        original,
        tool_name="run_skill",
        artifact_dir=tmp_path,
        config=_config(),
    )

    data = json.loads(shaped)
    assert data["success"] is False
    assert data["error"] == "response_budget_irreducible_shape"
    assert open(data["artifact_path"], encoding="utf-8").read() == original


def test_recipe_artifact_always_persisted(tmp_path):
    payload = {"success": True, "kitchen": "open", "content": "name: demo\n"}
    original = json.dumps(payload)
    result = enforce_response_budget(
        original,
        tool_name="open_kitchen",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        effective_delivery_token_limit=1_000_000,
    )
    artifacts = list(tmp_path.iterdir())
    assert len(artifacts) == 1
    assert artifacts[0].read_text(encoding="utf-8") == original
    data = json.loads(result)
    assert data["artifact_path"] == str(artifacts[0].resolve())
    assert data["sha256"] == hashlib.sha256(original.encode("utf-8")).hexdigest()


def test_recipe_artifact_deterministic_path(tmp_path):
    payload = {"success": True, "kitchen": "open", "content": "name: demo\n"}
    original = json.dumps(payload)
    first = enforce_response_budget(
        original,
        tool_name="open_kitchen",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        effective_delivery_token_limit=1_000_000,
    )
    second = enforce_response_budget(
        original,
        tool_name="open_kitchen",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        effective_delivery_token_limit=1_000_000,
    )
    first_data = json.loads(first)
    second_data = json.loads(second)
    assert first_data["artifact_path"] == second_data["artifact_path"]
    expected_sha256 = hashlib.sha256(original.encode("utf-8")).hexdigest()
    assert Path(first_data["artifact_path"]).name == f"open_kitchen_{expected_sha256[:16]}.log"
    assert len(list(tmp_path.iterdir())) == 1


def test_recipe_artifact_pre_published_not_rewritten(tmp_path):
    sentinel_path = tmp_path / "sentinel.log"
    sentinel_path.write_text("pre-published content", encoding="utf-8")
    payload = {
        "success": True,
        "kitchen": "open",
        "artifact_path": str(sentinel_path),
        "sha256": "f" * 64,
    }
    original = json.dumps(payload)
    result = enforce_response_budget(
        original,
        tool_name="open_kitchen",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        effective_delivery_token_limit=1_000_000,
    )
    data = json.loads(result)
    assert data["artifact_path"] == str(sentinel_path)
    assert data["sha256"] == "f" * 64
    assert [p.name for p in tmp_path.iterdir()] == ["sentinel.log"]
    assert sentinel_path.read_text(encoding="utf-8") == "pre-published content"


def test_nonpositive_response_max_bytes_is_rejected():
    with pytest.raises(ValueError, match="response_max_bytes"):
        OutputBudgetConfig(response_max_bytes=0)
    with pytest.raises(ValueError, match="response_max_bytes"):
        OutputBudgetConfig(response_max_bytes=-10)


def test_spill_and_failure_telemetry_is_exact_and_path_free(tmp_path, monkeypatch):
    from autoskillit.server import _response_budget

    secret_path = "/private/audit/secrets.log"

    def fail_publication(*_args, **_kwargs):
        raise OSError(secret_path)

    monkeypatch.setattr(_response_budget, "atomic_write", fail_publication)
    with structlog.testing.capture_logs() as logs:
        shaped = enforce_response_budget(
            "x" * 10_000,
            tool_name="tøøl/" + "x" * 100,
            artifact_dir=tmp_path,
            config=_config(),
        )

    assert secret_path not in shaped
    assert secret_path not in repr(logs)
    event = next(log for log in logs if log["event"] == "response_budget_failure")
    assert {key for key in event if key not in {"event", "log_level", "logger"}} == {
        "tool_name",
        "cause",
        "original_utf8_bytes",
    }
    assert event["cause"] == "artifact_publication_failed"
    assert event["tool_name"].isascii()
    assert len(event["tool_name"]) <= 64


def test_telemetry_failure_is_nonfatal(tmp_path, monkeypatch):
    from autoskillit.server import _response_budget

    monkeypatch.setattr(
        _response_budget.logger,
        "info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("telemetry failed")),
    )
    shaped = enforce_response_budget(
        "x" * 10_000,
        tool_name="telemetry_tool",
        artifact_dir=tmp_path,
        config=_config(),
    )

    assert isinstance(shaped, str)
    assert Path(json.loads(shaped)[RESPONSE_SPILL_METADATA_KEY]["artifact_path"]).exists()


def test_exemption_overage_fails_closed_and_does_not_spill(tmp_path):
    exemption = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["load_recipe"]
    original = "x" * (exemption.max_chars + 1)

    shaped = enforce_response_budget(
        original,
        tool_name="load_recipe",
        artifact_dir=tmp_path,
        config=_config(),
    )

    assert original not in shaped
    assert "exemption_ceiling_exceeded" in shaped
    assert list(tmp_path.iterdir()) == []


def test_successful_spill_and_exemption_events_have_exact_path_free_payloads(
    tmp_path, monkeypatch
):
    from autoskillit.server import _response_budget

    exemption = RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["load_recipe"]
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        _response_budget,
        "_emit_response_budget_event",
        lambda event, **payload: events.append((event, payload)),
    )
    enforce_response_budget(
        "measured recipe response",
        tool_name="load_recipe",
        artifact_dir=tmp_path,
        config=_config(),
    )
    shaped = enforce_response_budget(
        "x" * 10_000,
        tool_name="spill_tool",
        artifact_dir=tmp_path,
        config=_config(),
    )

    exemption_event = next(
        payload for event, payload in events if event == "response_budget_exemption"
    )
    assert set(exemption_event) == {
        "tool_name",
        "measurement_id",
        "original_chars",
        "original_utf8_bytes",
        "max_chars",
        "max_utf8_bytes",
    }
    assert exemption_event["measurement_id"] == exemption.measurement_id
    assert exemption_event["max_chars"] == exemption.max_chars
    assert exemption_event["max_utf8_bytes"] == exemption.max_utf8_bytes

    spill_event = next(payload for event, payload in events if event == "response_budget_spill")
    assert set(spill_event) == {
        "tool_name",
        "original_utf8_bytes",
        "projected_utf8_bytes",
    }
    assert spill_event["projected_utf8_bytes"] == len(shaped.encode("utf-8"))
    assert "artifact" not in repr(exemption_event)
    assert str(tmp_path) not in repr(spill_event)


def test_shape_json_response_under_threshold_is_passthrough(tmp_path):
    payload = {"key": "value"}
    result = shape_json_response(
        payload, tool_name="small_tool", artifact_dir=tmp_path, config=_config()
    )
    assert result == json.dumps(payload)
    assert not list(tmp_path.iterdir())


def test_shape_json_response_over_threshold_spills_with_metadata(tmp_path):
    payload = {"data": "x" * 10_000}
    result = shape_json_response(
        payload, tool_name="big_tool", artifact_dir=tmp_path, config=_config()
    )
    data = json.loads(result)
    assert RESPONSE_SPILL_METADATA_KEY in data
    artifact_path = data[RESPONSE_SPILL_METADATA_KEY]["artifact_path"]
    assert Path(artifact_path).exists()
    assert json.loads(Path(artifact_path).read_text()) == payload
    assert len(result.encode("utf-8")) <= _config().response_max_bytes


def test_plain_text_irreducible_shape_returns_failure(tmp_path):
    tiny_config = OutputBudgetConfig(
        inline_max_chars=10,
        head_chars=5,
        tail_chars=5,
        response_max_bytes=50,
    )
    result = enforce_response_budget(
        "x" * 1000,
        tool_name="tiny_tool",
        artifact_dir=tmp_path,
        config=tiny_config,
    )
    assert isinstance(result, str)
    data = json.loads(result)
    assert data.get("success") is False


def test_exempted_payload_spills_when_over_delivery_bound(tmp_path):
    """An exempted payload that fits the exemption byte ceiling but exceeds the
    backend's effective delivery token limit must be spilled, not passed through."""
    payload = {"success": True, "data": "x" * 80_000}
    original = json.dumps(payload)
    result = enforce_response_budget(
        original,
        tool_name="open_kitchen",
        artifact_dir=tmp_path,
        config=_config(),
        effective_delivery_token_limit=10_000,
    )
    assert isinstance(result, str)
    bound = 10_000 * 4
    assert len(result.encode("utf-8")) <= bound
    data = json.loads(result)
    assert data["success"] is True
    assert data["delivery_bound_spill"] is True
    metadata = data[RESPONSE_SPILL_METADATA_KEY]
    artifact_path = metadata["artifact_path"]
    assert Path(artifact_path).read_text() == original
    assert metadata["reason"] == "delivery_bound"


def _delivery_starvation_payload(
    *,
    content_chars: int,
    suggestion_chars: int,
    post_prune_step_names: list[str] | None = None,
) -> dict:
    suggestions = [
        {
            "rule": f"realistic-rule-{index}",
            "severity": "warning",
            "step": f"step_{index}",
            "message": "s" * suggestion_chars,
        }
        for index in range(60)
    ]
    step_lines = "\n".join(
        f"  step_{index}:\n    action: confirm\n    message: {'x' * 80}" for index in range(200)
    )
    content = "name: realistic-large-recipe\nsteps:\n" + step_lines + "\n"
    content += "# filler\n" * max(0, content_chars - len(content))
    payload = {
        "success": True,
        "kitchen": "open",
        "version": "1.2.3",
        "ingredients_table": "| Name | Description | Default |",
        "orchestration_rules": "Execute every routed step.",
        "stop_step_semantics": "Stop steps are terminal.",
        "errors": [],
        "suggestions": suggestions,
        "content": content[:content_chars],
    }
    if post_prune_step_names is not None:
        payload["post_prune_step_names"] = list(post_prune_step_names)
    return payload


_STARVATION_STEP_NAMES = [f"step_{index}" for index in range(200)]


@pytest.mark.parametrize(
    "step_names",
    [None, _STARVATION_STEP_NAMES],
    ids=["without_post_prune_step_names", "with_post_prune_step_names"],
)
def test_delivery_bound_summary_content_starvation_by_suggestions(tmp_path, step_names):
    payload = _delivery_starvation_payload(
        content_chars=100_000, suggestion_chars=750, post_prune_step_names=step_names
    )
    original = json.dumps(payload)

    result = enforce_response_budget(
        original,
        tool_name="open_kitchen",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        effective_delivery_token_limit=10_000,
    )

    assert isinstance(result, str)
    assert len(result.encode("utf-8")) <= 40_000
    data = json.loads(result)
    assert len(data["content"]) > 0
    if step_names is not None:
        for step_name in step_names:
            assert step_name in data["content"]


@pytest.mark.parametrize(
    "step_names",
    [None, _STARVATION_STEP_NAMES],
    ids=["without_post_prune_step_names", "with_post_prune_step_names"],
)
def test_delivery_bound_summary_budget_reallocation_after_projection(tmp_path, step_names):
    payload = _delivery_starvation_payload(
        content_chars=30_000, suggestion_chars=850, post_prune_step_names=step_names
    )
    original = json.dumps(payload)

    result = enforce_response_budget(
        original,
        tool_name="open_kitchen",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        effective_delivery_token_limit=10_000,
    )

    assert isinstance(result, str)
    data = json.loads(result)
    assert len(data["content"]) > 0
    assert len(result.encode("utf-8")) <= 40_000
    if step_names is not None:
        for step_name in step_names:
            assert step_name in data["content"]


def test_zero_delivery_limit_uses_worst_case_bound(tmp_path):
    payload = {"success": True, "content": "x" * 80_000}
    original = json.dumps(payload)

    result = enforce_response_budget(
        original,
        tool_name="open_kitchen",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        effective_delivery_token_limit=0,
    )

    assert isinstance(result, str)
    bound = resolve_worst_case_delivery_bound() * 4
    assert len(result.encode("utf-8")) <= bound
    data = json.loads(result)
    assert data["delivery_bound_spill"] is True
    assert data[RESPONSE_SPILL_METADATA_KEY]["reason"] == "delivery_bound"


def test_delivery_bound_summary_preserves_operational_fields(tmp_path):
    """Bounded summary must preserve success/kitchen/version/ingredients_table/
    orchestration_rules/stop_step_semantics/errors/suggestions verbatim,
    truncate content to fit, and nest spill metadata with reason='delivery_bound'
    and top-level delivery_bound_spill=True."""
    payload = {
        "success": True,
        "kitchen": "open",
        "version": "1.2.3",
        "ingredients_table": "| a |",
        "orchestration_rules": ["r1", "r2"],
        "stop_step_semantics": {"on_success": "stop"},
        "errors": [],
        "suggestions": [{"rule": "x"}],
        "diagram": "graph TD; A-->B",
        "content": "x" * 150_000,
    }
    original = json.dumps(payload)
    result = enforce_response_budget(
        original,
        tool_name="open_kitchen",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        effective_delivery_token_limit=10_000,
    )
    assert isinstance(result, str)
    bound = 10_000 * 4
    assert len(result.encode("utf-8")) <= bound
    data = json.loads(result)
    assert data["delivery_bound_spill"] is True
    assert data["success"] == payload["success"]
    assert data["kitchen"] == payload["kitchen"]
    assert data["version"] == payload["version"]
    assert data["ingredients_table"] == payload["ingredients_table"]
    assert data["orchestration_rules"] == payload["orchestration_rules"]
    assert data["stop_step_semantics"] == payload["stop_step_semantics"]
    assert data["errors"] == payload["errors"]
    assert data["suggestions"] == payload["suggestions"]
    assert data["diagram"] == payload["diagram"]
    assert data["content"].startswith("x")
    assert payload["content"].startswith(data["content"])
    metadata = data[RESPONSE_SPILL_METADATA_KEY]
    assert metadata["reason"] == "delivery_bound"
    assert set(metadata) == RESPONSE_SPILL_METADATA_KEYS
    assert Path(metadata["artifact_path"]).read_text() == original


def test_delivery_bound_summary_small_bound_no_exception(tmp_path):
    """Regression guard for the REQ-026 fallback branch: when the initial
    projection lands between the bound and response_max_bytes, the bounded
    summary must return a valid envelope rather than raise."""
    payload = {
        "success": True,
        "kitchen": "open",
        "version": "1.2.3",
        "ingredients_table": "| a |",
        "orchestration_rules": ["r1", "r2"],
        "stop_step_semantics": {"on_success": "stop"},
        "errors": [],
        "suggestions": [{"rule": "x"}],
        "diagram": "graph TD; A-->B",
        "content": "x" * 30_000,
    }
    original = json.dumps(payload)
    result = enforce_response_budget(
        original,
        tool_name="open_kitchen",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        effective_delivery_token_limit=500,
    )
    assert isinstance(result, str)
    bound = 500 * 4
    assert len(result.encode("utf-8")) <= bound
    data = json.loads(result)
    assert data["delivery_bound_spill"] is True
    metadata = data[RESPONSE_SPILL_METADATA_KEY]
    assert metadata["reason"] == "delivery_bound"
    assert data["success"] is True
    assert data["kitchen"] == payload["kitchen"]


def test_delivery_bound_summary_drops_diagram_when_needed(tmp_path):
    """When the bound is too small for even an empty content + diagram,
    the summary must drop diagram while preserving the operational fields."""
    payload = {
        "success": True,
        "kitchen": "open",
        "version": "1.2.3",
        "ingredients_table": "| a |",
        "orchestration_rules": ["r1", "r2"],
        "stop_step_semantics": {"on_success": "stop"},
        "errors": [],
        "suggestions": [],
        "diagram": "D" * 3_000,
        "content": "x" * 50_000,
    }
    original = json.dumps(payload)
    result = enforce_response_budget(
        original,
        tool_name="open_kitchen",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        effective_delivery_token_limit=500,
    )
    assert isinstance(result, str)
    bound = 500 * 4
    assert len(result.encode("utf-8")) <= bound
    data = json.loads(result)
    assert "diagram" not in data
    assert data["delivery_bound_spill"] is True
    assert data["success"] is True
    assert data["kitchen"] == payload["kitchen"]
    metadata = data[RESPONSE_SPILL_METADATA_KEY]
    assert metadata["reason"] == "delivery_bound"


def test_over_ceiling_payload_fails_even_when_over_delivery_bound(tmp_path):
    """Pin restored ordering: an over-ceiling exempted payload must fail with
    exemption_ceiling_exceeded, not route to delivery-bound spill."""
    payload = {"success": True, "content": "x" * 190_000}
    original = json.dumps(payload)
    result = enforce_response_budget(
        original,
        tool_name="open_kitchen",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        effective_delivery_token_limit=10_000,
    )
    assert isinstance(result, str)
    data = json.loads(result)
    assert data["success"] is False
    assert data["error"] == "response_budget_exemption_ceiling_exceeded"
    assert RESPONSE_SPILL_METADATA_KEY not in data


def test_non_exempted_projection_capped_at_delivery_bound(tmp_path):
    """REQ-023 pin: non-exempted projection must be capped at min(
    response_max_bytes, effective_delivery_token_limit * 4)."""
    payload = {"data": "y" * 150_000}
    original = json.dumps(payload)
    result = enforce_response_budget(
        original,
        tool_name="run_skill",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        effective_delivery_token_limit=500,
    )
    assert isinstance(result, str)
    bound = 500 * 4
    assert len(result.encode("utf-8")) <= bound
    data = json.loads(result)
    assert RESPONSE_SPILL_METADATA_KEY in data


def test_delivery_bound_summary_projects_oversized_preserved_fields(tmp_path):
    """REQ-026/REQ-027 rung-4 pin: preserved fields must stay present even
    when they alone exceed the bound — the ladder projects values, never drops
    keys."""
    payload = {
        "success": True,
        "kitchen": "open",
        "version": "1.2.3",
        "ingredients_table": "R" * 60_000,
        "orchestration_rules": ["r1"],
        "stop_step_semantics": {"on_success": "stop"},
        "errors": [],
        "suggestions": [],
        "content": "x" * 40_000,
    }
    original = json.dumps(payload)
    result = enforce_response_budget(
        original,
        tool_name="open_kitchen",
        artifact_dir=tmp_path,
        config=OutputBudgetConfig(),
        effective_delivery_token_limit=10_000,
    )
    assert isinstance(result, str)
    bound = 10_000 * 4
    assert len(result.encode("utf-8")) <= bound
    data = json.loads(result)
    assert data["delivery_bound_spill"] is True
    for key in (
        "success",
        "kitchen",
        "version",
        "ingredients_table",
        "orchestration_rules",
        "stop_step_semantics",
        "errors",
        "suggestions",
    ):
        assert key in data, f"preserved key {key!r} missing"
    assert isinstance(data["ingredients_table"], str)
    assert len(data["ingredients_table"]) < 60_000
    metadata = data[RESPONSE_SPILL_METADATA_KEY]
    assert metadata["reason"] == "delivery_bound"
    assert set(metadata) == RESPONSE_SPILL_METADATA_KEYS
    assert Path(metadata["artifact_path"]).read_text() == original
