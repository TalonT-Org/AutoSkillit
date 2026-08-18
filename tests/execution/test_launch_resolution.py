"""Declarative launch-authority and stable-contract tests."""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256

import pytest

from autoskillit.core import (
    CANONICAL_LAUNCH_DIGEST_FIELDS,
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    CmdOrigin,
    CmdSpec,
    LaunchAdapterResult,
    LaunchContractError,
    LaunchFallbackRoute,
    LaunchResolutionRequest,
    LaunchSurface,
    LaunchValueSource,
    LaunchValueSourceKind,
    ProviderBinding,
    ResolvedLaunchContract,
    SemanticLaunchPlan,
    SkillProjectionBinding,
)
from autoskillit.execution import DefaultLaunchResolver

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


GLOBAL = LaunchValueSource(LaunchValueSourceKind.GLOBAL, "agent_backend.backend")
RECIPE = LaunchValueSource(LaunchValueSourceKind.RECIPE, "recipe.backend")
STEP = LaunchValueSource(LaunchValueSourceKind.STEP, "recipe.steps.build.backend")
CALLER = LaunchValueSource(LaunchValueSourceKind.CALLER, "request.backend")
ADAPTER = LaunchValueSource(LaunchValueSourceKind.ADAPTER, "adapter.model")


def _authority(
    backend: str,
    kind: BackendAuthorityKind,
    tier: BackendAuthorityTier,
    key_path: str,
) -> BackendAuthority:
    return BackendAuthority(backend=backend, kind=kind, tier=tier, key_path=key_path)


def _request(**changes: object) -> LaunchResolutionRequest:
    values: dict[str, object] = {
        "surface": LaunchSurface.HEADLESS_SKILL,
        "authority_candidates": (
            _authority(
                "claude-code",
                BackendAuthorityKind.GLOBAL,
                BackendAuthorityTier.GLOBAL,
                "agent_backend.backend",
            ),
        ),
        "semantic_plan": SemanticLaunchPlan(
            surface=LaunchSurface.HEADLESS_SKILL,
            semantic_digest="semantic-v1",
            projection_digest="projection-v1",
        ),
        "command": "/autoskillit:rectify",
        "arguments": ("--issue", "123"),
        "cwd": "/work/repo",
        "requested_model": "sonnet",
        "requested_model_source": CALLER,
        "configured_model": "claude-sonnet-4-5",
        "configured_model_source": STEP,
        "effort": "high",
        "effort_source": STEP,
        "sandbox_mode": "workspace-write",
        "network_access": False,
        "pty_required": False,
        "inherited_fd_policy": "plugin-and-channel-b",
        "branch_identity": {"name": "feature/launch", "head": "abc123"},
        "worktree_identity": {"path": "/work/repo", "git_common": "/work/.git"},
        "executable_identity": {"path": "/usr/bin/claude", "sha256": "exe-v1"},
        "plugin_identity": {"incarnation": "plugin-v1", "tree": "tree-v1"},
        "projection_identity": {"schema": "2", "root": "rectify"},
        "artifact_paths": ("/work/plugin",),
        "quota_identity": {"account": "team", "bucket": "default"},
    }
    values.update(changes)
    return LaunchResolutionRequest(**values)  # type: ignore[arg-type]


