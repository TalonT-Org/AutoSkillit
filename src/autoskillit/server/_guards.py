"""Orchestration-level gate functions for MCP tool access control."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import SessionType, extract_path_arg, get_logger, session_type
from autoskillit.pipeline import gate_error_result, headless_error_result

if TYPE_CHECKING:
    from autoskillit.config._config_dataclasses import ProvidersConfig

logger = get_logger(__name__)


def _get_ctx():  # type: ignore[return]
    from autoskillit.server._state import _get_ctx as _ctx_fn

    return _ctx_fn()


def _get_config():  # type: ignore[return]
    from autoskillit.server._state import _get_config as _cfg_fn

    return _cfg_fn()


def _require_orchestrator_or_higher(tool_name: str = "") -> str | None:
    """Return headless_error JSON if session is L1 (leaf); None if permitted.

    Interactive sessions (HEADLESS not set) always pass.
    Headless sessions must be L2 (orchestrator) or L3 (fleet).
    Fail-closed: unset/invalid SESSION_TYPE → LEAF → deny.
    """
    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        return None

    st = session_type()
    if st in (SessionType.ORCHESTRATOR, SessionType.FLEET):
        return None

    msg = (
        f"{tool_name} cannot be called from leaf sessions. "
        "Only orchestrator or fleet sessions may call this tool."
        if tool_name
        else None
    )
    return headless_error_result(msg)


def _require_orchestrator_exact(tool_name: str = "") -> str | None:
    """Return headless_error JSON if session is not exactly L2; None if permitted.

    Interactive sessions (HEADLESS not set) always pass.
    Headless sessions must be exactly L2 (orchestrator).
    L1 (leaf) and L3 (fleet) are both denied.
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
            f"{tool_name} cannot be called from leaf sessions. "
            "Only the orchestrator may call this tool."
            if tool_name
            else None
        )
    return headless_error_result(msg)


def _require_fleet(tool_name: str = "") -> str | None:
    """Return headless_error JSON if session is not L3 (fleet); None if permitted.

    No interactive bypass — fleet is a specific orchestration level, not a headless guard.
    L1 (leaf) and L2 (orchestrator) sessions are both denied.
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


def _resolve_provider_profile(
    step_name: str,
    recipe_name: str,
    config_providers: ProvidersConfig,
) -> tuple[str, dict[str, str]]:

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
            return (step_override, config_providers.profiles.get(step_override, {}))

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
            return (wildcard, config_providers.profiles.get(wildcard, {}))

    # Tier 3: step YAML provider field
    if step_name:
        logger.debug("provider_profile_resolved", tier="step_provider_field", profile=step_name)
        if step_name == "anthropic":
            return ("anthropic", {})
        return (step_name, config_providers.profiles.get(step_name, {}))

    # Tier 4: default
    name = config_providers.default_provider or "anthropic"
    logger.debug("provider_profile_resolved", tier="default", profile=name)
    if name == "anthropic":
        return ("anthropic", {})
    return (name, config_providers.profiles.get(name, {}))
