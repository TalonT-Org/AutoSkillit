"""First-run detection and guided onboarding menu for cook sessions."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import get_logger

logger = get_logger(__name__)


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
