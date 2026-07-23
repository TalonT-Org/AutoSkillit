"""Immutable recipe generations, finalization, and bounded pull contracts."""

from __future__ import annotations

import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    RECIPE_DELIVERY_SURFACE_REGISTRY,
    RecipeDeliveryAttestation,
    RecipeDeliveryEvidenceDef,
    RecipeDeliveryMode,
    RecipeDeliveryRequest,
    load_yaml,
    recipe_delivery_request_digest,
)
from autoskillit.execution import (
    CODEX_RECIPE_DELIVERY_BUDGET,
    ProtectedStoreAuthority,
    RecipeDeliveryReceiptLedger,
)
from autoskillit.execution.backends import CodexBackend
from autoskillit.server._recipe_delivery import (
    RECIPE_ARTIFACT_MAX_BLOB_BYTES,
    RECIPE_BODY_END,
    RECIPE_BODY_START,
    RECIPE_COMPLETION_SENTINEL,
    RecipeArtifactError,
    RecipeArtifactGeneration,
    _generation_dir,
    build_recipe_envelope,
    complete_finalized_recipe_response,
    finalize_recipe_delivery,
    load_recipe_artifact,
    persist_recipe_artifact,
    recipe_pull_producers,
    recipe_recreation_producers,
    retire_recipe_artifacts,
)
from autoskillit.server._response_budget import enforce_response_budget
from autoskillit.server.tools.tools_recipe import get_recipe_section

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_NOW = 1_800_000_000


def _payload(
    content: str = "name: remediation\nsteps:\n  first:\n    action: stop\n",
) -> dict[str, object]:
    return {
        "success": True,
        "valid": True,
        "content": content,
        "post_prune_step_names": ["first"],
        "orchestration_rules": "follow the graph",
        "ingredients_table": {"task": {"required": True}},
    }


def _persist(
    tmp_path: Path,
    payload: dict[str, object] | None = None,
    *,
    producer: str = "open_kitchen",
) -> RecipeArtifactGeneration:
    return persist_recipe_artifact(
        tmp_path,
        kitchen_id="kitchen-test",
        producer_tool=producer,
        recipe_name="remediation",
        payload=dict(payload or _payload()),
    )


def test_artifact_namespace_encodes_colliding_kitchen_ids_injectively(tmp_path: Path) -> None:
    first = persist_recipe_artifact(
        tmp_path,
        kitchen_id="a/b",
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    second = persist_recipe_artifact(
        tmp_path,
        kitchen_id="a?b",
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )

    assert retire_recipe_artifacts(tmp_path, kitchen_id="a/b") is True
    with pytest.raises(RecipeArtifactError):
        load_recipe_artifact(tmp_path, kitchen_id="a/b", identity=first)
    assert load_recipe_artifact(tmp_path, kitchen_id="a?b", identity=second) == _payload()


def test_artifact_persistence_rejects_blob_above_read_ceiling(tmp_path: Path) -> None:
    oversized = _payload("x" * (RECIPE_ARTIFACT_MAX_BLOB_BYTES + 1))

    with pytest.raises(RecipeArtifactError, match="exceeds persistence limit"):
        _persist(tmp_path, oversized)


def test_shared_producer_surfaces_require_identical_pull_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conflicting = dict(RECIPE_DELIVERY_SURFACE_REGISTRY)
    conflicting["conflicting_open_kitchen"] = conflicting["open_kitchen"]._replace(
        pull_eligible=False
    )
    monkeypatch.setattr(
        "autoskillit.server._recipe_delivery.RECIPE_DELIVERY_SURFACE_REGISTRY",
        conflicting,
    )

    with pytest.raises(RecipeArtifactError, match="must share pull policies"):
        recipe_pull_producers()
    with pytest.raises(RecipeArtifactError, match="must share pull policies"):
        recipe_recreation_producers()


def _remove_persisted_namespace(temp_dir: Path, *, kitchen_id: str) -> None:
    shutil.rmtree(temp_dir / "recipe-delivery" / kitchen_id)


def _build_envelope(
    tmp_path: Path, payload: dict[str, object], *, bound_bytes: int
) -> tuple[dict[str, object], RecipeArtifactGeneration]:
    generation = _persist(tmp_path, payload)
    skeleton_source = MagicMock()
    skeleton_source.recipe_name = "remediation"
    skeleton_source.active_recipe_steps = {}
    envelope = build_recipe_envelope(
        payload,
        recipe_name="remediation",
        generation=generation,
        skeleton_source=skeleton_source,
        bound_bytes=bound_bytes,
    )
    return envelope, generation


def test_same_payload_is_idempotent_and_changed_payload_is_immutable(tmp_path: Path) -> None:
    first = _persist(tmp_path)
    same = _persist(tmp_path)
    changed = _persist(tmp_path, _payload("name: remediation\nsteps: {}\n"))

    assert same == first
    assert changed.payload_sha256 != first.payload_sha256
    assert load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=first) == _payload()
    assert load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=changed) == _payload(
        "name: remediation\nsteps: {}\n"
    )


