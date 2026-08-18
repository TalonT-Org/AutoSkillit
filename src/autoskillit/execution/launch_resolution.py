"""Concrete backend-authority resolver and stable launch finalizer."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256
from types import MappingProxyType

from autoskillit.core import (
    BackendAuthority,
    CmdSpec,
    CodingAgentBackend,
    LaunchAdapter,
    LaunchAdapterResult,
    LaunchContractError,
    LaunchPreparation,
    LaunchResolutionRequest,
    LaunchValueSource,
    LaunchValueSourceKind,
    ResolvedLaunchContract,
    SecretEnvironmentBinding,
    SemanticLaunchPlan,
)

__all__ = ["DefaultLaunchResolver"]


_DEFAULT_BACKEND_ALIASES = {
    "claude": "claude-code",
    "claude_code": "claude-code",
    "codex-cli": "codex",
}
_CREDENTIAL_ENV_SUFFIXES = (
    "_API_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_CREDENTIAL",
)


def _is_credential_environment_key(key: str) -> bool:
    upper = key.upper()
    return upper.endswith(_CREDENTIAL_ENV_SUFFIXES) or upper in {
        "API_KEY",
        "TOKEN",
        "SECRET",
        "PASSWORD",
    }


def _default_source() -> LaunchValueSource:
    return LaunchValueSource(LaunchValueSourceKind.DEFAULT, "backend.default")


class DefaultLaunchResolver:
    """Select only typed explicit authorities and finalize one adapter result."""

    def __init__(
        self,
        *,
        known_backends: tuple[str, ...] = ("claude-code", "codex"),
        backend_aliases: Mapping[str, str] | None = None,
    ) -> None:
        self._known_backends = frozenset(known_backends)
        aliases = dict(_DEFAULT_BACKEND_ALIASES)
        if backend_aliases is not None:
            aliases.update(backend_aliases)
        self._backend_aliases = aliases

    def _canonical_backend(self, backend: str, *, key_path: str) -> str:
        canonical = self._backend_aliases.get(backend, backend)
        if canonical not in self._known_backends:
            valid = ", ".join(sorted(self._known_backends))
            raise LaunchContractError(
                f"unknown backend {backend!r} at {key_path}; valid backends: {valid}"
            )
        return canonical

    def prepare(self, request: LaunchResolutionRequest) -> LaunchPreparation:
        if request.surface is not request.semantic_plan.surface:
            raise LaunchContractError(
                "launch surface mismatch: "
                f"request={request.surface.value}, plan={request.semantic_plan.surface.value}"
            )
        if not request.authority_candidates:
            raise LaunchContractError("launch requires explicit backend authority")

        highest_tier = max(candidate.tier for candidate in request.authority_candidates)
        highest = tuple(
            candidate
            for candidate in request.authority_candidates
            if candidate.tier is highest_tier
        )
        canonical_highest = tuple(
            (
                candidate,
                self._canonical_backend(candidate.backend, key_path=candidate.key_path),
            )
            for candidate in highest
        )
        selected_backends = {backend for _, backend in canonical_highest}
        if len(selected_backends) != 1:
            paths = ", ".join(candidate.key_path for candidate, _ in canonical_highest)
            raise LaunchContractError(f"ambiguous same-tier backend authorities at {paths}")
        selected_candidate, selected_backend = canonical_highest[0]
        selected_authority = replace(selected_candidate, backend=selected_backend)

        projection_binding = request.skill_projection_binding
        if projection_binding is not None:
            if projection_binding.backend != selected_backend:
                raise LaunchContractError(
                    "skill projection backend drifted from selected backend authority"
                )
            if projection_binding.cwd != request.cwd:
                raise LaunchContractError("skill projection cwd drifted from launch request")
            if projection_binding.projection_digest != request.semantic_plan.projection_digest:
                raise LaunchContractError("skill projection digest drifted from semantic plan")
            semantic_digest = sha256(
                json.dumps(
                    dict(projection_binding.semantic_digests),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if semantic_digest != request.semantic_plan.semantic_digest:
                raise LaunchContractError("skill semantic digest drifted from projection binding")
            if not set(projection_binding.artifact_paths).issubset(set(request.artifact_paths)):
                raise LaunchContractError("skill projection artifact paths escaped launch request")

        binding = request.provider_binding
        if binding is None:
            provider = ""
            profile = ""
            normalized_endpoint = ""
            provider_source = _default_source()
            profile_source = _default_source()
            endpoint_source = _default_source()
            provider_environment: Mapping[str, str] = {}
            secret_environment_keys: tuple[str, ...] = ()
            secret_profile_identity = f"{selected_backend}:default"
        else:
            required_backend = self._canonical_backend(
                binding.required_backend, key_path=binding.key_path
            )
            if required_backend != selected_backend:
                raise LaunchContractError(
                    f"provider binding at {binding.key_path} requires {required_backend}, "
                    f"but explicit authority selected {selected_backend}"
                )
            provider = binding.provider
            profile = binding.profile
            normalized_endpoint = binding.normalized_endpoint
            provider_source = binding.provider_source
            profile_source = binding.profile_source
            endpoint_source = binding.endpoint_source
            provider_environment = binding.environment
            secret_environment_keys = binding.secret_environment_keys
            secret_profile_identity = f"{provider}:{profile}"

        fallback_routes = []
        for route in request.fallback_routes:
            route_backend = self._canonical_backend(route.backend, key_path=route.source.key_path)
            if route_backend != selected_backend:
                raise LaunchContractError(
                    "cross-backend fallback is forbidden: "
                    f"selected {selected_backend}, route requested {route_backend}"
                )
            fallback_routes.append(replace(route, backend=route_backend))

        return LaunchPreparation(
            surface=request.surface,
            selected_backend=selected_backend,
            effective_backend=selected_backend,
            backend_authority=selected_authority,
            semantic_plan=request.semantic_plan,
            command=request.command,
            arguments=request.arguments,
            cwd=request.cwd,
            provider=provider,
            profile=profile,
            normalized_endpoint=normalized_endpoint,
            provider_source=provider_source,
            profile_source=profile_source,
            endpoint_source=endpoint_source,
            requested_model=request.requested_model,
            requested_model_source=request.requested_model_source,
            configured_model=request.configured_model,
            configured_model_source=request.configured_model_source,
            effort=request.effort,
            effort_source=request.effort_source,
            fallback_routes=tuple(fallback_routes),
            sandbox_mode=request.sandbox_mode,
            network_access=request.network_access,
            pty_required=request.pty_required,
            inherited_fd_policy=request.inherited_fd_policy,
            branch_identity=request.branch_identity,
            worktree_identity=request.worktree_identity,
            executable_identity=request.executable_identity,
            plugin_identity=request.plugin_identity,
            projection_identity=request.projection_identity,
            artifact_paths=request.artifact_paths,
            quota_identity=request.quota_identity,
            provider_environment=provider_environment,
            secret_environment_keys=secret_environment_keys,
            secret_profile_identity=secret_profile_identity,
            skill_projection_binding=request.skill_projection_binding,
        )

    def prepare_resume(
        self,
        contract: ResolvedLaunchContract,
        *,
        command: str,
        cwd: str,
    ) -> LaunchPreparation:
        """Restore a preparation from persisted authority without reselection."""
        if cwd != contract.cwd:
            raise LaunchContractError("resume cwd drifted from persisted launch contract")
        secret_profiles = {binding.profile_identity for binding in contract.secret_bindings}
        if len(secret_profiles) > 1:
            raise LaunchContractError("persisted launch contract has ambiguous secret profiles")
        return LaunchPreparation(
            surface=contract.surface,
            selected_backend=contract.selected_backend,
            effective_backend=contract.effective_backend,
            backend_authority=contract.backend_authority,
            semantic_plan=SemanticLaunchPlan(
                surface=contract.surface,
                semantic_digest=contract.semantic_digest,
                projection_digest=contract.projection_digest,
            ),
            command=command,
            arguments=(),
            cwd=contract.cwd,
            provider=contract.provider,
            profile=contract.profile,
            normalized_endpoint=contract.normalized_endpoint,
            provider_source=contract.provider_source,
            profile_source=contract.profile_source,
            endpoint_source=contract.endpoint_source,
            requested_model=contract.requested_model,
            requested_model_source=contract.requested_model_source,
            configured_model=contract.configured_model,
            configured_model_source=contract.configured_model_source,
            effort=contract.effort,
            effort_source=contract.effort_source,
            fallback_routes=contract.fallback_routes,
            sandbox_mode=contract.sandbox_mode,
            network_access=contract.network_access,
            pty_required=contract.pty_required,
            inherited_fd_policy=contract.inherited_fd_policy,
            branch_identity=contract.branch_identity,
            worktree_identity=contract.worktree_identity,
            executable_identity=contract.executable_identity,
            plugin_identity=contract.plugin_identity,
            projection_identity=contract.projection_identity,
            artifact_paths=contract.artifact_paths,
            quota_identity=contract.quota_identity,
            provider_environment={},
            secret_environment_keys=tuple(
                binding.environment_key for binding in contract.secret_bindings
            ),
            secret_profile_identity=(
                next(iter(secret_profiles)) if secret_profiles else contract.profile
            ),
            skill_projection_binding=contract.skill_projection_binding,
        )

    def backend_for(self, preparation: LaunchPreparation) -> CodingAgentBackend:
        """Resolve the selected runtime implementation at the authority boundary."""
        return self.backend_for_authority(preparation.backend_authority)

    def backend_for_authority(self, authority: BackendAuthority) -> CodingAgentBackend:
        """Resolve one typed authority without exposing the backend registry downstream."""
        from autoskillit.execution.backends import get_backend

        backend = self._canonical_backend(authority.backend, key_path=authority.key_path)
        return get_backend(backend)

    @staticmethod
    def _require_equal(field_name: str, expected: object, observed: object) -> None:
        if expected != observed:
            raise LaunchContractError(
                f"{field_name.replace('_', ' ')} drift between preparation and finalization"
            )

    def _validate_adapter_result(
        self, preparation: LaunchPreparation, result: LaunchAdapterResult
    ) -> None:
        if result.unsupported_reason:
            raise LaunchContractError(f"unsupported launch semantics: {result.unsupported_reason}")
        checks = (
            ("backend", preparation.selected_backend, result.backend),
            ("provider", preparation.provider, result.provider),
            ("profile", preparation.profile, result.profile),
            (
                "normalized_endpoint",
                preparation.normalized_endpoint,
                result.normalized_endpoint,
            ),
            (
                "semantic_digest",
                preparation.semantic_plan.semantic_digest,
                result.semantic_digest,
            ),
            (
                "projection_digest",
                preparation.semantic_plan.projection_digest,
                result.projection_digest,
            ),
            ("cwd", preparation.cwd, result.cwd),
            ("command", preparation.command, result.command),
            ("arguments", preparation.arguments, result.arguments),
            ("branch_identity", preparation.branch_identity, result.branch_identity),
            (
                "worktree_identity",
                preparation.worktree_identity,
                result.worktree_identity,
            ),
            (
                "executable_identity",
                preparation.executable_identity,
                result.executable_identity,
            ),
            ("plugin_identity", preparation.plugin_identity, result.plugin_identity),
            (
                "projection_identity",
                preparation.projection_identity,
                result.projection_identity,
            ),
            ("artifact_paths", preparation.artifact_paths, result.artifact_paths),
            (
                "skill_projection_binding",
                preparation.skill_projection_binding,
                result.skill_projection_binding,
            ),
            ("effort", preparation.effort, result.effort),
            ("effort_source", preparation.effort_source, result.effort_source),
        )
        for field_name, expected, observed in checks:
            self._require_equal(field_name, expected, observed)

        binding = result.skill_projection_binding
        if binding is not None:
            self._require_equal("projection backend", result.backend, binding.backend)
            self._require_equal("projection cwd", result.cwd, binding.cwd)
            self._require_equal(
                "projection digest",
                result.projection_digest,
                binding.projection_digest,
            )
            if not set(binding.artifact_paths).issubset(set(result.artifact_paths)):
                raise LaunchContractError(
                    "skill projection artifact paths drifted from adapter result"
                )

        self._require_equal("cwd", preparation.cwd, result.cmd_spec.cwd)
        for key, value in preparation.provider_environment.items():
            if result.nonsecret_env.get(key) != value:
                raise LaunchContractError(
                    f"provider environment drift for {key} between preparation and finalization"
                )

    def finalize(
        self, preparation: LaunchPreparation, adapter: LaunchAdapter
    ) -> ResolvedLaunchContract:
        # This is intentionally the only adapter invocation in the finalizer.
        result = adapter.build(preparation)
        self._validate_adapter_result(preparation, result)

        raw_env = dict(result.cmd_spec.env)
        if not set(preparation.secret_environment_keys).issubset(result.secret_environment_keys):
            raise LaunchContractError("adapter omitted a declared secret environment binding")
        secret_keys = tuple(sorted(set(result.secret_environment_keys)))
        secret_profile_identity = (
            result.secret_profile_identity or preparation.secret_profile_identity
        )
        secret_bindings: list[SecretEnvironmentBinding] = []
        for key in secret_keys:
            if key not in raw_env:
                raise LaunchContractError(f"required secret environment {key} is missing")
            value = raw_env.pop(key)
            secret_bindings.append(
                SecretEnvironmentBinding(
                    environment_key=key,
                    profile_identity=secret_profile_identity,
                    value_sha256=sha256(value.encode()).hexdigest(),
                )
            )
        undeclared = sorted(key for key in raw_env if _is_credential_environment_key(key))
        if undeclared:
            raise LaunchContractError(
                f"undeclared secret environment key {undeclared[0]} in adapter command"
            )
        if raw_env != dict(result.nonsecret_env):
            raise LaunchContractError(
                "nonsecret environment drift between adapter evidence and exact command"
            )

        inherited_fd_count = len(result.cmd_spec.inherited_fds)
        sanitized_spec = replace(
            result.cmd_spec,
            env=MappingProxyType(raw_env),
            is_resume=False,
            inherited_fds=(),
            force_inactive_agent_teams=result.cmd_spec.force_inactive_agent_teams,
        )
        skill_projection_binding = (
            result.skill_projection_binding.bind_launch(
                cmd_spec=sanitized_spec,
                branch_identity=result.branch_identity,
                worktree_identity=result.worktree_identity,
                executable_identity=result.executable_identity,
                plugin_identity=result.plugin_identity,
                artifact_paths=result.artifact_paths,
            )
            if result.skill_projection_binding is not None
            else None
        )
        return ResolvedLaunchContract(
            surface=preparation.surface,
            selected_backend=preparation.selected_backend,
            effective_backend=preparation.effective_backend,
            backend_authority=preparation.backend_authority,
            provider=preparation.provider,
            profile=preparation.profile,
            normalized_endpoint=preparation.normalized_endpoint,
            provider_source=preparation.provider_source,
            profile_source=preparation.profile_source,
            endpoint_source=preparation.endpoint_source,
            requested_model=preparation.requested_model,
            requested_model_source=preparation.requested_model_source,
            configured_model=preparation.configured_model,
            configured_model_source=preparation.configured_model_source,
            physical_model=result.physical_model,
            physical_model_source=result.physical_model_source,
            effort=result.effort,
            effort_source=result.effort_source,
            semantic_digest=result.semantic_digest,
            adapter_digest=result.adapter_digest,
            projection_digest=result.projection_digest,
            fallback_routes=preparation.fallback_routes,
            cwd=preparation.cwd,
            cmd_spec=sanitized_spec,
            sandbox_mode=preparation.sandbox_mode,
            network_access=preparation.network_access,
            pty_required=preparation.pty_required,
            inherited_fd_policy=preparation.inherited_fd_policy,
            inherited_fd_count=inherited_fd_count,
            executable_identity=result.executable_identity,
            plugin_identity=result.plugin_identity,
            branch_identity=result.branch_identity,
            worktree_identity=result.worktree_identity,
            projection_identity=result.projection_identity,
            artifact_paths=result.artifact_paths,
            nonsecret_env=result.nonsecret_env,
            secret_bindings=tuple(secret_bindings),
            quota_identity=preparation.quota_identity,
            skill_projection_binding=skill_projection_binding,
        )

    def validate_resume(
        self,
        expected: ResolvedLaunchContract,
        actual: ResolvedLaunchContract,
    ) -> None:
        """Prove a resumed physical attempt retains persisted portable semantics."""
        checks = (
            ("surface", expected.surface, actual.surface),
            ("selected_backend", expected.selected_backend, actual.selected_backend),
            ("effective_backend", expected.effective_backend, actual.effective_backend),
            ("backend_authority", expected.backend_authority, actual.backend_authority),
            ("provider", expected.provider, actual.provider),
            ("profile", expected.profile, actual.profile),
            ("normalized_endpoint", expected.normalized_endpoint, actual.normalized_endpoint),
            ("requested_model", expected.requested_model, actual.requested_model),
            ("configured_model", expected.configured_model, actual.configured_model),
            ("physical_model", expected.physical_model, actual.physical_model),
            ("effort", expected.effort, actual.effort),
            ("semantic_digest", expected.semantic_digest, actual.semantic_digest),
            ("projection_digest", expected.projection_digest, actual.projection_digest),
            (
                "skill_projection_binding",
                expected.skill_projection_binding,
                actual.skill_projection_binding,
            ),
            ("fallback_routes", expected.fallback_routes, actual.fallback_routes),
            ("cwd", expected.cwd, actual.cwd),
            ("sandbox_mode", expected.sandbox_mode, actual.sandbox_mode),
            ("network_access", expected.network_access, actual.network_access),
            ("pty_required", expected.pty_required, actual.pty_required),
            (
                "inherited_fd_policy",
                expected.inherited_fd_policy,
                actual.inherited_fd_policy,
            ),
            ("executable_identity", expected.executable_identity, actual.executable_identity),
            ("plugin_identity", expected.plugin_identity, actual.plugin_identity),
            ("branch_identity", expected.branch_identity, actual.branch_identity),
            ("worktree_identity", expected.worktree_identity, actual.worktree_identity),
            ("projection_identity", expected.projection_identity, actual.projection_identity),
            ("artifact_paths", expected.artifact_paths, actual.artifact_paths),
            ("secret_bindings", expected.secret_bindings, actual.secret_bindings),
            ("quota_identity", expected.quota_identity, actual.quota_identity),
        )
        for field_name, expected_value, actual_value in checks:
            if expected_value != actual_value:
                raise LaunchContractError(
                    f"resume launch {field_name.replace('_', ' ')} drifted from persisted contract"
                )

    def rehydrate_secret_environment(
        self,
        contract: ResolvedLaunchContract,
        secret_environment: Mapping[str, str],
        *,
        inherited_fds: tuple[int, ...] = (),
    ) -> CmdSpec:
        expected_keys = {binding.environment_key for binding in contract.secret_bindings}
        provided_keys = set(secret_environment)
        if expected_keys != provided_keys:
            missing = ", ".join(sorted(expected_keys - provided_keys))
            extra = ", ".join(sorted(provided_keys - expected_keys))
            raise LaunchContractError(
                f"secret environment binding mismatch (missing={missing!r}, extra={extra!r})"
            )
        for binding in contract.secret_bindings:
            observed = sha256(secret_environment[binding.environment_key].encode()).hexdigest()
            if observed != binding.value_sha256:
                raise LaunchContractError(
                    f"secret environment digest mismatch for {binding.environment_key}"
                )
        if len(inherited_fds) != contract.inherited_fd_count:
            raise LaunchContractError(
                "inherited FD count does not match the stable launch contract"
            )
        env = dict(contract.nonsecret_env)
        env.update(secret_environment)
        return replace(
            contract.cmd_spec,
            env=env,
            inherited_fds=inherited_fds,
            force_inactive_agent_teams=contract.cmd_spec.force_inactive_agent_teams,
        )