def _adapter_result(preparation, **changes: object) -> LaunchAdapterResult:
    values: dict[str, object] = {
        "backend": preparation.selected_backend,
        "provider": preparation.provider,
        "profile": preparation.profile,
        "normalized_endpoint": preparation.normalized_endpoint,
        "physical_model": "claude-sonnet-4-5-20250929",
        "physical_model_source": ADAPTER,
        "effort": preparation.effort,
        "effort_source": preparation.effort_source,
        "semantic_digest": preparation.semantic_plan.semantic_digest,
        "adapter_digest": "adapter-v1",
        "projection_digest": preparation.semantic_plan.projection_digest,
        "cwd": preparation.cwd,
        "command": preparation.command,
        "arguments": preparation.arguments,
        "branch_identity": preparation.branch_identity,
        "worktree_identity": preparation.worktree_identity,
        "executable_identity": preparation.executable_identity,
        "plugin_identity": preparation.plugin_identity,
        "projection_identity": preparation.projection_identity,
        "artifact_paths": preparation.artifact_paths,
        "nonsecret_env": {"AUTOSKILLIT_MODE": "headless"},
        "secret_environment_keys": preparation.secret_environment_keys,
        "secret_profile_identity": preparation.secret_profile_identity,
        "skill_projection_binding": preparation.skill_projection_binding,
        "cmd_spec": CmdSpec(
            cmd=("/usr/bin/claude", "-p", preparation.command, *preparation.arguments),
            env={"AUTOSKILLIT_MODE": "headless"},
            cwd=preparation.cwd,
            origin=CmdOrigin(
                binary="/usr/bin/claude",
                mode_flags=("-p",),
                positional=(preparation.command, *preparation.arguments),
            ),
            process_idle_timeout_ms=90_000,
            inherited_fds=(9, 11),
        ),
    }
    values.update(changes)
    return LaunchAdapterResult(**values)  # type: ignore[arg-type]


class _Adapter:
    def __init__(self, result_factory=_adapter_result) -> None:
        self.calls = 0
        self.result_factory = result_factory

    def build(self, preparation):
        self.calls += 1
        return self.result_factory(preparation)


def _projection_binding() -> SkillProjectionBinding:
    digest = sha256(b"skill").hexdigest()
    semantic = sha256(b"semantic").hexdigest()
    adaptation = sha256(b"adaptation").hexdigest()
    return SkillProjectionBinding(
        root_name="rectify",
        member_names=("rectify",),
        execution_role="session",
        capability_union=frozenset(),
        source_identities={
            "rectify": {
                "origin": "bundled_extended",
                "logical_name": "rectify",
                "search_dir": None,
                "precedence": None,
            }
        },
        canonical_digests={"rectify": digest},
        projected_digests={"rectify": digest},
        semantic_digests={"rectify": semantic},
        adaptation_digests={"rectify": adaptation},
        projection_version=4,
        project_root="/work/repo",
        cwd="/work/repo",
        backend="claude-code",
        artifact_paths=("/work/plugin",),
    )