@pytest.mark.parametrize(
    "version_name",
    ["RECIPE_ARTIFACT_DESCRIPTOR_VERSION", "RECIPE_ARTIFACT_SCHEMA_VERSION"],
)
def test_generation_path_includes_version_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version_name: str
) -> None:
    first = _persist(tmp_path)
    monkeypatch.setattr(f"autoskillit.server._recipe_delivery.{version_name}", 2)

    second = _persist(tmp_path)

    assert second.payload_sha256 == first.payload_sha256
    assert second.pull_identity() != first.pull_identity()
    assert load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=first) == _payload()
    assert load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=second) == _payload()


def test_concurrent_writers_publish_one_exact_generation(tmp_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        generations = list(pool.map(lambda _: _persist(tmp_path), range(24)))
    assert generations == [generations[0]] * len(generations)
    assert (
        load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=generations[0])
        == _payload()
    )


@pytest.mark.parametrize(
    ("filename", "error"),
    [
        ("payload.json", "content-addressed payload collision"),
        ("descriptor.json", "content-addressed descriptor collision"),
    ],
)
def test_persistence_collision_checks_use_bounded_descriptor_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    error: str,
) -> None:
    _persist(tmp_path)
    target = next((tmp_path / "recipe-delivery").rglob(filename))
    target.write_bytes(target.read_bytes() + b"x")

    def _unbounded_read_forbidden(*_args, **_kwargs):
        raise AssertionError("unbounded pathlib read used during collision check")

    monkeypatch.setattr(Path, "read_bytes", _unbounded_read_forbidden)
    monkeypatch.setattr(Path, "read_text", _unbounded_read_forbidden)

    with pytest.raises(RecipeArtifactError, match=error):
        _persist(tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("producer_tool", "invalid"),
        ("payload_sha256", "sha256:" + ("0" * 64)),
        ("artifact_blob_sha256", "sha256:" + ("1" * 64)),
        ("artifact_blob_size_bytes", 1),
        ("body_sha256", "sha256:" + ("2" * 64)),
        ("body_size_bytes", 1),
    ],
)
def test_generation_identity_domains_are_independently_verified(
    tmp_path: Path, field: str, value: str | int
) -> None:
    generation = _persist(tmp_path)
    with pytest.raises(RecipeArtifactError):
        load_recipe_artifact(
            tmp_path,
            kitchen_id="kitchen-test",
            identity=replace(generation, **{field: value}),
        )


def test_generation_descriptor_read_has_server_owned_ceiling(tmp_path: Path) -> None:
    generation = _persist(tmp_path)
    descriptor_path = next((tmp_path / "recipe-delivery").rglob("descriptor.json"))
    descriptor_path.write_bytes(b"x" * 20_000)

    with pytest.raises(RecipeArtifactError, match="descriptor exceeds read limit"):
        load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=generation)


