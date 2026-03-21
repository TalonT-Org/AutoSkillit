"""First-run detection and guided onboarding menu for cook sessions."""

from __future__ import annotations

import json
import subprocess as _sp
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OnboardingIntel:
    scanner_found: str | None = None
    build_tools: list[str] = field(default_factory=list)
    github_issues: list[str] = field(default_factory=list)


def is_first_run(project_dir: Path) -> bool:
    """Return True when the project is initialized but has never been onboarded."""
    # 1. autoskillit init must have run
    if not (project_dir / ".autoskillit" / "config.yaml").exists():
        return False
    # 2. onboarding already completed
    if (project_dir / ".autoskillit" / ".onboarded").exists():
        return False
    # 3. project already has local recipes (beyond the fresh state)
    recipes_dir = project_dir / ".autoskillit" / "recipes"
    if recipes_dir.exists():
        try:
            if any(recipes_dir.iterdir()):
                return False
        except OSError:
            pass
    # 4. project already has skill overrides (already customized)
    from autoskillit.workspace.skills import detect_project_local_overrides

    if detect_project_local_overrides(project_dir):
        return False
    return True


def mark_onboarded(project_dir: Path) -> None:
    """Write the .autoskillit/.onboarded marker file (idempotent)."""
    from autoskillit.core.io import atomic_write

    marker = project_dir / ".autoskillit" / ".onboarded"
    if not marker.exists():
        atomic_write(marker, "")


_KNOWN_BUILD_FILES: dict[str, str] = {
    "Taskfile.yml": "Taskfile",
    "Taskfile.yaml": "Taskfile",
    "Makefile": "Makefile",
    "package.json": "npm/yarn",
    "pyproject.toml": "uv/pip",
}

_KNOWN_SCANNERS: frozenset[str] = frozenset(
    {"gitleaks", "detect-secrets", "trufflehog", "git-secrets"}
)


def _detect_scanner(project_dir: Path) -> str | None:
    """Return first known scanner hook-id found in .pre-commit-config.yaml, else None."""
    pre_commit = project_dir / ".pre-commit-config.yaml"
    try:
        content = pre_commit.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for scanner in _KNOWN_SCANNERS:
        if scanner in content:
            return scanner
    return None


def _detect_build_tools(project_dir: Path) -> list[str]:
    """Return names of detected build tools present in project_dir."""
    found: list[str] = []
    seen: set[str] = set()
    for filename, label in _KNOWN_BUILD_FILES.items():
        if (project_dir / filename).exists() and label not in seen:
            found.append(label)
            seen.add(label)
    return found


def _fetch_good_first_issues(project_dir: Path) -> list[str]:  # noqa: ARG001
    """Return up to 3 'good first issue' titles from the GitHub remote.

    Returns [] on any failure (gh not installed, not authenticated, no remote).
    """
    try:
        result = _sp.run(
            [
                "gh",
                "issue",
                "list",
                "--label",
                "good first issue",
                "--limit",
                "3",
                "--json",
                "number,title",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if result.returncode != 0:
            return []
        items: Any = json.loads(result.stdout or "[]")
        return [f"#{i['number']}: {i['title']}" for i in items if "number" in i]
    except Exception:
        return []


def gather_intel(project_dir: Path) -> OnboardingIntel:
    """Concurrently gather project intelligence (scanner, build tools, issues)."""
    with ThreadPoolExecutor(max_workers=3) as ex:
        sf = ex.submit(_detect_scanner, project_dir)
        bf = ex.submit(_detect_build_tools, project_dir)
        gf = ex.submit(_fetch_good_first_issues, project_dir)
    try:
        scanner = sf.result()
    except Exception:
        scanner = None
    try:
        tools = bf.result()
    except Exception:
        tools = []
    try:
        issues = gf.result()
    except Exception:
        issues = []
    return OnboardingIntel(scanner_found=scanner, build_tools=tools, github_issues=issues)


def _suggest_demo_target(project_dir: Path, intel: OnboardingIntel | None) -> str:
    """Return a suggested project_dir string for the demo run, or empty string."""
    return str(project_dir)


def run_onboarding_menu(project_dir: Path, *, color: bool = True) -> str | None:
    """Display the guided onboarding menu.

    Starts background intel gathering before showing the menu.
    Returns an initial_prompt string for A/B/C/D paths (mark_onboarded called
    by the caller's finally block), or None for E/decline (mark_onboarded
    called internally).
    """
    _B = "\x1b[1m" if color else ""
    _C = "\x1b[96m" if color else ""
    _Y = "\x1b[33m" if color else ""
    _R = "\x1b[0m" if color else ""

    print(
        f"\n{_B}It looks like this is your first time using AutoSkillit in this project.{_R}"
    )
    ans = input("Would you like help getting started? [Y/n]: ").strip().lower()
    if ans in ("n", "no"):
        mark_onboarded(project_dir)
        return None

    # Start gathering intel concurrently while user reads the menu
    executor = ThreadPoolExecutor(max_workers=3)
    intel_future = executor.submit(gather_intel, project_dir)

    print(f"\n{_B}What would you like to do?{_R}")
    print(f"  {_Y}A{_R} — {_C}Analyze this repo{_R}          (runs /autoskillit:setup-project wizard)")
    print(f"  {_Y}B{_R} — {_C}I have a GitHub issue{_R}       (routes to prepare-issue)")
    print(f"  {_Y}C{_R} — {_C}Show me a demo run{_R}          (auto-detects a safe target)")
    print(f"  {_Y}D{_R} — {_C}Write a custom recipe{_R}        (runs /autoskillit:write-recipe)")
    print(f"  {_Y}E{_R} — {_C}Skip{_R}                         (start a normal session)")

    choice = input(f"\n{_B}[A/B/C/D/E]: {_R}").strip().upper()

    # Collect intel (likely already done)
    try:
        intel: OnboardingIntel | None = intel_future.result(timeout=5.0)
    except Exception:
        intel = None
    finally:
        executor.shutdown(wait=False)

    if choice == "A":
        return "/autoskillit:setup-project"

    if choice == "B":
        ref = input("Issue URL or number: ").strip()
        if ref:
            return f"/autoskillit:prepare-issue {ref}"
        return "/autoskillit:setup-project"

    if choice == "C":
        target = _suggest_demo_target(project_dir, intel)
        prompt = "/autoskillit:setup-project"
        if target:
            prompt += f" {target}"
        return prompt

    if choice == "D":
        return "/autoskillit:write-recipe"

    # E or unrecognized → skip
    mark_onboarded(project_dir)
    return None