def test_resolved_launch_contract_solely_owns_projection_binding() -> None:
    resolver = DefaultLaunchResolver()
    binding = _projection_binding()
    request = _request(
        semantic_plan=SemanticLaunchPlan(
            surface=LaunchSurface.HEADLESS_SKILL,
            semantic_digest=sha256(
                json.dumps(
                    dict(binding.semantic_digests),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            projection_digest=binding.projection_digest,
        ),
        skill_projection_binding=binding,
    )

    contract = resolver.finalize(resolver.prepare(request), _Adapter())

    assert contract.skill_projection_binding is not binding
    assert contract.skill_projection_binding is not None
    assert contract.skill_projection_binding.finalized
    assert contract.skill_projection_binding.projection_digest == binding.projection_digest
    restored = ResolvedLaunchContract.from_payload(
        contract.canonical_payload,
        expected_digest=contract.digest,
    )
    assert restored.skill_projection_binding == contract.skill_projection_binding


@pytest.mark.parametrize(
    ("candidates", "expected_backend", "expected_key_path"),
    [
        (
            (
                _authority(
                    "claude",
                    BackendAuthorityKind.GLOBAL,
                    BackendAuthorityTier.GLOBAL,
                    "agent_backend.backend",
                ),
            ),
            "claude-code",
            "agent_backend.backend",
        ),
        (
            (
                _authority(
                    "claude-code",
                    BackendAuthorityKind.GLOBAL,
                    BackendAuthorityTier.GLOBAL,
                    "agent_backend.backend",
                ),
                _authority(
                    "codex",
                    BackendAuthorityKind.RECIPE,
                    BackendAuthorityTier.RECIPE,
                    "recipe.backend",
                ),
            ),
            "codex",
            "recipe.backend",
        ),
        (
            (
                _authority(
                    "codex-cli",
                    BackendAuthorityKind.CALLER,
                    BackendAuthorityTier.CALLER,
                    "request.backend",
                ),
                _authority(
                    "claude-code",
                    BackendAuthorityKind.STEP,
                    BackendAuthorityTier.STEP,
                    "recipe.steps.build.backend",
                ),
            ),
            "codex",
            "request.backend",
        ),
    ],
)
def test_explicit_authority_precedence_is_declarative(
    candidates, expected_backend: str, expected_key_path: str
) -> None:
    preparation = DefaultLaunchResolver().prepare(_request(authority_candidates=candidates))

    assert preparation.selected_backend == expected_backend
    assert preparation.effective_backend == expected_backend
    assert preparation.backend_authority.key_path == expected_key_path


@pytest.mark.parametrize(
    "metadata",
    [
        {"agent_model": "codex"},
        {"agent_subagent": "codex"},
        {"cross_skill_ref": "codex"},
        {"git_metadata_write": True},
        {"model": "sonnet"},
        {"model": "opus"},
        {"model": "haiku"},
        {"model": "gpt-5.1-codex"},
        {"model": "codex-mini"},
        {"model": "prod-profile"},
        {"ANTHROPIC_BASE_URL": "https://proxy.invalid"},
        {"feature.codex_backend": True},
    ],
)
def test_non_authorities_never_select_a_backend(metadata: dict[str, object]) -> None:
    preparation = DefaultLaunchResolver().prepare(_request(non_authority_metadata=metadata))

    assert preparation.selected_backend == "claude-code"
    assert preparation.backend_authority.kind is BackendAuthorityKind.GLOBAL


def test_missing_and_ambiguous_explicit_authority_fail_closed() -> None:
    resolver = DefaultLaunchResolver()
    with pytest.raises(LaunchContractError, match="explicit backend authority"):
        resolver.prepare(_request(authority_candidates=()))

    candidates = (
        _authority(
            "claude-code",
            BackendAuthorityKind.STEP,
            BackendAuthorityTier.STEP,
            "recipe.steps.build.backend",
        ),
        _authority(
            "codex",
            BackendAuthorityKind.STEP,
            BackendAuthorityTier.STEP,
            "overrides.build.backend",
        ),
    )
    with pytest.raises(LaunchContractError, match="ambiguous.*recipe.steps.build.backend"):
        resolver.prepare(_request(authority_candidates=candidates))


def test_provider_profile_cannot_bind_a_different_backend() -> None:
    binding = ProviderBinding(
        provider="anthropic",
        profile="production",
        required_backend="claude-code",
        normalized_endpoint="https://api.anthropic.com",
        key_path="providers.production.backend",
        provider_source=RECIPE,
        profile_source=RECIPE,
        endpoint_source=RECIPE,
    )
    candidate = _authority(
        "codex",
        BackendAuthorityKind.CALLER,
        BackendAuthorityTier.CALLER,
        "request.backend",
    )

    with pytest.raises(
        LaunchContractError, match="providers.production.backend.*claude-code.*codex"
    ):
        DefaultLaunchResolver().prepare(
            _request(authority_candidates=(candidate,), provider_binding=binding)
        )


def test_fallback_routes_must_remain_on_selected_backend() -> None:
    same_backend = LaunchFallbackRoute(
        backend="claude-code",
        provider="anthropic",
        profile="backup",
        model="claude-sonnet-4-5",
        source=RECIPE,
    )
    preparation = DefaultLaunchResolver().prepare(_request(fallback_routes=(same_backend,)))
    assert preparation.fallback_routes == (same_backend,)

    cross_backend = replace(same_backend, backend="codex")
    with pytest.raises(LaunchContractError, match="cross-backend fallback"):
        DefaultLaunchResolver().prepare(_request(fallback_routes=(cross_backend,)))


def test_finalization_builds_once_and_captures_canonical_physical_semantics() -> None:
    resolver = DefaultLaunchResolver()
    preparation = resolver.prepare(_request())
    adapter = _Adapter()

    contract = resolver.finalize(preparation, adapter)

    assert adapter.calls == 1
    assert tuple(contract.canonical_payload) == CANONICAL_LAUNCH_DIGEST_FIELDS
    assert contract.models == {
        "requested": "sonnet",
        "requested_source": CALLER.to_payload(),
        "configured": "claude-sonnet-4-5",
        "configured_source": STEP.to_payload(),
        "adapter_physical": "claude-sonnet-4-5-20250929",
        "adapter_physical_source": ADAPTER.to_payload(),
    }
    assert contract.cmd_spec.inherited_fds == ()
    command_payload = contract.canonical_payload["command"]
    assert command_payload["argv"] == (
        "/usr/bin/claude",
        "-p",
        "/autoskillit:rectify",
        "--issue",
        "123",
    )
    assert command_payload["fd_policy"] == "plugin-and-channel-b"
    assert command_payload["inherited_fd_count"] == 2
    assert "runtime_model" not in repr(contract.canonical_payload)
    assert "attempt" not in contract.canonical_payload
    assert "retry" not in contract.canonical_payload
    assert "resume" not in contract.canonical_payload


def test_digest_is_stable_and_nested_collections_are_immutable() -> None:
    resolver = DefaultLaunchResolver()
    preparation = resolver.prepare(_request())

    first = resolver.finalize(preparation, _Adapter())

    def different_ephemeral_fds(prep):
        result = _adapter_result(prep)
        return replace(
            result,
            cmd_spec=replace(result.cmd_spec, inherited_fds=(41, 43)),
        )

    second = resolver.finalize(preparation, _Adapter(different_ephemeral_fds))
    assert first.digest == second.digest
    assert first.canonical_payload == second.canonical_payload
    with pytest.raises(TypeError):
        first.nonsecret_env["MUTATE"] = "denied"  # type: ignore[index]
    with pytest.raises(TypeError):
        first.cmd_spec.env["MUTATE"] = "denied"  # type: ignore[index]
    with pytest.raises(TypeError):
        first.branch_identity["head"] = "changed"  # type: ignore[index]


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("backend", "codex"),
        ("cwd", "/other"),
        ("command", "/autoskillit:other"),
        ("arguments", ("--issue", "999")),
        ("branch_identity", {"name": "other", "head": "abc123"}),
        ("worktree_identity", {"path": "/other", "git_common": "/work/.git"}),
        ("executable_identity", {"path": "/tmp/claude", "sha256": "exe-v1"}),
        ("plugin_identity", {"incarnation": "plugin-v2", "tree": "tree-v1"}),
        ("projection_identity", {"schema": "2", "root": "other"}),
        ("artifact_paths", ("/other/plugin",)),
        ("semantic_digest", "semantic-v2"),
        ("projection_digest", "projection-v2"),
    ],
)
def test_finalization_rejects_preparation_drift(field: str, replacement: object) -> None:
    resolver = DefaultLaunchResolver()
    preparation = resolver.prepare(_request())

    def drifted(prep):
        return replace(_adapter_result(prep), **{field: replacement})

    with pytest.raises(LaunchContractError, match=field.replace("_", " ")):
        resolver.finalize(preparation, _Adapter(drifted))