def test_non_utf8_payload_is_normalized_to_recipe_artifact_error(tmp_path: Path) -> None:
    blob = b"\xff"
    qualified_blob_sha = f"sha256:{hashlib.sha256(blob).hexdigest()}"
    payload_sha = "sha256:" + hashlib.sha256(b"autoskillit.recipe-payload.v1\0" + blob).hexdigest()
    empty_body_sha = f"sha256:{hashlib.sha256(b'').hexdigest()}"
    generation = RecipeArtifactGeneration(
        producer_tool="open_kitchen",
        recipe_name="remediation",
        descriptor_version=1,
        schema_version=1,
        payload_sha256=payload_sha,
        artifact_blob_sha256=qualified_blob_sha,
        artifact_blob_size_bytes=len(blob),
        body_sha256=empty_body_sha,
        body_size_bytes=0,
    )
    directory = _generation_dir(
        tmp_path,
        kitchen_id="kitchen-test",
        producer_tool=generation.producer_tool,
        recipe_name=generation.recipe_name,
        descriptor_version=generation.descriptor_version,
        schema_version=generation.schema_version,
        payload_sha256=generation.payload_sha256,
    )
    directory.mkdir(parents=True)
    (directory / "payload.json").write_bytes(blob)
    (directory / "descriptor.json").write_text(
        json.dumps(generation.pull_identity(), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(RecipeArtifactError, match="not valid JSON"):
        load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=generation)


def test_generation_descriptor_has_no_caller_selected_path(tmp_path: Path) -> None:
    pull = _persist(tmp_path).pull_identity()
    assert set(pull) == {
        "producer_tool",
        "recipe_name",
        "descriptor_version",
        "schema_version",
        "payload_sha256",
        "artifact_blob_sha256",
        "artifact_blob_size_bytes",
        "body_sha256",
        "body_size_bytes",
        "pull_tool",
    }
    assert not {"artifact_path", "path", "sha256"} & set(pull)
    assert recipe_pull_producers() == {"open_kitchen", "load_recipe", "get_recipe"}
    assert recipe_recreation_producers() == {"open_kitchen", "get_recipe"}


def test_kitchen_retirement_removes_only_that_namespace(tmp_path: Path) -> None:
    first = _persist(tmp_path)
    second = persist_recipe_artifact(
        tmp_path,
        kitchen_id="other-kitchen",
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    assert retire_recipe_artifacts(tmp_path, kitchen_id="kitchen-test") is True
    with pytest.raises(RecipeArtifactError):
        load_recipe_artifact(tmp_path, kitchen_id="kitchen-test", identity=first)
    with pytest.raises(RecipeArtifactError, match="namespace is retired"):
        _persist(tmp_path)
    assert (
        load_recipe_artifact(tmp_path, kitchen_id="other-kitchen", identity=second) == _payload()
    )


@pytest.mark.parametrize("kitchen_id", [".", ".."])
def test_kitchen_retirement_rejects_dot_path_components(tmp_path: Path, kitchen_id: str) -> None:
    sentinel = tmp_path / "unrelated-temp-data"
    sentinel.write_text("preserve me", encoding="utf-8")

    assert retire_recipe_artifacts(tmp_path, kitchen_id=kitchen_id) is False
    assert sentinel.read_text(encoding="utf-8") == "preserve me"


def test_codex_without_supported_host_evidence_uses_bounded_envelope(tool_ctx) -> None:
    tool_ctx.backend = CodexBackend()
    tool_ctx.kitchen_id = "codex-envelope"
    payload = _payload("x" * 50_000)

    finalized = finalize_recipe_delivery(
        payload,
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ENVELOPE
    envelope = json.loads(finalized.rendered)
    assert "content" not in envelope
    assert envelope["recipe_pull"]["payload_sha256"].startswith("sha256:")
    assert len(finalized.rendered.encode("utf-8")) <= 40_000


def test_token_dense_payload_does_not_use_four_byte_ordinary_estimate(tool_ctx) -> None:
    tool_ctx.backend = CodexBackend()
    tool_ctx.kitchen_id = "codex-token-dense"

    finalized = finalize_recipe_delivery(
        _payload("!" * 20_000),
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ENVELOPE
    assert len(finalized.rendered.encode("utf-8")) <= (
        CODEX_RECIPE_DELIVERY_BUDGET.ordinary_omitted_result_token_limit
    )


def test_envelope_priority_fields_share_multibyte_budget_safely(tmp_path: Path) -> None:
    payload = _payload()
    payload["orchestration_rules"] = "雪" * 1_000
    payload["stop_step_semantics"] = "界" * 1_000
    bound_bytes = 4_000

    envelope, _generation = _build_envelope(tmp_path, payload, bound_bytes=bound_bytes)
    rendered = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))

    assert len(rendered.encode("utf-8")) <= bound_bytes
    for key in ("orchestration_rules", "stop_step_semantics"):
        projected = envelope[key]
        assert isinstance(projected, str)
        assert projected
        assert len(projected.encode("utf-8")) < len(str(payload[key]).encode("utf-8"))
        assert projected.encode("utf-8").decode("utf-8") == projected


def test_envelope_fallbacks_follow_tight_and_extreme_bounds(tmp_path: Path) -> None:
    payload = _payload()
    payload["post_prune_step_names"] = [f"step-{index:03d}" for index in range(100)]
    generation = _persist(tmp_path, payload)
    pull_fallback = {
        "success": False,
        "error": "recipe_envelope_exceeds_delivery_bound",
        "recipe_pull": generation.pull_identity(),
    }
    error_fallback = {
        "success": False,
        "error": "recipe_envelope_exceeds_delivery_bound",
    }

    pull_bound = len(
        json.dumps(pull_fallback, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    error_bound = len(
        json.dumps(error_fallback, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )

    assert _build_envelope(tmp_path, payload, bound_bytes=pull_bound)[0] == pull_fallback
    assert _build_envelope(tmp_path, payload, bound_bytes=error_bound)[0] == error_fallback
    assert _build_envelope(tmp_path, payload, bound_bytes=2)[0] == {}
    with pytest.raises(ValueError, match="too small for a JSON object"):
        _build_envelope(tmp_path, payload, bound_bytes=1)


def test_finalizer_uses_backend_selected_recipe_budget(tool_ctx) -> None:
    selected_budget = CODEX_RECIPE_DELIVERY_BUDGET._replace(contract_digest="sha256:" + ("d" * 64))
    backend = MagicMock()
    backend.capabilities = replace(
        CodexBackend().capabilities,
        recipe_delivery_budget=selected_budget,
    )
    tool_ctx.backend = backend
    tool_ctx.kitchen_id = "selected-budget"

    finalized = finalize_recipe_delivery(
        _payload(),
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ORDINARY_INLINE
    assert finalized.decision.contract_digest == selected_budget.contract_digest


def _request() -> RecipeDeliveryRequest:
    budget = CODEX_RECIPE_DELIVERY_BUDGET
    return RecipeDeliveryRequest(
        audience="autoskillit.recipe-delivery",
        delivery_call_id="delivery-finalizer-001",
        contract_version=budget.contract_version,
        contract_digest=budget.contract_digest,
        caller_requested_outer_tokens=(budget.authoritative_attested_recipe_result_token_limit),
        code_digest="sha256:" + ("b" * 64),
    )


def _evidence() -> RecipeDeliveryEvidenceDef:
    budget = CODEX_RECIPE_DELIVERY_BUDGET
    return RecipeDeliveryEvidenceDef(
        identity="protected-finalizer-test-v1",
        host_channel="test-process-isolated-host",
        evidence_schema_version=budget.evidence_version,
        parser_version=budget.parser_version,
        cli_identity="codex-test-cli",
        selected_limit_derivation="protected-resolved-outer-limit",
        selected_result_token_limit=(budget.authoritative_attested_recipe_result_token_limit),
        contract_digest=budget.contract_digest,
    )


def _attestation(thread_id: str = "thread-finalizer") -> RecipeDeliveryAttestation:
    request = _request()
    budget = CODEX_RECIPE_DELIVERY_BUDGET
    return RecipeDeliveryAttestation(
        audience=request.audience,
        thread_id=thread_id,
        turn_id="turn-finalizer-001",
        outer_call_id="outer-finalizer-001",
        code_mode_cell_id="cell-finalizer-001",
        delivery_call_id=request.delivery_call_id,
        host_observed_requested_outer_tokens=request.caller_requested_outer_tokens,
        selected_result_token_limit=(budget.authoritative_attested_recipe_result_token_limit),
        code_digest=request.code_digest,
        request_digest=recipe_delivery_request_digest(request),
        nonce="nonce-finalizer-001",
        expires_at_unix=2_000_000_000,
        contract_version=budget.contract_version,
        contract_digest=budget.contract_digest,
        parser_version=budget.parser_version,
        evidence_version=budget.evidence_version,
        evidence_identity=_evidence().identity,
    )


def _ledger(tmp_path: Path) -> RecipeDeliveryReceiptLedger:
    return RecipeDeliveryReceiptLedger.initialize_protected(
        ProtectedStoreAuthority(
            root=tmp_path / "protected-receipts",
            security_identity="protected-finalizer-test-v1",
            local_filesystem=True,
            caller_writable=False,
            initialized_by_host=True,
        )
    )


def _protected_codex_backend() -> MagicMock:
    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities = replace(
        CodexBackend().capabilities,
        protected_recipe_delivery_capable=True,
    )
    return backend


def test_attested_finalization_commits_only_after_exact_enforcement(
    tmp_path: Path, tool_ctx
) -> None:
    tool_ctx.backend = _protected_codex_backend()
    tool_ctx.kitchen_id = "codex-attested"
    ledger = _ledger(tmp_path)
    finalized = finalize_recipe_delivery(
        _payload("x" * 50_000),
        surface="open_kitchen",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        delivery_request=_request(),
        attestation=_attestation(),
        supported_evidence=_evidence(),
        receipt_ledger=ledger,
        now_unix=_NOW,
    )

    assert finalized.decision.mode is RecipeDeliveryMode.ATTESTED_INLINE
    assert finalized.receipt_handle is not None
    assert RECIPE_BODY_START in finalized.rendered
    assert RECIPE_BODY_END in finalized.rendered
    assert RECIPE_COMPLETION_SENTINEL in finalized.rendered
    assert ledger.receipt_status("thread-finalizer") == "pending"

    enforced = enforce_response_budget(
        finalized.rendered,
        tool_name="open_kitchen",
        artifact_dir=tmp_path / "responses",
        config=OutputBudgetConfig(),
        selected_result_token_limit=finalized.decision.selected_result_token_limit,
    )
    assert (
        complete_finalized_recipe_response(finalized, enforced, now_unix=_NOW)
        == finalized.rendered
    )
    assert ledger.receipt_status("thread-finalizer") == "committed"


def test_transformed_attested_response_aborts_pending_receipt(tmp_path: Path, tool_ctx) -> None:
    tool_ctx.backend = _protected_codex_backend()
    tool_ctx.kitchen_id = "codex-abort"
    ledger = _ledger(tmp_path)
    finalized = finalize_recipe_delivery(
        _payload("x" * 50_000),
        surface="load_recipe",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        delivery_request=_request(),
        attestation=_attestation("thread-abort"),
        supported_evidence=_evidence(),
        receipt_ledger=ledger,
        now_unix=_NOW,
    )
    assert finalized.decision.mode is RecipeDeliveryMode.ATTESTED_INLINE
    transformed = "bounded replacement"
    assert complete_finalized_recipe_response(finalized, transformed) == transformed
    assert ledger.receipt_status("thread-abort") is None


def test_failed_receipt_abort_is_reported(
    tmp_path: Path, tool_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_ctx.backend = _protected_codex_backend()
    tool_ctx.kitchen_id = "codex-abort-failure"
    ledger = _ledger(tmp_path)
    finalized = finalize_recipe_delivery(
        _payload("x" * 50_000),
        surface="load_recipe",
        recipe_name="remediation",
        tool_ctx=tool_ctx,
        delivery_request=_request(),
        attestation=_attestation("thread-abort-failure"),
        supported_evidence=_evidence(),
        receipt_ledger=ledger,
        now_unix=_NOW,
    )
    assert finalized.decision.mode is RecipeDeliveryMode.ATTESTED_INLINE
    monkeypatch.setattr(RecipeDeliveryReceiptLedger, "abort", lambda *_args: False)

    completed = complete_finalized_recipe_response(finalized, "bounded replacement")

    assert json.loads(completed) == {
        "success": False,
        "error": "recipe_delivery_receipt_abort_failed",
    }
    assert ledger.receipt_status("thread-abort-failure") == "pending"


async def test_pull_tool_reads_exact_generation_and_reports_byte_offsets(
    tool_ctx_kitchen_open,
) -> None:
    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "pull-kitchen"
    expected_content = "héllo\n" * 12_000
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(expected_content),
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    chunks: list[str] = []
    expected_byte_start = 0
    part = 0
    while True:
        rendered = await get_recipe_section(section="content", part=part, **kwargs)
        assert len(rendered.encode("utf-8")) <= (
            CODEX_RECIPE_DELIVERY_BUDGET.ordinary_omitted_result_token_limit
        )
        response = json.loads(rendered)
        assert response["success"] is True
        assert response["byte_start"] == expected_byte_start
        assert response["byte_end"] == response["byte_start"] + len(
            response["content"].encode("utf-8")
        )
        assert response["byte_end"] <= response["byte_total"]
        assert response["payload_sha256"] == generation.payload_sha256
        assert response["body_sha256"] == generation.body_sha256
        chunks.append(response["content"])
        expected_byte_start = response["byte_end"]
        if not response["has_more"]:
            assert "next_part" not in response
            break
        assert response["next_part"] == part + 1
        part = response["next_part"]

    assert part > 0
    assert expected_byte_start == response["byte_total"]
    assert "".join(chunks) == expected_content


async def test_pull_tool_returns_named_step_and_rejects_unknown_section(
    tool_ctx_kitchen_open,
) -> None:
    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "pull-named-step"
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")

    named = json.loads(await get_recipe_section(section="first", **kwargs))
    assert named["success"] is True
    assert named["section"] == "first"
    assert load_yaml(named["content"]) == {"first": {"action": "stop"}}

    unknown = json.loads(await get_recipe_section(section="not_a_real_step", **kwargs))
    assert unknown == {
        "success": False,
        "error": "section_not_found",
        "section": "not_a_real_step",
    }


async def test_pull_tool_reports_malformed_named_step_yaml(tool_ctx_kitchen_open) -> None:
    tool_ctx_kitchen_open.kitchen_id = "pull-malformed-step"
    malformed = _payload("steps: [")
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=malformed,
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")

    response = json.loads(await get_recipe_section(section="first", **kwargs))

    assert response["success"] is False
    assert response["error"] == "recipe_artifact_parse_failed"


async def test_oversized_named_step_round_trips_through_continuation(
    tool_ctx_kitchen_open,
) -> None:
    tool_ctx_kitchen_open.backend = CodexBackend()
    tool_ctx_kitchen_open.kitchen_id = "pull-oversized-step"
    content = "steps:\n  giant_step:\n    note: " + ("X" * 80_000) + "\n"
    payload = _payload(content)
    payload["post_prune_step_names"] = ["giant_step"]
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=payload,
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    chunks: list[str] = []
    part = 0

    while True:
        rendered = await get_recipe_section(section="giant_step", part=part, **kwargs)
        assert len(rendered.encode("utf-8")) <= (
            CODEX_RECIPE_DELIVERY_BUDGET.ordinary_omitted_result_token_limit
        )
        response = json.loads(rendered)
        assert response["success"] is True
        chunks.append(response["content"])
        if response["has_more"] is False:
            break
        part = response["next_part"]

    assert part > 0
    reconstructed = load_yaml("".join(chunks))
    parsed = load_yaml(content)
    assert reconstructed == {"giant_step": parsed["steps"]["giant_step"]}


async def test_pull_tool_rejects_wrong_generation_identity(tool_ctx_kitchen_open) -> None:
    tool_ctx_kitchen_open.kitchen_id = "pull-wrong"
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    kwargs["artifact_blob_sha256"] = "sha256:" + ("0" * 64)
    response = json.loads(await get_recipe_section(section="content", **kwargs))
    assert response == {"success": False, "error": "invalid_recipe_artifact_identity"}


@pytest.mark.parametrize("field", ["artifact_blob_size_bytes", "body_size_bytes"])
async def test_pull_tool_rejects_forged_unbounded_identity_sizes(
    tool_ctx_kitchen_open, field: str
) -> None:
    tool_ctx_kitchen_open.kitchen_id = "pull-unbounded-identity"
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    kwargs[field] = 1_000_000_000

    response = json.loads(await get_recipe_section(section="content", **kwargs))

    assert response == {"success": False, "error": "invalid_recipe_artifact_identity"}


async def test_pull_tool_recreates_missing_exact_generation(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.server.tools.tools_recipe as tools_recipe

    tool_ctx_kitchen_open.kitchen_id = "pull-recreate"
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    _remove_persisted_namespace(
        tool_ctx_kitchen_open.temp_dir, kitchen_id=tool_ctx_kitchen_open.kitchen_id
    )
    monkeypatch.setattr(tools_recipe, "serve_recipe", lambda *_args, **_kwargs: _payload())
    monkeypatch.setattr(
        tools_recipe,
        "build_open_kitchen_recipe_payload",
        lambda data, *, version: data,
    )

    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    response = json.loads(await get_recipe_section(section="content", **kwargs))

    assert response["success"] is True
    assert response["content"] == _payload()["content"]
    assert (
        load_recipe_artifact(
            tool_ctx_kitchen_open.temp_dir,
            kitchen_id=tool_ctx_kitchen_open.kitchen_id,
            identity=generation,
        )
        == _payload()
    )


async def test_pull_tool_reports_invalid_missing_generation_recreation(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.server.tools.tools_recipe as tools_recipe

    tool_ctx_kitchen_open.kitchen_id = "pull-recreate-invalid"
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    _remove_persisted_namespace(
        tool_ctx_kitchen_open.temp_dir, kitchen_id=tool_ctx_kitchen_open.kitchen_id
    )
    monkeypatch.setattr(
        tools_recipe,
        "serve_recipe",
        lambda *_args, **_kwargs: {"valid": False, "content": "invalid"},
    )

    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    response = json.loads(await get_recipe_section(section="content", **kwargs))

    assert response == {
        "success": False,
        "error": "recipe_artifact_unavailable",
        "detail": "recreation returned invalid recipe",
    }


async def test_pull_tool_rejects_changed_recreated_generation(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    import autoskillit.server.tools.tools_recipe as tools_recipe

    tool_ctx_kitchen_open.kitchen_id = "pull-recreate-changed"
    generation = persist_recipe_artifact(
        tool_ctx_kitchen_open.temp_dir,
        kitchen_id=tool_ctx_kitchen_open.kitchen_id,
        producer_tool="open_kitchen",
        recipe_name="remediation",
        payload=_payload(),
    )
    _remove_persisted_namespace(
        tool_ctx_kitchen_open.temp_dir, kitchen_id=tool_ctx_kitchen_open.kitchen_id
    )
    monkeypatch.setattr(
        tools_recipe,
        "serve_recipe",
        lambda *_args, **_kwargs: _payload("name: remediation\nsteps: {}\n"),
    )
    monkeypatch.setattr(
        tools_recipe,
        "build_open_kitchen_recipe_payload",
        lambda data, *, version: data,
    )

    kwargs = generation.pull_identity()
    kwargs.pop("pull_tool")
    response = json.loads(await get_recipe_section(section="content", **kwargs))

    assert response == {"success": False, "error": "invalid_recipe_artifact_identity"}
