"""Orchestration-level gate functions for MCP tool access control."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, assert_never

import regex as re

from autoskillit.core import (
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    BackendPinResolution,
    FaultDomain,
    InputContractResolver,
    extract_bash_write_targets,
    extract_path_arg,
    extract_positional_args,
    extract_skill_name,
    get_logger,
    is_path_like_token,
    parse_plan_paths,
)
from autoskillit.execution import get_backend
from autoskillit.hooks import (
    PROTECTED_SOURCE_PATH_PATTERNS,
    command_has_blocked_protected_path_read,
)
from autoskillit.pipeline import gate_error_result

if TYPE_CHECKING:
    from autoskillit.config._config_dataclasses import (
        AgentBackendConfig,
        ProviderProfileDef,
        ProvidersConfig,
    )

logger = get_logger(__name__)


RECIPE_READ_DENY_TRIGGER: str = "must not read recipe/skill/agent files directly"

_RECIPE_READ_CALLABLE_PATTERN: re.Pattern[str] = re.compile(r"autoskillit\.recipe\.(?!_cmd_rpc)")


def _get_ctx():  # type: ignore[return]
    from autoskillit.server.lifecycle._state import _get_ctx as _ctx_fn  # circular-break

    return _ctx_fn()


def _get_config():  # type: ignore[return]
    from autoskillit.server.lifecycle._state import _get_config as _cfg_fn  # circular-break

    return _cfg_fn()


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


def _require_no_infrastructure_fault(
    tool_name: str,
    *,
    override_reason: str | None,
) -> str | None:
    """Refuse a destructive tool call after an infrastructure fault, fail-closed.

    There is no rollback: releasing a GitHub claim or removing a clone cannot
    be undone by a process that is itself dying. That asymmetry — a
    destructive step with no compensating action, taken on evidence the
    environment produced rather than the work — is why refusal is the
    default. An override requires a caller-supplied, non-empty reason; every
    override is logged with the reason and the fault domain it overrides.

    Returns gate_error_result JSON on refusal, None if the call may proceed.
    """
    authority = _get_ctx().run_skill_completion
    if authority is None:
        return None
    receipt = authority.most_recent_acknowledged()
    if receipt is None or receipt.fault_domain is not FaultDomain.INFRASTRUCTURE:
        return None
    if override_reason:
        logger.warning(
            "infrastructure_fault_gate_overridden",
            tool_name=tool_name,
            reason=override_reason,
            fault_domain=receipt.fault_domain.value,
        )
        return None
    return gate_error_result(
        f"{tool_name} refused: the most recently completed step ended in an "
        "infrastructure fault (a property of the environment — the package "
        "was replaced, an artifact was contended — not of the work). There "
        "is no rollback for this action once taken. Pass an explicit, "
        "non-empty override reason if this refusal should not apply."
    )


def _check_recipe_read_prohibition(
    *, cmd: str | None = None, callable_name: str | None = None
) -> str | None:
    """Deny recipe/skill/agent file reads and recipe module callables.

    Headless-only: interactive sessions bypass this guard.
    Returns gate_error_result JSON on match, None on pass.
    """
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        return None
    if cmd is not None:
        if command_has_blocked_protected_path_read(cmd, PROTECTED_SOURCE_PATH_PATTERNS):
            return gate_error_result(
                f"run_cmd {RECIPE_READ_DENY_TRIGGER}. "
                "Use load_recipe to recall step definitions or the Skill tool "
                "for skill instructions."
            )
    if callable_name is not None:
        if _RECIPE_READ_CALLABLE_PATTERN.match(callable_name):
            return gate_error_result(
                f"run_python {RECIPE_READ_DENY_TRIGGER}. "
                "Use load_recipe to recall step definitions."
            )
    return None


def _check_write_target_boundary(
    cmd: str, cwd: str, allowed_prefixes: tuple[str, ...]
) -> str | None:
    """Deny run_cmd writes outside allowed prefix directories.

    Fail-open: returns None when allowed_prefixes is empty (no write scope configured).
    Returns gate_error_result JSON when a write target falls outside all prefixes.
    """
    if not allowed_prefixes:
        return None
    targets = extract_bash_write_targets(cmd, cwd)
    if not targets:
        return None
    normalized = tuple(os.path.realpath(p).rstrip("/") + "/" for p in allowed_prefixes)
    for target in targets:
        resolved = os.path.realpath(target)
        if not any(resolved.startswith(pfx) for pfx in normalized):
            return gate_error_result(
                f"run_cmd write target {target!r} is outside allowed write prefixes: "
                f"{', '.join(allowed_prefixes)}"
            )
    return None


def _validate_skill_command(skill_command: str) -> str | None:
    """Return error JSON if skill_command does not start with '/'.

    Validates the MCP-layer input format, which is always slash-prefixed.
    Backend-specific sigil translation happens downstream in _ensure_skill_prefix().
    """
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

    allowed = _get_config().implement_gate.allowed_plan_dirs
    if allowed and plan_path.parent.name not in allowed:
        return gate_error_result(
            f"Plan file is not at its original location. "
            f"Expected parent directory to be one of {sorted(allowed)}, "
            f"got: {plan_path.parent.name!r}. "
            f"The implement gate requires the plan at its make-plan/ or rectify/ origin — "
            f"copies in other directories are not accepted."
        )

    return None


def _check_input_contracts(
    skill_command: str,
    cwd: str,
    resolver: InputContractResolver | None,
) -> str | None:
    """Validate skill_command arguments against input contracts.

    Returns gate_error_result JSON string on validation failure, None if OK.
    Fails open if resolver is None or skill has no contracts.
    """
    if resolver is None:
        return None
    try:
        specs = resolver(skill_command)
    except Exception:
        logger.warning(
            "input_contract_resolver_failed", skill_command=skill_command, exc_info=True
        )
        return None
    if not specs:
        return None

    try:
        args = extract_positional_args(skill_command)
    except ValueError as e:
        skill_label = extract_skill_name(skill_command) or skill_command
        return gate_error_result(f"Malformed quoting in skill_command for {skill_label}: {e}")
    path_args = [a for a in args if is_path_like_token(a)]

    for spec in specs:
        if spec.position >= len(path_args):
            if spec.required:
                return gate_error_result(
                    f"Missing required {spec.type} argument '{spec.name}' "
                    f"for {extract_skill_name(skill_command) or skill_command}"
                )
            continue

        value = path_args[spec.position]

        match spec.type:
            case "file_path":
                resolved = _resolve_input_path(cwd, value)
                if not resolved.is_file():
                    return gate_error_result(
                        f"Input '{spec.name}' for {extract_skill_name(skill_command)}: "
                        f"expected a file, path does not exist or is a directory: {resolved}"
                    )
            case "directory_path":
                resolved = _resolve_input_path(cwd, value)
                if not resolved.is_dir():
                    return gate_error_result(
                        f"Input '{spec.name}' for {extract_skill_name(skill_command)}: "
                        f"expected a directory, path does not exist or is a file: {resolved}"
                    )
            case "file_path_list":
                members = parse_plan_paths(value)
                missing: list[str] = []
                for member in members:
                    member_path = _resolve_input_path(cwd, member)
                    if not member_path.is_file():
                        missing.append(str(member_path))
                if missing:
                    return gate_error_result(
                        f"Input '{spec.name}' for {extract_skill_name(skill_command)}: "
                        f"{len(missing)} of {len(members)} expected files do not exist "
                        f"or are directories: {', '.join(missing)}"
                    )
            case _ as unreachable:
                assert_never(unreachable)

    return None


def _resolve_input_path(cwd: str, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(cwd) / value


def _provider_configuration_can_use_anthropic(config: Any) -> bool:
    """Return whether an unpinned Claude launch can select Anthropic."""
    features = getattr(config, "features", {})
    if not isinstance(features, dict) or not features.get("providers", False):
        return True

    providers = config.providers
    if providers.default_provider in (None, "anthropic"):
        return True
    if "anthropic" in providers.step_overrides.values():
        return True
    return any(
        "anthropic" in overrides.values() for overrides in providers.recipe_overrides.values()
    )


def _backend_supports_quota(ctx: Any) -> bool:
    """Return whether a configured launch can require Anthropic quota admission."""
    config = getattr(ctx, "config", None)
    if config is None:
        backend = getattr(ctx, "backend", None)
        return backend is not None and backend.capabilities.anthropic_provider_capable

    def supports_anthropic(authority: BackendAuthority) -> bool:
        try:
            backend = get_backend(authority.backend)
        except (KeyError, ValueError):
            return False
        return backend.capabilities.anthropic_provider_capable

    authorities = [
        BackendAuthority(
            backend=config.agent_backend.backend,
            kind=BackendAuthorityKind.GLOBAL,
            tier=BackendAuthorityTier.GLOBAL,
            key_path="agent_backend.backend",
        ),
        *(
            BackendAuthority(
                backend=backend,
                kind=BackendAuthorityKind.STEP,
                tier=BackendAuthorityTier.STEP,
                key_path=f"agent_backend.step_overrides.{step}",
            )
            for step, backend in config.agent_backend.step_overrides.items()
        ),
        *(
            BackendAuthority(
                backend=backend,
                kind=BackendAuthorityKind.RECIPE,
                tier=BackendAuthorityTier.RECIPE,
                key_path=f"agent_backend.recipe_overrides.{recipe}.{step}",
            )
            for recipe, overrides in config.agent_backend.recipe_overrides.items()
            for step, backend in overrides.items()
        ),
    ]
    if any(supports_anthropic(authority) for authority in authorities) and (
        _provider_configuration_can_use_anthropic(config)
    ):
        return True

    for index, candidate in enumerate(config.providers.execution_candidates):
        authority = BackendAuthority(
            backend=candidate.backend,
            kind=BackendAuthorityKind.GLOBAL,
            tier=BackendAuthorityTier.GLOBAL,
            key_path=f"providers.execution_candidates.{index}.backend",
        )
        if not supports_anthropic(authority):
            continue
        if candidate.profile == "anthropic":
            return True
        if candidate.profile is None and _provider_configuration_can_use_anthropic(config):
            return True
    return False


def _provider_result(
    provider: str,
    profiles: dict[str, dict[str, str | None]],
) -> tuple[str, dict[str, str]]:
    if provider == "anthropic":
        return ("anthropic", {})
    raw = profiles.get(provider)
    if raw is None:
        logger.warning("provider_profile_not_found", provider=provider, tier="recipe_override")
        return (provider, {})
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


def _resolved_provider_result(
    provider: str,
    config_providers: ProvidersConfig,
    warning_tier: str,
    fallback_name: str,
) -> tuple[str, dict[str, str]]:
    if provider == "anthropic":
        return ("anthropic", {})
    profile = config_providers.resolved_profiles.get(provider)
    if profile is None:
        logger.warning("provider_profile_not_found", provider=provider, tier=warning_tier)
        return (fallback_name, {})
    return (provider, _profile_to_env(profile))


def _resolve_provider_profile(
    step_name: str,
    recipe_name: str,
    config_providers: ProvidersConfig,
    *,
    step_provider: str = "",
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
            return _resolved_provider_result(
                step_override,
                config_providers,
                "step_override",
                step_override,
            )

    # Tier 2: wildcard override (requires recipe context)
    if recipe_name:
        wildcard = config_providers.step_overrides.get("*")
        if wildcard:
            logger.debug(
                "provider_profile_resolved",
                tier="recipe_wildcard",
                profile=wildcard,
            )
            return _resolved_provider_result(
                wildcard,
                config_providers,
                "wildcard_override",
                wildcard,
            )

    # Tier 3: explicit step-level provider declaration (YAML provider: field)
    if step_provider:
        logger.debug(
            "provider_profile_resolved", tier="step_provider_field", profile=step_provider
        )
        return _resolved_provider_result(
            step_provider,
            config_providers,
            "step_provider_field",
            "anthropic",
        )

    # Tier 4: default
    name = config_providers.default_provider or "anthropic"
    logger.debug("provider_profile_resolved", tier="default", profile=name)
    return _resolved_provider_result(name, config_providers, "default", name)


def _resolve_backend_override(
    step_name: str,
    recipe_name: str,
    config_backend: AgentBackendConfig,
) -> BackendPinResolution | None:
    """Resolve explicit backend override from config.

    Returns a ``BackendPinResolution`` with backend name, typed authority, tier, and the
    dotted config key path, or ``None`` to fall through to
    capability/provider routing.

    Precedence (highest first):
      Tier 0:  ``recipe_overrides[recipe][step]``  (exact)
      Tier 0W: ``recipe_overrides[recipe]["*"]``   (recipe wildcard)
      Tier 1:  ``step_overrides[step]``            (global per-step, requires recipe context)
      Tier 2:  ``step_overrides["*"]``             (global wildcard, requires recipe context)

    Tiers 1 and 2 require a non-empty ``recipe_name`` to match the gating used
    by ``_resolve_provider_profile`` — without a recipe context, explicit
    overrides are inert so that the same config produces the same effective
    route whether invoked from admission or dispatch.
    """
    if recipe_name and recipe_name in config_backend.recipe_overrides:
        recipe_map = config_backend.recipe_overrides[recipe_name]
        if step_name and step_name in recipe_map:
            logger.debug(
                "backend_override_resolved",
                tier="recipe_step",
                recipe=recipe_name,
                step=step_name,
                backend=recipe_map[step_name],
            )
            return BackendPinResolution(
                backend=recipe_map[step_name],
                kind=BackendAuthorityKind.RECIPE,
                tier="recipe_step",
                key_path=f"agent_backend.recipe_overrides.{recipe_name}.{step_name}",
            )
        if "*" in recipe_map:
            logger.debug(
                "backend_override_resolved",
                tier="recipe_wildcard",
                recipe=recipe_name,
                step=step_name,
                backend=recipe_map["*"],
            )
            return BackendPinResolution(
                backend=recipe_map["*"],
                kind=BackendAuthorityKind.RECIPE,
                tier="recipe_wildcard",
                key_path=f"agent_backend.recipe_overrides.{recipe_name}.*",
            )

    if recipe_name and step_name and step_name in config_backend.step_overrides:
        logger.debug(
            "backend_override_resolved",
            tier="step_override",
            step=step_name,
            backend=config_backend.step_overrides[step_name],
        )
        return BackendPinResolution(
            backend=config_backend.step_overrides[step_name],
            kind=BackendAuthorityKind.STEP,
            tier="step_override",
            key_path=f"agent_backend.step_overrides.{step_name}",
        )

    if recipe_name and "*" in config_backend.step_overrides:
        logger.debug(
            "backend_override_resolved",
            tier="step_wildcard",
            step=step_name,
            backend=config_backend.step_overrides["*"],
        )
        return BackendPinResolution(
            backend=config_backend.step_overrides["*"],
            kind=BackendAuthorityKind.STEP,
            tier="step_wildcard",
            key_path="agent_backend.step_overrides.*",
        )

    return None


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
