"""Fleet infrastructure and campaign state doctor checks."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.core import Severity, get_logger, pkg_root
from autoskillit.hook_registry import canonical_script_basenames

from ._doctor_types import DoctorResult

logger = get_logger(__name__)

_STALE_THRESHOLD_DAYS = 7


def _check_script_version_health(project_dir: Path | None = None) -> DoctorResult:
    """Check recipe migration status for all project pipeline scripts."""
    from autoskillit import __version__
    from autoskillit.core import RecipeSource
    from autoskillit.migration import FailureStore, default_store_path
    from autoskillit.recipe import list_recipes as _list_all_recipes

    root = project_dir or Path.cwd()
    _all_result = _list_all_recipes(root)
    scripts_result_items = [r for r in _all_result.items if r.source == RecipeSource.PROJECT]
    if not scripts_result_items:
        return DoctorResult(Severity.OK, "script_version_health", "No pipeline scripts found")

    from packaging.version import Version

    failure_store = FailureStore(default_store_path(root))
    known_failures = failure_store.load()

    failed_migrations: list[str] = []
    outdated: list[str] = []
    for script in scripts_result_items:
        if script.name in known_failures:
            f = known_failures[script.name]
            failed_migrations.append(f"{script.name} (failed after {f.retries_attempted} retries)")
        elif script.version is None or Version(script.version) < Version(__version__):
            outdated.append(script.name)

    if failed_migrations:
        return DoctorResult(
            Severity.ERROR,
            "script_version_health",
            "Migration failed — manual intervention required: " + ", ".join(failed_migrations),
        )
    if outdated:
        return DoctorResult(
            Severity.WARNING,
            "script_version_health",
            "Outdated recipes: " + ", ".join(outdated) + ". Will be auto-migrated on next load.",
        )
    return DoctorResult(Severity.OK, "script_version_health", "All recipes up to date")


def _check_sous_chef_bundled() -> DoctorResult:
    """Check that the sous-chef skill directory exists."""
    sous_chef_dir = pkg_root() / "skills" / "sous-chef"
    if sous_chef_dir.is_dir():
        return DoctorResult(
            Severity.OK,
            "sous_chef_bundled",
            "Sous-chef skill directory exists",
        )
    return DoctorResult(
        Severity.ERROR,
        "sous_chef_bundled",
        f"Sous-chef skill not found at {sous_chef_dir}/. Fatal prerequisite.",
    )


def _check_fleet_dispatch_guard_registered() -> DoctorResult:
    """Check that fleet dispatch guard is registered in HOOK_REGISTRY."""
    from autoskillit.hook_registry import HOOKS_DIR

    check_name = "fleet_dispatch_guard_registered"
    if "fleet_dispatch_guard.py" not in canonical_script_basenames():
        return DoctorResult(
            Severity.ERROR,
            check_name,
            "Fleet dispatch guard not registered in hooks.json. "
            "Run: autoskillit config sync-hooks",
        )
    if not (HOOKS_DIR / "fleet_dispatch_guard.py").is_file():
        return DoctorResult(
            Severity.ERROR,
            check_name,
            "Fleet dispatch guard registered but script file missing on disk. "
            "Run: autoskillit install",
        )
    return DoctorResult(
        Severity.OK,
        check_name,
        "Fleet dispatch guard registered and accessible",
    )


def _check_stale_fleet_state(project_dir: Path | None = None) -> DoctorResult:
    """Check for stale campaign state files with running dispatches > 7 days old."""
    import time

    root = project_dir or Path.cwd()
    fleet_dir = root / ".autoskillit" / "temp" / "fleet"
    check_name = "stale_fleet_state"
    if not fleet_dir.is_dir():
        return DoctorResult(Severity.OK, check_name, "No fleet state directory")
    threshold = time.time() - (_STALE_THRESHOLD_DAYS * 86400)
    stale_paths: list[str] = []
    for campaign_dir in fleet_dir.iterdir():
        if not campaign_dir.is_dir():
            continue
        state_file = campaign_dir / "state.json"
        if not state_file.is_file():
            continue
        if state_file.stat().st_mtime > threshold:
            continue
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            dispatches = data.get("dispatches", [])
            has_running = any(d.get("status") == "running" for d in dispatches)
            if has_running:
                stale_paths.append(str(state_file))
        except (json.JSONDecodeError, OSError, KeyError):
            continue
    if stale_paths:
        paths_str = ", ".join(stale_paths)
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Stale campaign state > {_STALE_THRESHOLD_DAYS} days: {paths_str}. "
            f"Run: autoskillit fleet status <id> --reap",
        )
    return DoctorResult(Severity.OK, check_name, "No stale fleet state files")


def _check_campaign_onboarding_hint(project_dir: Path | None = None) -> DoctorResult:
    """Hint when no campaign recipes exist yet."""
    root = project_dir or Path.cwd()
    campaigns_dir = root / ".autoskillit" / "recipes" / "campaigns"
    check_name = "campaign_onboarding_hint"
    if not campaigns_dir.is_dir():
        return DoctorResult(
            Severity.INFO,
            check_name,
            "No campaign recipes found. Get started: /autoskillit:make-campaign <description>",
        )
    yaml_files = [
        f for f in campaigns_dir.iterdir() if f.suffix in (".yaml", ".yml") and f.is_file()
    ]
    if not yaml_files:
        return DoctorResult(
            Severity.INFO,
            check_name,
            "No campaign recipes found. Get started: /autoskillit:make-campaign <description>",
        )
    return DoctorResult(
        Severity.OK,
        check_name,
        f"{len(yaml_files)} campaign recipe(s) found",
    )


def _check_campaign_manifest_clone_dests(project_dir: Path | None = None) -> DoctorResult:
    """Check that dispatches within campaign recipes use unique clone destinations."""
    from autoskillit.core import YAMLError, load_yaml

    root = project_dir or Path.cwd()
    campaigns_dir = root / ".autoskillit" / "recipes" / "campaigns"
    check_name = "campaign_manifest_clone_dests"
    if not campaigns_dir.is_dir():
        return DoctorResult(Severity.OK, check_name, "No campaigns directory")
    yaml_files = [
        f for f in campaigns_dir.iterdir() if f.suffix in (".yaml", ".yml") and f.is_file()
    ]
    if not yaml_files:
        return DoctorResult(Severity.OK, check_name, "No campaign recipes to check")
    seen_paths: dict[str, list[str]] = {}
    for yaml_file in yaml_files:
        try:
            data = load_yaml(yaml_file)
        except (YAMLError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        dispatches = data.get("dispatches", [])
        if not isinstance(dispatches, list):
            continue
        recipe_name = data.get("name", yaml_file.stem)
        for dispatch in dispatches:
            if not isinstance(dispatch, dict):
                continue
            ingredients = dispatch.get("ingredients", {})
            if not isinstance(ingredients, dict):
                continue
            clone_path = ingredients.get("clone_path", "")
            if clone_path:
                key = str(clone_path)
                label = f"{recipe_name}:{dispatch.get('name', '?')}"
                seen_paths.setdefault(key, []).append(label)
    duplicates = {path: users for path, users in seen_paths.items() if len(users) > 1}
    if duplicates:
        dup_details = "; ".join(
            f"{path} (used by {', '.join(users)})" for path, users in duplicates.items()
        )
        return DoctorResult(
            Severity.WARNING,
            check_name,
            f"Dispatches share a literal clone destination: {dup_details}. "
            f"Use unique clone paths per dispatch.",
        )
    return DoctorResult(Severity.OK, check_name, "All dispatch clone destinations unique")