def test_provider_environment_drift_fails_closed() -> None:
    binding = ProviderBinding(
        provider="anthropic",
        profile="production",
        required_backend="claude-code",
        normalized_endpoint="https://api.anthropic.com",
        key_path="providers.production.backend",
        provider_source=RECIPE,
        profile_source=RECIPE,
        endpoint_source=RECIPE,
        environment={"ANTHROPIC_BASE_URL": "https://api.anthropic.com"},
    )
    resolver = DefaultLaunchResolver()
    preparation = resolver.prepare(_request(provider_binding=binding))

    def drifted(prep):
        result = _adapter_result(prep)
        return replace(result, nonsecret_env={"ANTHROPIC_BASE_URL": "https://proxy.invalid"})

    with pytest.raises(LaunchContractError, match="provider environment"):
        resolver.finalize(preparation, _Adapter(drifted))


def test_secrets_are_digest_bound_but_never_serialized() -> None:
    secret = "sk-ant-secret-value"
    binding = ProviderBinding(
        provider="anthropic",
        profile="production",
        required_backend="claude-code",
        normalized_endpoint="https://api.anthropic.com",
        key_path="providers.production.backend",
        provider_source=RECIPE,
        profile_source=RECIPE,
        endpoint_source=RECIPE,
        secret_environment_keys=("ANTHROPIC_API_KEY",),
    )
    resolver = DefaultLaunchResolver()
    preparation = resolver.prepare(_request(provider_binding=binding))

    def with_secret(prep):
        result = _adapter_result(prep)
        env = dict(result.cmd_spec.env)
        env["ANTHROPIC_API_KEY"] = secret
        return replace(result, cmd_spec=replace(result.cmd_spec, env=env))

    contract = resolver.finalize(preparation, _Adapter(with_secret))
    serialized = contract.canonical_json

    assert secret not in serialized
    assert "ANTHROPIC_API_KEY" not in contract.cmd_spec.env
    assert contract.secret_bindings[0].value_sha256 == sha256(secret.encode()).hexdigest()
    rehydrated = resolver.rehydrate_secret_environment(
        contract, {"ANTHROPIC_API_KEY": secret}, inherited_fds=(7, 13)
    )
    assert rehydrated.env["ANTHROPIC_API_KEY"] == secret
    assert rehydrated.inherited_fds == (7, 13)
    with pytest.raises(LaunchContractError, match="digest"):
        resolver.rehydrate_secret_environment(contract, {"ANTHROPIC_API_KEY": "changed"})


