"""Model resolution for headless launches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from autoskillit.core import (
    LaunchValueSource,
    LaunchValueSourceKind,
    ModelIdentity,
    ModelPinResolution,
    get_logger,
    is_feature_enabled,
)

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig

logger = get_logger(__name__)


def resolve_model_pin(
    step_model: str,
    config: AutomationConfig,
    *,
    step_name: str = "",
    recipe_name: str = "",
    caller_key_path: str = "run_skill.model",
) -> ModelPinResolution:
    """Resolve the model to launch with, in descending priority order."""
    profiles_enabled = is_feature_enabled(
        "providers",
        config.features,
        experimental_enabled=config.experimental_enabled,
    )

    def resolved_pin(
        value: str,
        source: LaunchValueSource,
        *,
        tier: str,
    ) -> ModelPinResolution | None:
        if not value:
            return None
        profile = config.providers.profiles.get(value) if profiles_enabled else None
        if profile is None:
            logger.debug("model_resolved", tier=tier, model=value)
            return ModelPinResolution(value, source)
        profile_model = profile.get("ANTHROPIC_MODEL")
        if not profile_model:
            logger.warning(
                "provider_profile_no_model",
                profile=value,
                hint="Add ANTHROPIC_MODEL to the profile or use a literal model name",
            )
            return None
        logger.debug("model_resolved", tier=tier, model=profile_model, profile=value)
        return ModelPinResolution(profile_model, source, profile_name=value)

    candidates: list[tuple[str, LaunchValueSource, str]] = []
    if config.model.model_override:
        candidates.append(
            (
                config.model.model_override,
                LaunchValueSource(LaunchValueSourceKind.GLOBAL, "model.model_override"),
                "override",
            )
        )
    if recipe_name and step_name:
        if recipe_model := config.model.recipe_overrides.get(recipe_name, {}).get(step_name):
            candidates.append(
                (
                    recipe_model,
                    LaunchValueSource(
                        LaunchValueSourceKind.RECIPE,
                        f"model.recipe_overrides.{recipe_name}.{step_name}",
                    ),
                    "recipe_override",
                )
            )
    if step_name and (step_override := config.model.step_overrides.get(step_name)):
        candidates.append(
            (
                step_override,
                LaunchValueSource(LaunchValueSourceKind.STEP, f"model.step_overrides.{step_name}"),
                "step_override",
            )
        )
    if recipe_name:
        provider_models = config.providers.model_overrides.get(recipe_name, {})
        provider_key = step_name
        provider_model = provider_models.get(provider_key) if provider_key else None
        if provider_model is None:
            provider_model, provider_key = provider_models.get("*"), "*"
        if provider_model:
            candidates.append(
                (
                    provider_model,
                    LaunchValueSource(
                        LaunchValueSourceKind.RECIPE,
                        f"providers.model_overrides.{recipe_name}.{provider_key}",
                    ),
                    "provider_model_override",
                )
            )
    if step_model:
        candidates.append(
            (step_model, LaunchValueSource(LaunchValueSourceKind.CALLER, caller_key_path), "step")
        )
    if config.model.default_model:
        candidates.append(
            (
                config.model.default_model,
                LaunchValueSource(LaunchValueSourceKind.DEFAULT, "model.default_model"),
                "default",
            )
        )
    for value, source, tier in candidates:
        if pin := resolved_pin(value, source, tier=tier):
            return pin
    logger.debug("model_resolved", tier="none", model=None)
    default_key_path = f"{caller_key_path.rsplit('.', 1)[0]}.defaults"
    return ModelPinResolution(
        "", LaunchValueSource(LaunchValueSourceKind.DEFAULT, default_key_path)
    )


def resolve_model_identity(
    pin: ModelPinResolution,
    *,
    profile_name: str = "",
) -> ModelIdentity:
    """Attach provider awareness to an already-resolved model pin."""
    configured = pin.model
    if profile_name and profile_name != "anthropic":
        return ModelIdentity.for_provider(
            configured=configured,
            effective="",
            profile=profile_name,
        )
    if configured:
        return ModelIdentity.anthropic(configured)
    return ModelIdentity.unknown()
