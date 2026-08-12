"""AST guards for independent recipe-delivery provenance domains."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core import (
    AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS,
    AUTOSKILLIT_ATTESTED_META_SUPPORT,
    CLAUDE_INJECTED_CLIENT_RESULT_TOKENS,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_DELIVERY_BOUNDS = (
    Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "core" / "_delivery_bounds.py"
)


def _resolver() -> ast.FunctionDef:
    tree = ast.parse(_DELIVERY_BOUNDS.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_recipe_delivery_decision":
            return node
    pytest.fail("resolve_recipe_delivery_decision not found")


def _assigned_expression(function: ast.FunctionDef, target_name: str) -> ast.expr:
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == target_name
        ):
            return node.value
    pytest.fail(f"assignment to {target_name!r} not found")


def test_requested_and_observed_limits_have_independent_sources() -> None:
    function = _resolver()
    requested = ast.dump(_assigned_expression(function, "requested"))
    observed = ast.dump(_assigned_expression(function, "observed"))

    assert "caller_requested_outer_tokens" in requested
    assert "request" in requested
    assert "host_observed_requested_outer_tokens" not in requested

    assert "host_observed_requested_outer_tokens" in observed
    assert "attestation" in observed
    assert "caller_requested_outer_tokens" not in observed


def test_selected_limit_never_comes_from_history_or_measured_size() -> None:
    function = _resolver()
    body = ast.dump(function)
    assert "history_retention_token_limit" not in body
    assert "measured_recipe_exemption_max_utf8_bytes" not in body

    selected_values = [
        keyword.value
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        for keyword in call.keywords
        if keyword.arg == "selected_limit"
    ]
    assert selected_values, "resolver must pass an explicit selected limit on every decision path"
    for value in selected_values:
        value_dump = ast.dump(value)
        assert "required_serialized_tokens" not in value_dump
        assert "history_retention_token_limit" not in value_dump


def test_resolver_does_not_treat_wire_metadata_or_rollouts_as_authority() -> None:
    body = ast.dump(_resolver()).lower()
    for untrusted_source in ("_meta", "rollout", "trace", "tool_output_token_limit"):
        assert untrusted_source not in body


def test_attestation_gate_and_annotation_ceiling_are_independent() -> None:
    """Attested gate tokens and annotation ceiling must never be compared.

    The annotated regime (char-gated via ``exemption_ceiling_chars``) and the
    unannotated regime (token-gated via ``attested_client_gate_tokens``) are
    independent admission channels — the resolver must never cross-compare a
    char ceiling against a token count.
    """
    function = _resolver()
    # Walk the AST for Compare nodes — no comparison should involve both
    # "attested_client_gate_tokens" and "exemption_ceiling_chars".
    for node in ast.walk(function):
        if isinstance(node, ast.Compare):
            names = {
                n.attr if isinstance(n, ast.Attribute) else n.id
                for n in ast.walk(node)
                if isinstance(n, (ast.Name, ast.Attribute))
            }
            assert not (
                "attested_client_gate_tokens" in names and "exemption_ceiling_chars" in names
            ), (
                "resolver cross-compares attested gate tokens with annotation ceiling — "
                "these are independent admission channels (token vs char)"
            )


def test_resolver_validates_attestation_gate_before_trusting() -> None:
    """The resolver must validate attested_client_gate_tokens before consuming it.

    A bare non-None check is insufficient — the gate must be compared against
    the expected injected value (CLAUDE_INJECTED_CLIENT_RESULT_TOKENS) so
    arbitrary positive attestations cannot bypass the token gate.
    """
    body = ast.dump(_resolver())
    # The resolver must reference CLAUDE_INJECTED_CLIENT_RESULT_TOKENS to
    # validate the attested gate — its name (or its re-export) must appear.
    assert "CLAUDE_INJECTED_CLIENT_RESULT_TOKENS" in body, (
        "resolver does not validate attested_client_gate_tokens against "
        "CLAUDE_INJECTED_CLIENT_RESULT_TOKENS — arbitrary attestation accepted"
    )


def test_launcher_attestation_env_reaches_server_context_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Launcher-to-server attestation channel proof.

    The Claude backend launcher injects specific env vars (via
    ``_claude_host_attestation_env``). The server's
    ``initialize_host_client_attestation`` must read the exact same values
    and cache them. This test proves end-to-end that the launcher's
    attested gate tokens and annotation-support flag reach the server
    context unchanged.
    """
    from packaging.version import Version

    from autoskillit.execution.backends.claude import _claude_host_attestation_env
    from autoskillit.server._recipe_delivery import (
        initialize_host_client_attestation,
    )

    # Get what the launcher would inject for a version that supports annotations
    launcher_env = _claude_host_attestation_env(Version("2.1.197"))
    assert launcher_env  # non-empty for annotation-capable versions

    # Set the launcher's env vars
    for key, value in launcher_env.items():
        monkeypatch.setenv(key, value)

    # Read what the server context sees
    attestation = initialize_host_client_attestation()
    assert attestation is not None, "server context got None despite launcher env being set"

    # Prove exact equality: the server reads the exact values the launcher set
    assert (
        str(attestation.attested_client_gate_tokens)
        == launcher_env[AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS]
    ), "attested_client_gate_tokens mismatch between launcher and server context"
    expected_annotation = launcher_env.get(AUTOSKILLIT_ATTESTED_META_SUPPORT) == "1"
    assert attestation.annotation_support is expected_annotation, (
        "annotation_support mismatch between launcher and server context"
    )

    # Prove the attested gate is the injected value
    assert attestation.attested_client_gate_tokens == CLAUDE_INJECTED_CLIENT_RESULT_TOKENS, (
        "launcher attestation gate does not match CLAUDE_INJECTED_CLIENT_RESULT_TOKENS"
    )