def test_undeclared_credential_environment_is_rejected() -> None:
    resolver = DefaultLaunchResolver()
    preparation = resolver.prepare(_request())

    def leaking(prep):
        result = _adapter_result(prep)
        env = dict(result.cmd_spec.env)
        env["OPENAI_API_KEY"] = "raw-secret"
        return replace(result, cmd_spec=replace(result.cmd_spec, env=env))

    with pytest.raises(LaunchContractError, match="undeclared secret.*OPENAI_API_KEY"):
        resolver.finalize(preparation, _Adapter(leaking))


def test_surface_mismatch_and_unsupported_semantics_fail_before_final_launch() -> None:
    mismatched_plan = SemanticLaunchPlan(
        surface=LaunchSurface.INTERACTIVE_COOK,
        semantic_digest="semantic-v1",
        projection_digest="projection-v1",
    )
    with pytest.raises(LaunchContractError, match="launch surface"):
        DefaultLaunchResolver().prepare(_request(semantic_plan=mismatched_plan))

    resolver = DefaultLaunchResolver()
    preparation = resolver.prepare(_request())

    def unsupported(prep):
        return replace(
            _adapter_result(prep),
            unsupported_reason="headless skill cannot use interactive-only hooks",
        )

    adapter = _Adapter(unsupported)
    with pytest.raises(LaunchContractError, match="interactive-only hooks"):
        resolver.finalize(preparation, adapter)
    assert adapter.calls == 1


def test_cli_and_mcp_entrypoints_share_the_same_semantic_surface() -> None:
    resolver = DefaultLaunchResolver()
    cli = resolver.prepare(_request(non_authority_metadata={"entrypoint": "cli"}))
    mcp = resolver.prepare(_request(non_authority_metadata={"entrypoint": "mcp"}))

    cli_contract = resolver.finalize(cli, _Adapter())
    mcp_contract = resolver.finalize(mcp, _Adapter())
    assert cli.surface is LaunchSurface.HEADLESS_SKILL
    assert mcp.surface is LaunchSurface.HEADLESS_SKILL
    assert cli_contract.digest == mcp_contract.digest


