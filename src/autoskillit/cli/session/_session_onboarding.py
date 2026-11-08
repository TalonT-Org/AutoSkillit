"""Session skill guidance and first-run onboarding for cook sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, overload

from autoskillit.core import SkillContractError, get_logger

if TYPE_CHECKING:
    from autoskillit.core import SkillResolver, SkillUnavailabilityPayload, SkillVisibilitySpec
    from autoskillit.workspace import SkillExclusion

logger = get_logger(__name__)


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


def validate_cook_skill_visibility(
    visibility: SkillVisibilitySpec, resolver: SkillResolver, project_dir: Path
) -> None:
    """Render invalid skill-tier roles before cook starts launch setup."""
    from autoskillit.workspace import validate_skill_tier_roles

    try:
        validate_skill_tier_roles(visibility, resolver, project_dir)
    except SkillContractError as exc:
        render_skill_contract_composition_failure(exc)
        raise SystemExit(1) from exc


def is_first_run(project_dir: Path) -> bool:
    """Return True when the project is initialized but has never been onboarded."""
    if not (project_dir / ".autoskillit" / "config.yaml").exists():
        return False
    if (project_dir / ".autoskillit" / ".onboarded").exists():
        return False
    recipes_dir = project_dir / ".autoskillit" / "recipes"
    if recipes_dir.exists():
        try:
            if any(recipes_dir.iterdir()):
                return False
        except OSError:
            logger.debug("Could not list recipes dir %s", recipes_dir, exc_info=True)
    from autoskillit.workspace import detect_project_local_overrides

    if detect_project_local_overrides(project_dir):
        return False
    return True


def mark_onboarded(project_dir: Path) -> None:
    """Write the .autoskillit/.onboarded marker file (idempotent)."""
    from autoskillit.core import atomic_write

    marker = project_dir / ".autoskillit" / ".onboarded"
    if not marker.exists():
        atomic_write(marker, "")


def run_onboarding_menu(project_dir: Path, *, color: bool = True) -> str | None:
    """Display the guided onboarding menu.

    Returns an initial_prompt string for A/B/C/D paths (mark_onboarded called
    by the caller's finally block), or None for E/decline (mark_onboarded
    called internally).
    """
    _B = "\x1b[1m" if color else ""
    _C = "\x1b[96m" if color else ""
    _Y = "\x1b[33m" if color else ""
    _R = "\x1b[0m" if color else ""

    from autoskillit.cli.ui._timed_input import timed_prompt

    print(f"\n{_B}It looks like this is your first time using AutoSkillit in this project.{_R}")
    ans = timed_prompt(
        "Would you like help getting started? [Y/n]", default="", timeout=120, label="onboarding"
    )
    if ans.lower() in ("n", "no"):
        mark_onboarded(project_dir)
        return None

    print(f"\n{_B}What would you like to do?{_R}")
    print(
        f"  {_Y}A{_R} — {_C}Analyze this repo{_R}          "
        f"(runs /autoskillit:setup-project wizard)"
    )
    print(f"  {_Y}B{_R} — {_C}I have a GitHub issue{_R}       (routes to prepare-issue)")
    print(f"  {_Y}C{_R} — {_C}Show me a demo run{_R}          (auto-detects a safe target)")
    print(f"  {_Y}D{_R} — {_C}Write a custom recipe{_R}        (runs /autoskillit:write-recipe)")
    print(f"  {_Y}E{_R} — {_C}Skip{_R}                         (start a normal session)")

    choice = timed_prompt("\n[A/B/C/D/E]", default="E", timeout=120, label="onboarding").upper()

    if choice == "A":
        return "/autoskillit:setup-project"

    if choice == "B":
        ref = timed_prompt("Issue URL or number:", default="", timeout=120, label="onboarding")
        if ref:
            return f"/autoskillit:prepare-issue {ref}"
        return "/autoskillit:setup-project"

    if choice == "C":
        return f"/autoskillit:setup-project {project_dir}"

    if choice == "D":
        return "/autoskillit:write-recipe"

    # E or unrecognized → skip
    mark_onboarded(project_dir)
    return None
