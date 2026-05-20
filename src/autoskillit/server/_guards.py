"""Orchestration-level gate functions for MCP tool access control."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import SessionType, extract_path_arg, get_logger, session_type
from autoskillit.pipeline import gate_error_result, headless_error_result

if TYPE_CHECKING:
    from autoskillit.config._config_dataclasses import ProviderProfileDef, ProvidersConfig

logger = get_logger(__name__)


def _get_ctx():  # type: ignore[return]
    from autoskillit.server._state import _get_ctx as _ctx_fn

    return _ctx_fn()


def _get_config():  # type: ignore[return]
    from autoskillit.server._state import _get_config as _cfg_fn

    return _cfg_fn()


def _require_orchestrator_or_higher(tool_name: str = "") -> str | None:
    """Return headless_error JSON if session is L1 (skill session); None if permitted.

    Interactive sessions (HEADLESS not set) always pass.
    Headless sessions must be L2 (orchestrator) or L3 (fleet).
    Fail-closed: unset/invalid SESSION_TYPE → SKILL → deny.
    """
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        return None

    st = session_type()
    if st in (SessionType.ORCHESTRATOR, SessionType.FLEET):
        return None

    msg = (
        f"{tool_name} cannot be called from skill sessions. "
        "Only orchestrator or fleet sessions may call this tool."
        if tool_name
        else None
    )
    return headless_error_result(msg)


def _require_orchestrator_exact(tool_name: str = "") -> str | None:
    """Return headless_error JSON if session is not exactly L2; None if permitted.

    Interactive sessions (HEADLESS not set) always pass.
    Headless sessions must be exactly L2 (orchestrator).
    L1 (skill session) and L3 (fleet) are both denied.
    """
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        return None

    st = session_type()
    if st is SessionType.ORCHESTRATOR:
        return None

    if st is SessionType.FLEET:
        msg = (
            f"{tool_name} cannot be called from {st.value} sessions. "
            f"{st.value.capitalize()} sessions have an auto-opened gate."
            " open_kitchen is unnecessary."
            if tool_name
            else None
        )
    else:
        msg = (
            f"{tool_name} cannot be called from skill sessions. "
            "Only the orchestrator may call this tool."
            if tool_name
            else None
        )
    return headless_error_result(msg)


def _require_fleet(tool_name: str = "") -> str | None:
    """Return headless_error JSON if session is not L3 (fleet); None if permitted.

    No interactive bypass — fleet is a specific orchestration level, not a headless guard.
    L1 (skill session) and L2 (orchestrator) sessions are both denied.
    """
    st = session_type()
    if st is SessionType.FLEET:
        return None

    msg = (
        f"{tool_name} requires a fleet session. Current session type is not fleet."
        if tool_name
        else None
    )
    return headless_error_result(msg)


def _require_enabled() -> str | None:
    """Return error JSON if tools are not enabled, None if OK.

    All tools are gated by default and can only be activated by the user
    typing the open_kitchen prompt. The prompt name is prefixed by Claude
    Code based on how the server was loaded (plugin vs --plugin-dir).
    This survives --dangerously-skip-permissions because MCP prompts are
    outside the permission system.
    """
    if not _get_ctx().gate.enabled:
        return gate_error_result()
    return None


def _validate_skill_command(skill_command: str) -> str | None:
    """Return error JSON if skill_command does not start with '/', None if OK."""
    if not skill_command.strip().startswith("/"):
        return gate_error_result(
            "run_skill requires a slash-command as skill_command.\n"
            f"Got: {skill_command!r}\n"
            "Expected: skill_command must start with '/' "
            "(e.g. /autoskillit:open-kitchen, /make-plan, /audit-arch).\n"
            "Prose task descriptions are not valid skill invocations."
        )
    return None


def _check_dry_walkthrough(skill_command: str, cwd: str) -> str | None:
    """If skill_command is an implement skill, verify the plan has been dry-walked.

    Returns an error JSON string if validation fails, None if OK.
    """
    tokens = skill_command.strip().split()
    if not tokens or tokens[0] not in _get_config().implement_gate.skill_names:
        return None
    skill_name = tokens[0]
    plan_path_str = extract_path_arg(skill_command)
    if plan_path_str is None:
        return gate_error_result(f"Missing plan path argument for {skill_name}")
    plan_path = Path(cwd) / plan_path_str
    if not plan_path.is_file():
        return gate_error_result(f"Plan file not found: {plan_path}")

    first_line = plan_path.read_text().split("\n", 1)[0].strip()
    if first_line != _get_config().implement_gate.marker:
        return gate_error_result(
            f"Plan has NOT been dry-walked. Run /dry-walkthrough on the plan first. "
            f"Expected first line: {_get_config().implement_gate.marker!r}, "
            f"actual: {first_line[:100]!r}"
        )

    return None


def _provider_result(
    provider: str,
    profiles: dict[str, dict[str, str | None]],
) -> tuple[str, dict[str, str]]:
    if provider == "anthropic":
        return ("anthropic", {})
    raw = profiles.get(provider, {})
    return (provider, {k: v for k, v in raw.items() if v is not None})


def _profile_to_env(profile: ProviderProfileDef) -> dict[str, str]:
    env: dict[str, str] = {}
    if profile.base_url:
        env["ANTHROPIC_BASE_URL"] = profile.base_url
    if profile.timeout_seconds is not None and profile.timeout_seconds > 0:
        env["API_TIMEOUT_MS"] = str(profile.timeout_seconds * 1000)
    if profile.api_key_env:
        val = os.environ.get(profile.api_key_env)
        if val:
            env["ANTHROPIC_API_KEY"] = val
        else:
            logger.warning("provider_api_key_env_missing", env_var_name=profile.api_key_env)
    env.update(profile.raw_env)
    return env


def _resolve_provider_profile(
    step_name: str,
    recipe_name: str,
    config_providers: ProvidersConfig,
) -> tuple[str, dict[str, str]]:

    # Tiers 0/0W: recipe-scoped overrides — single lookup shared by both tiers
    if recipe_name:
        recipe_map = config_providers.recipe_overrides.get(recipe_name)
        if recipe_map:
            # Tier 0: per-recipe per-step override (highest specificity)
            if step_name:
                recipe_step_override = recipe_map.get(step_name)
                if recipe_step_override:
                    logger.debug(
                        "provider_profile_resolved",
                        tier="recipe_step_override",
                        profile=recipe_step_override,
                    )
                    return _provider_result(recipe_step_override, config_providers.profiles)

            # Tier 0W: per-recipe wildcard override
            recipe_wildcard = recipe_map.get("*")
            if recipe_wildcard:
                logger.debug(
                    "provider_profile_resolved",
                    tier="recipe_wildcard_override",
                    profile=recipe_wildcard,
                )
                return _provider_result(recipe_wildcard, config_providers.profiles)

    # Tier 1: per-step config override (requires recipe context)
    if recipe_name and step_name:
        step_override = config_providers.step_overrides.get(step_name)
        if step_override:
            logger.debug(
                "provider_profile_resolved",
                tier="step_override",
                profile=step_override,
            )
            if step_override == "anthropic":
                return ("anthropic", {})
            profile = config_providers.resolved_profiles.get(step_override)
            if profile is None:
                logger.debug("provider_profile_not_found", provider=step_override)
                return (step_override, {})
            return (step_override, _profile_to_env(profile))

    # Tier 2: wildcard override (requires recipe context)
    if recipe_name:
        wildcard = config_providers.step_overrides.get("*")
        if wildcard:
            logger.debug(
                "provider_profile_resolved",
                tier="recipe_wildcard",
                profile=wildcard,
            )
            if wildcard == "anthropic":
                return ("anthropic", {})
            profile = config_providers.resolved_profiles.get(wildcard)
            if profile is None:
                logger.debug("provider_profile_not_found", provider=wildcard)
                return (wildcard, {})
            return (wildcard, _profile_to_env(profile))

    # Tier 3: step YAML provider field
    if step_name:
        logger.debug("provider_profile_resolved", tier="step_provider_field", profile=step_name)
        if step_name == "anthropic":
            return ("anthropic", {})
        profile = config_providers.resolved_profiles.get(step_name)
        if profile is None:
            logger.debug("provider_profile_not_found", provider=step_name)
            return (step_name, {})
        return (step_name, _profile_to_env(profile))

    # Tier 4: default
    name = config_providers.default_provider or "anthropic"
    logger.debug("provider_profile_resolved", tier="default", profile=name)
    if name == "anthropic":
        return ("anthropic", {})
    profile = config_providers.resolved_profiles.get(name)
    if profile is None:
        logger.debug("provider_profile_not_found", provider=name)
        return (name, {})
    return (name, _profile_to_env(profile))


def _resolve_model_as_profile(
    model_value: str,
    config_providers: ProvidersConfig,
) -> tuple[str, str, dict[str, str] | None]:
    """Check if a model value names a configured provider profile.

    When a model parameter matches a key in ``providers.profiles``, the profile's
    ``ANTHROPIC_MODEL`` entry becomes the effective model for ``--model``, and the
    remaining profile entries become environment variable overrides for the subprocess.

    Returns:
        (effective_model, profile_name, provider_extras):
        - Profile match with ANTHROPIC_MODEL:
          (profile's model, profile_key, env_dict without ANTHROPIC_MODEL)
        - Profile match without ANTHROPIC_MODEL:
          ("", "", None) — caller should fall through to config default
        - No match:
          (model_value, "", None) — treat as literal model name
    """
    if not model_value:
        return (model_value, "", None)

    profile = config_providers.profiles.get(model_value)
    if profile is None:
        return (model_value, "", None)

    actual_model = profile.get("ANTHROPIC_MODEL")
    if not actual_model:
        logger.warning(
            "provider_profile_no_model",
            profile=model_value,
            hint="Add ANTHROPIC_MODEL to the profile or use a literal model name",
        )
        return ("", "", None)

    logger.debug(
        "model_as_profile_resolved",
        input_model=model_value,
        resolved_model=actual_model,
        profile=model_value,
    )
    env_dict = {k: v for k, v in profile.items() if k != "ANTHROPIC_MODEL" and v is not None}
    return (actual_model, model_value, env_dict)