def test_resume_preparation_restores_persisted_authority_without_reselection() -> None:
    resolver = DefaultLaunchResolver()
    persisted = resolver.finalize(resolver.prepare(_request()), _Adapter())

    preparation = resolver.prepare_resume(
        persisted,
        command="continue from persisted session",
        cwd=persisted.cwd,
    )
    resumed = resolver.finalize(preparation, _Adapter())

    assert preparation.backend_authority == persisted.backend_authority
    assert preparation.command == "continue from persisted session"
    assert preparation.arguments == ()
    resolver.validate_resume(persisted, resumed)


def test_resume_validation_rejects_authority_drift_before_execution() -> None:
    resolver = DefaultLaunchResolver()
    persisted = resolver.finalize(resolver.prepare(_request()), _Adapter())
    preparation = resolver.prepare_resume(
        persisted,
        command="continue from persisted session",
        cwd=persisted.cwd,
    )
    resumed = resolver.finalize(preparation, _Adapter())
    drifted = replace(
        resumed,
        backend_authority=_authority(
            "claude-code",
            BackendAuthorityKind.CALLER,
            BackendAuthorityTier.CALLER,
            "request.backend",
        ),
    )

    with pytest.raises(LaunchContractError, match="backend authority drifted"):
        resolver.validate_resume(persisted, drifted)
    with pytest.raises(LaunchContractError, match="resume cwd drifted"):
        resolver.prepare_resume(
            persisted,
            command="continue from persisted session",
            cwd="/other/worktree",
        )


def test_resolved_contract_strict_payload_round_trip_and_digest_verification() -> None:
    resolver = DefaultLaunchResolver()
    preparation = resolver.prepare(_request())
    contract = resolver.finalize(preparation, _Adapter())

    restored = ResolvedLaunchContract.from_payload(
        contract.canonical_payload,
        expected_digest=contract.digest,
    )

    assert restored == contract
    assert restored.digest == contract.digest
    with pytest.raises(LaunchContractError, match="digest mismatch"):
        ResolvedLaunchContract.from_payload(
            contract.canonical_payload,
            expected_digest="0" * 64,
        )


def _forced_adapter_result(preparation) -> LaunchAdapterResult:
    base = _adapter_result(preparation)
    return replace(
        base,
        cmd_spec=replace(base.cmd_spec, force_inactive_agent_teams=True),
    )


def test_force_inactive_intent_survives_every_cmd_spec_reconstruction() -> None:
    """Reconstruction sites must carry spec intent, not silently drop it.

    Each site below once rebuilt CmdSpec from an explicit keyword allowlist,
    which zeroes any field the allowlist predates. They now rebuild with
    ``replace``, so a new field cannot be dropped by omission.
    """
    resolver = DefaultLaunchResolver()
    preparation = resolver.prepare(_request())
    contract = resolver.finalize(preparation, _Adapter(_forced_adapter_result))

    assert contract.cmd_spec.force_inactive_agent_teams is True

    rehydrated = resolver.rehydrate_secret_environment(contract, {}, inherited_fds=(9, 11))
    assert rehydrated.force_inactive_agent_teams is True

    restored = ResolvedLaunchContract.from_payload(
        contract.canonical_payload,
        expected_digest=contract.digest,
    )
    assert restored.cmd_spec.force_inactive_agent_teams is True


def test_force_inactive_intent_defaults_false_through_reconstruction() -> None:
    resolver = DefaultLaunchResolver()
    preparation = resolver.prepare(_request())
    contract = resolver.finalize(preparation, _Adapter())

    assert contract.cmd_spec.force_inactive_agent_teams is False
    rehydrated = resolver.rehydrate_secret_environment(contract, {}, inherited_fds=(9, 11))
    assert rehydrated.force_inactive_agent_teams is False


def test_launch_digest_distinguishes_force_inactive_intent() -> None:
    """Two launches differing only in intent must not hash identically."""
    resolver = DefaultLaunchResolver()
    default_contract = resolver.finalize(resolver.prepare(_request()), _Adapter())
    forced_contract = resolver.finalize(
        resolver.prepare(_request()), _Adapter(_forced_adapter_result)
    )

    assert default_contract.digest != forced_contract.digest
