"""Skill admission and diagnostics for interactive CLI sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, overload

from autoskillit.core import SkillContractError

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig
    from autoskillit.core import SkillUnavailabilityPayload, SkillVisibilitySpec
    from autoskillit.workspace import DefaultSkillResolver, SkillExclusion


def render_skill_contract_composition_failure(exc: SkillContractError) -> None:
    """Print a clean, actionable message for a composition-root contract failure.

    No traceback. The underlying message is already actionable (file path,
    invalidity kind's hint, and — for the tier-role gate — an embedded
    doctor pointer) after resolution-boundary containment; this guarantees
    the doctor pointer is present for every SkillContractError shape, not
    only the ones that already embed it.
    """
    print(f"ERROR: {exc}")
    print("Run: autoskillit doctor")


def render_skill_catalog_exclusions(exclusions: tuple[SkillExclusion, ...]) -> None:
    """Print one operator-visible warning line per excluded skill candidate."""
    for exclusion in exclusions:
        hint = "; ".join(exclusion.hints)
        print(f"WARNING: excluded project-local skill {exclusion.name!r} at {exclusion.path}")
        if hint:
            print(f"  hint: {hint}")


def render_skill_unavailability(
    unavailability_payload: SkillUnavailabilityPayload,
) -> None:
    """Print backend exclusions grouped by operation and diagnostic."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for item in unavailability_payload["unavailable"]:
        grouped.setdefault((item["operation"], item["diagnostic"]), []).append(item["skill"])
    for (operation, diagnostic), skill_names in sorted(grouped.items()):
        names = ", ".join(sorted(skill_names))
        print(
            f"{len(skill_names)} skills unavailable on this backend "
            f"({operation}: {diagnostic}): {names}"
        )


@overload
def append_skill_unavailability(
    system_prompt: None,
    unavailability_payload: SkillUnavailabilityPayload,
) -> None: ...


@overload
def append_skill_unavailability(
    system_prompt: str,
    unavailability_payload: SkillUnavailabilityPayload,
) -> str: ...


def append_skill_unavailability(
    system_prompt: str | None,
    unavailability_payload: SkillUnavailabilityPayload,
) -> str | None:
    """Append the canonical machine-readable refusal payload to a prompt."""
    if system_prompt is None or not unavailability_payload["unavailable"]:
        return system_prompt
    unavailable_json = json.dumps(
        unavailability_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    block = (
        f"<autoskillit_skill_unavailability>{unavailable_json}</autoskillit_skill_unavailability>"
    )
    if block in system_prompt:
        return system_prompt
    return f"{system_prompt.rstrip()}\n\n{block}"


def prepare_cook_skill_visibility(
    config: AutomationConfig, project_dir: Path
) -> tuple[DefaultSkillResolver, SkillVisibilitySpec]:
    """Resolve cook visibility and reject invalid tier roles before launch setup."""
    from autoskillit.workspace import DefaultSkillResolver, validate_skill_tier_roles

    resolver = DefaultSkillResolver()
    visibility = config.skill_visibility_spec()
    try:
        validate_skill_tier_roles(visibility, resolver, project_dir)
    except SkillContractError as exc:
        render_skill_contract_composition_failure(exc)
        raise SystemExit(1) from exc
    return resolver, visibility
