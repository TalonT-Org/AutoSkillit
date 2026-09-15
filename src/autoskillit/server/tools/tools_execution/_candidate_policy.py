"""Resolve one configured execution candidate without launching a worker."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from autoskillit.core import (
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    LaunchValueSource,
    LaunchValueSourceKind,
    ModelPinResolution,
    ProviderBinding,
    SkillContractError,
)
from autoskillit.execution import get_backend, resolve_model_pin
from autoskillit.server.lifecycle._guards import _profile_to_env

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig, ExecutionCandidateSpec


def candidate_authority(
    primary: BackendAuthority, candidate: ExecutionCandidateSpec | None, ordinal: int
) -> BackendAuthority:
    """The primary retains its pin; each alternative names its own backend."""
    if candidate is None:
        return primary
    return BackendAuthority(
        backend=candidate.backend,
        kind=BackendAuthorityKind.GLOBAL,
        tier=BackendAuthorityTier.GLOBAL,
        key_path=f"providers.execution_candidates.{ordinal - 1}.backend",
    )


def _configured_profile(
    config: AutomationConfig,
    *,
    step_name: str,
    recipe_name: str,
    step_provider: str,
) -> tuple[str, LaunchValueSource, bool]:
    providers = config.providers
    if recipe_name:
        recipe = providers.recipe_overrides.get(recipe_name) or {}
        if step_name and recipe.get(step_name):
            return (
                recipe[step_name],
                LaunchValueSource(
                    LaunchValueSourceKind.RECIPE,
                    f"providers.recipe_overrides.{recipe_name}.{step_name}",
                ),
                True,
            )
        if recipe.get("*"):
            return (
                recipe["*"],
                LaunchValueSource(
                    LaunchValueSourceKind.RECIPE,
                    f"providers.recipe_overrides.{recipe_name}.*",
                ),
                True,
            )
        if step_name and providers.step_overrides.get(step_name):
            return (
                providers.step_overrides[step_name],
                LaunchValueSource(
                    LaunchValueSourceKind.STEP,
                    f"providers.step_overrides.{step_name}",
                ),
                True,
            )
        if providers.step_overrides.get("*"):
            return (
                providers.step_overrides["*"],
                LaunchValueSource(LaunchValueSourceKind.STEP, "providers.step_overrides.*"),
                True,
            )
    if step_provider:
        return (
            step_provider,
            LaunchValueSource(LaunchValueSourceKind.CALLER, "run_skill.step_provider"),
            True,
        )
    if providers.default_provider:
        return (
            providers.default_provider,
            LaunchValueSource(LaunchValueSourceKind.DEFAULT, "providers.default_provider"),
            False,
        )
    return (
        "",
        LaunchValueSource(LaunchValueSourceKind.DEFAULT, "backend.native_provider"),
        False,
    )


def resolve_candidate_policy(
    config: AutomationConfig,
    *,
    authority: BackendAuthority,
    candidate: ExecutionCandidateSpec | None,
    ordinal: int,
    step_name: str,
    recipe_name: str,
    step_provider: str,
    requested_model: str,
    providers_enabled: bool,
) -> tuple[ProviderBinding, ModelPinResolution, dict[str, str]]:
    """Resolve model/profile precedence and a fresh environment for one ordinal."""
    model_value = requested_model
    model_key = "run_skill.model"
    if candidate is not None and candidate.model:
        model_value = candidate.model
        model_key = f"providers.execution_candidates.{ordinal - 1}.model"
    model_pin = resolve_model_pin(
        model_value,
        config,
        step_name=step_name,
        recipe_name=recipe_name,
        caller_key_path=model_key,
    )

    if candidate is not None and candidate.profile:
        selected = candidate.profile
        source = LaunchValueSource(
            LaunchValueSourceKind.GLOBAL,
            f"providers.execution_candidates.{ordinal - 1}.profile",
        )
        explicit = True
    elif providers_enabled:
        selected, source, explicit = _configured_profile(
            config,
            step_name=step_name,
            recipe_name=recipe_name,
            step_provider=step_provider,
        )
    else:
        selected = ""
        source = LaunchValueSource(LaunchValueSourceKind.DEFAULT, "backend.native_provider")
        explicit = False

    model_profile = model_pin.profile_name if providers_enabled else ""
    if model_profile:
        if explicit and selected != model_profile:
            raise SkillContractError(
                f"Model profile {model_profile!r} conflicts with selected provider {selected!r}"
            )
        if not explicit:
            selected = model_profile
            source = model_pin.source

    backend = get_backend(authority.backend)
    native_provider = (
        "anthropic" if backend.capabilities.anthropic_provider_capable else authority.backend
    )
    if not selected:
        selected = native_provider
    if native_provider != "anthropic" and selected != native_provider:
        raise SkillContractError(
            f"Provider profile {selected!r} is incompatible with backend {authority.backend!r}"
        )

    if (
        providers_enabled
        and model_pin.source.key_path == "model.default_model"
        and selected in config.providers.profiles
    ):
        profile_model = config.providers.profiles[selected].get("ANTHROPIC_MODEL")
        if profile_model:
            model_pin = ModelPinResolution(
                profile_model,
                LaunchValueSource(
                    LaunchValueSourceKind.GLOBAL,
                    f"providers.profiles.{selected}.ANTHROPIC_MODEL",
                ),
                selected,
            )

    environment: dict[str, str] = {}
    profile = "default"
    endpoint = ""
    endpoint_source = source
    definition = config.providers.resolved_profiles.get(selected)
    if selected != "anthropic" and definition is not None:
        if definition.api_key_env and not os.environ.get(definition.api_key_env):
            raise SkillContractError(
                f"Provider profile {selected!r} requires {definition.api_key_env!r}"
            )
        environment = _profile_to_env(definition)
        profile = selected
        endpoint = definition.base_url or ""
        if endpoint:
            endpoint_source = LaunchValueSource(
                LaunchValueSourceKind.GLOBAL,
                f"providers.profiles.{selected}.base_url",
            )
    elif selected != authority.backend and selected != "anthropic":
        raise SkillContractError(f"Provider profile {selected!r} is not configured")
    secret_keys = tuple(
        sorted(
            key
            for key in environment
            if any(token in key.upper() for token in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
        )
    )
    binding = ProviderBinding(
        provider=selected,
        profile=profile,
        required_backend=authority.backend,
        normalized_endpoint=endpoint,
        key_path=source.key_path,
        provider_source=source,
        profile_source=source,
        endpoint_source=endpoint_source,
        environment={key: value for key, value in environment.items() if key not in secret_keys},
        secret_environment_keys=secret_keys,
    )
    return binding, model_pin, environment