def test_implementation_recipe_envelope_is_physically_required() -> None:
    """The implementation recipe's flow records make inline delivery impossible.

    After Stage F's projection removal, the implementation recipe payload
    (without finalized_recipe_projection) is ~154K serialized chars — well
    under the annotation ceiling. However, the surface_payload rendered at
    delivery time includes 651 flow records, bringing the total to ~286K
    serialized chars — far exceeding the 175,500-char annotation ceiling.
    The ledger correctly pins implementation/claude-code to ENVELOPE.
    This is not a deficiency but a physical constraint: flow records
    dominate the payload size for recipes with substantial pipeline state.
    """
    import tempfile
    import threading
    from types import SimpleNamespace
    from typing import Any as AnyType
    from typing import cast as cast_fn

    import autoskillit.server._recipe_generation as _recipe_generation
    from autoskillit.config import OutputBudgetConfig
    from autoskillit.core import (
        FinalizedRecipeProjection,
        HostClientAttestation,
        RecipeDeliveryMode,
    )
    from autoskillit.execution.backends import BACKEND_REGISTRY
    from autoskillit.pipeline.recipe_initialization import NoActiveRecipe
    from autoskillit.recipe import load_and_validate
    from autoskillit.server._recipe_delivery import (
        finalize_recipe_delivery,
        prepare_recipe_delivery_generation,
    )
    from autoskillit.server._recipe_generation import RecipeGenerationStore
    from autoskillit.server.tools._serve_helpers import build_open_kitchen_recipe_payload

    project_root = Path(__file__).resolve().parents[2]
    loaded = load_and_validate(
        "implementation",
        project_dir=project_root,
        ingredient_overrides={
            "task": "test",
            "issue_url": "https://test/1",
            "source_dir": str(project_root),
        },
        include_finalized_projection=True,
    )
    projection = loaded.pop("_finalized_projection", None)
    assert isinstance(projection, FinalizedRecipeProjection)
    payload = build_open_kitchen_recipe_payload(dict(loaded), version="0.0.0")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _recipe_generation._RECIPE_GENERATION_STORE = RecipeGenerationStore()
        tool_ctx = cast_fn(
            AnyType,
            SimpleNamespace(
                backend=BACKEND_REGISTRY["claude-code"](),
                config=SimpleNamespace(output_budget=OutputBudgetConfig()),
                kitchen_id="impl-envelope-proof",
                recipe_execution_lock=threading.RLock(),
                recipe_initialization_state=NoActiveRecipe(),
                temp_dir=tmp_path,
            ),
        )
        prepared = prepare_recipe_delivery_generation(
            payload,
            recipe_name="implementation",
            tool_ctx=tool_ctx,
            finalized_projection=projection,
        )
        attestation = HostClientAttestation(
            attested_client_gate_tokens=CLAUDE_INJECTED_CLIENT_RESULT_TOKENS,
            annotation_support=True,
        )
        finalized = finalize_recipe_delivery(
            payload,
            surface="open_kitchen",
            recipe_name="implementation",
            tool_ctx=tool_ctx,
            finalized_projection=projection,
            flow_generation=prepared.flow_generation,
            canonical_artifact_payload=prepared.canonical_artifact_payload,
            execution_snapshot=prepared.execution_snapshot,
            normalized_compile_key=prepared.normalized_compile_key,
            host_client_attestation=attestation,
        )
        # The implementation recipe must resolve to ENVELOPE — its rendered
        # payload (with flow records) exceeds the annotation ceiling.
        assert finalized.decision.mode == RecipeDeliveryMode.ENVELOPE, (
            f"implementation/claude-code should be ENVELOPE due to flow records, "
            f"got {finalized.decision.mode}"
        )
