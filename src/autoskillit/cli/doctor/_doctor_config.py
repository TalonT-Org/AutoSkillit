"""Config, gitignore, and secret scanning doctor checks."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import Severity, get_logger

from ._doctor_types import DoctorResult

if TYPE_CHECKING:
    from autoskillit.config import AutomationConfig

logger = get_logger(__name__)


def _load_config_guarded(
    project_dir: Path | None = None,
) -> tuple[AutomationConfig, list[DoctorResult]]:
    """Load project config, surviving a broken config instead of raising.

    On success, returns the loaded config and an empty result list. On a
    ``ConfigSchemaError`` or ``YAMLError`` (schema-invalid or syntactically
    malformed ``config.yaml``), returns dataclass defaults plus a
    ``config_loadable`` ERROR result — the caller's remaining checks then run
    against built-in defaults rather than dying before any of them execute.
    """
    from autoskillit.config import AutomationConfig, ConfigSchemaError, load_config
    from autoskillit.core import YAMLError

    results: list[DoctorResult] = []
    try:
        cfg = load_config(project_dir or Path.cwd())
    except (ConfigSchemaError, YAMLError) as exc:
        cfg = AutomationConfig()
        results.append(
            DoctorResult(
                Severity.ERROR,
                "config_loadable",
                f"Configuration could not be loaded: {exc} "
                f"Backend and all config-derived checks below ran against built-in "
                f"defaults (agent_backend.backend={cfg.agent_backend.backend!r}), "
                f"not your configuration.",
            )
        )
    return cfg, results


def _check_project_config(project_dir: Path | None = None) -> DoctorResult:
    """Check that .autoskillit/config.yaml exists."""
    root = project_dir or Path.cwd()
    if not (root / ".autoskillit" / "config.yaml").is_file():
        return DoctorResult(
            Severity.WARNING,
            "project_config",
            "No project config found. Run: autoskillit init",
        )
    return DoctorResult(Severity.OK, "project_config", "Project config exists")


def _check_config_layers_for_secrets(project_dir: Path | None = None) -> DoctorResult:
    """Check all config.yaml layers for _SECRETS_ONLY_KEYS violations.

    Scans the user-level and project-level config.yaml files for any keys
    that belong only in .secrets.yaml. Reports ERROR with exact fix guidance.
    """
    from autoskillit.config import ConfigSchemaError, remap_retired_keys, validate_layer_keys
    from autoskillit.core import YAMLError, load_yaml

    root = project_dir or Path.cwd()
    config_paths = [
        Path.home() / ".autoskillit" / "config.yaml",
        root / ".autoskillit" / "config.yaml",
    ]
    for config_path in config_paths:
        if not config_path.is_file():
            continue
        try:
            data = load_yaml(config_path) or {}
        except YAMLError as exc:
            return DoctorResult(
                severity=Severity.WARNING,
                check="config_secrets_placement",
                message=f"Could not parse {str(config_path)!r} as YAML: {exc}",
            )
        if not isinstance(data, dict):
            continue
        data, _ = remap_retired_keys(data, is_secrets_layer=False)
        try:
            validate_layer_keys(data, config_path, is_secrets_layer=False)
        except ConfigSchemaError as exc:
            return DoctorResult(
                severity=Severity.ERROR,
                check="config_secrets_placement",
                message=str(exc),
            )
    return DoctorResult(
        severity=Severity.OK,
        check="config_secrets_placement",
        message="No secrets found in config.yaml layers",
    )


def _check_gitignore_completeness(project_dir: Path) -> DoctorResult:
    """Check that every file in .autoskillit/ is gitignored or in the committed allowlist."""
    from autoskillit.core import _AUTOSKILLIT_GITIGNORE_ENTRIES, _COMMITTED_BY_DESIGN

    autoskillit_dir = project_dir / ".autoskillit"
    gitignore_path = autoskillit_dir / ".gitignore"
    if not autoskillit_dir.is_dir():
        return DoctorResult(Severity.OK, "gitignore_completeness", "No .autoskillit/ directory.")
    if not gitignore_path.exists():
        return DoctorResult(
            Severity.WARNING,
            "gitignore_completeness",
            ".autoskillit/.gitignore missing. Run 'autoskillit init'.",
        )
    gitignore_content = gitignore_path.read_text(encoding="utf-8")
    uncovered: list[str] = []
    for item in sorted(autoskillit_dir.iterdir()):
        if item.name == ".gitignore":
            continue
        if item.name in _COMMITTED_BY_DESIGN:
            continue
        check_name = item.name + "/" if item.is_dir() else item.name
        if check_name not in gitignore_content:
            uncovered.append(item.name)
    for entry in _AUTOSKILLIT_GITIGNORE_ENTRIES:
        if entry not in gitignore_content:
            entry_name = entry.rstrip("/")
            if entry_name not in uncovered:
                uncovered.append(entry_name)
    if uncovered:
        return DoctorResult(
            Severity.WARNING,
            "gitignore_completeness",
            f"Files in .autoskillit/ not covered by .gitignore: {', '.join(uncovered)}. "
            "Add to _AUTOSKILLIT_GITIGNORE_ENTRIES or _COMMITTED_BY_DESIGN.",
        )
    return DoctorResult(Severity.OK, "gitignore_completeness", "All .autoskillit/ files covered.")


def _check_secret_scanning_hook(project_dir: Path) -> DoctorResult:
    """Check that .pre-commit-config.yaml includes a known secret scanning hook."""
    from autoskillit.cli._init_helpers import _KNOWN_SCANNERS, _detect_secret_scanner

    if _detect_secret_scanner(project_dir):
        return DoctorResult(
            Severity.OK,
            "secret_scanning_hook",
            "Secret scanning hook detected in .pre-commit-config.yaml.",
        )
    pre_commit_path = project_dir / ".pre-commit-config.yaml"
    if not pre_commit_path.exists():
        msg = (
            "No .pre-commit-config.yaml found. AutoSkillit commits code automatically — "
            "add a secret scanner (gitleaks, detect-secrets, trufflehog, or git-secrets) "
            "to prevent credential leaks."
        )
    else:
        scanners = ", ".join(sorted(_KNOWN_SCANNERS))
        msg = (
            f".pre-commit-config.yaml exists but contains no known secret scanner "
            f"({scanners}). Add one to prevent credential leaks."
        )
    return DoctorResult(Severity.ERROR, "secret_scanning_hook", msg)


def _iter_backend_pins(data: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Yield (recipe_name, step_name, backend_name) tuples for every pin in a config layer.

    ``recipe_name`` is empty for global ``step_overrides`` pins, which are not
    scoped to a single recipe.
    """
    agent_backend = data.get("agent_backend")
    if not isinstance(agent_backend, dict):
        return []
    pins: list[tuple[str, str, str]] = []
    recipe_overrides = agent_backend.get("recipe_overrides")
    if isinstance(recipe_overrides, dict):
        for recipe_name, step_map in recipe_overrides.items():
            if not isinstance(step_map, dict):
                continue
            for step_name, backend_name in step_map.items():
                if isinstance(backend_name, str):
                    pins.append((str(recipe_name), str(step_name), backend_name))
    step_overrides = agent_backend.get("step_overrides")
    if isinstance(step_overrides, dict):
        for step_name, backend_name in step_overrides.items():
            if isinstance(backend_name, str):
                pins.append(("", str(step_name), backend_name))
    return pins


def _check_standing_backend_pins_feasibility(
    project_dir: Path | None = None,
) -> list[DoctorResult]:
    """Check that every standing agent_backend pin can adapt skill semantics.

    Re-reads each config.yaml layer individually (mirrors
    ``_check_config_layers_for_secrets``) and resolves every ``recipe_overrides``
    and ``step_overrides`` pin against the step's versioned semantic plan.

    Also checks the persistent-session-root axis (#4391): a pin to a backend
    whose `capabilities.session_dir_persistent` is True but whose generated-home
    root convention cannot be derived is reported as an ERROR naming the dotted
    config key — this runs for both recipe and global `step_overrides` pins,
    before the recipe-specific semantic-adaptation check below.
    """
    from autoskillit.core import (
        YAMLError,
        load_yaml,
        resolve_temp_dir,
    )
    from autoskillit.execution import get_backend
    from autoskillit.recipe import find_recipe_by_name, load_recipe
    from autoskillit.workspace import DefaultSkillResolver, resolve_persistent_session_root

    root = project_dir or Path.cwd()
    config_paths = [
        Path.home() / ".autoskillit" / "config.yaml",
        root / ".autoskillit" / "config.yaml",
    ]
    resolver = DefaultSkillResolver()
    results: list[DoctorResult] = []

    for config_path in config_paths:
        if not config_path.is_file():
            continue
        try:
            data = load_yaml(config_path) or {}
        except YAMLError as exc:
            results.append(
                DoctorResult(
                    Severity.WARNING,
                    "standing_backend_pins_feasibility",
                    f"Could not parse {str(config_path)!r} as YAML: {exc}",
                )
            )
            continue
        if not isinstance(data, dict):
            continue

        for recipe_name, step_name, backend_name in _iter_backend_pins(data):
            is_recipe_pin = bool(recipe_name)
            dotted_key = (
                f"agent_backend.recipe_overrides.{recipe_name}.{step_name}"
                if is_recipe_pin
                else f"agent_backend.step_overrides.{step_name}"
            )

            try:
                backend = get_backend(backend_name)
            except ValueError:
                results.append(
                    DoctorResult(
                        Severity.WARNING,
                        "standing_backend_pins_feasibility",
                        f"{config_path}: {dotted_key} references unknown backend "
                        f"{backend_name!r}.",
                    )
                )
                continue

            if backend.capabilities.session_dir_persistent:
                workspace_cfg = data.get("workspace")
                temp_override = (
                    workspace_cfg.get("temp_dir") if isinstance(workspace_cfg, dict) else None
                )
                base_root = resolve_temp_dir(
                    root, temp_override if isinstance(temp_override, str) else None
                )
                try:
                    resolve_persistent_session_root(base_root, backend)
                except RuntimeError as exc:
                    results.append(
                        DoctorResult(
                            Severity.ERROR,
                            "standing_backend_pins_feasibility",
                            f"{config_path}: {dotted_key} pins persistent backend "
                            f"{backend_name!r}, but no persistent session root can be "
                            f"derived: {exc}. Remove or change this pin, or fix the "
                            "backend's generated-home convention.",
                        )
                    )
                    continue

            if not is_recipe_pin:
                # Global step_overrides pins are not tied to a single recipe's step
                # definition, so there is no skill to resolve capabilities from.
                continue

            recipe_info = find_recipe_by_name(recipe_name, root)
            if recipe_info is None:
                results.append(
                    DoctorResult(
                        Severity.WARNING,
                        "standing_backend_pins_feasibility",
                        f"{config_path}: {dotted_key} references unknown recipe {recipe_name!r}.",
                    )
                )
                continue

            try:
                recipe = load_recipe(recipe_info.path)
            except (OSError, ValueError, YAMLError) as exc:
                results.append(
                    DoctorResult(
                        Severity.WARNING,
                        "standing_backend_pins_feasibility",
                        f"{config_path}: {dotted_key} could not load recipe "
                        f"{recipe_name!r}: {exc}",
                    )
                )
                continue

            step = recipe.steps.get(step_name)
            if step_name != "*" and step is None:
                results.append(
                    DoctorResult(
                        Severity.WARNING,
                        "standing_backend_pins_feasibility",
                        f"{config_path}: {dotted_key} references unknown step "
                        f"{step_name!r} in recipe {recipe_name!r}.",
                    )
                )
                continue

            steps_to_check = [step] if step is not None else list(recipe.steps.values())
            for target_step in steps_to_check:
                skill_name = target_step.skill_name
                if not skill_name:
                    continue
                skill_info = resolver.resolve_effective(skill_name, root)
                if skill_info is None:
                    results.append(
                        DoctorResult(
                            Severity.WARNING,
                            "standing_backend_pins_feasibility",
                            f"{config_path}: {dotted_key} references skill "
                            f"{skill_name!r} (step {target_step.name!r}) which could "
                            "not be resolved.",
                        )
                    )
                    continue
                if skill_info.invalid_reason is not None:
                    results.append(
                        DoctorResult(
                            Severity.WARNING,
                            "standing_backend_pins_feasibility",
                            f"{config_path}: {dotted_key} references skill "
                            f"{skill_name!r} (step {target_step.name!r}) whose contract "
                            f"is invalid, so its semantic-plan feasibility could not be "
                            f"checked: {skill_info.invalid_reason}",
                        )
                    )
                    continue

                if skill_info.semantic_plan is None:
                    continue
                adaptation = backend.adapt_skill_semantics(skill_info.semantic_plan)
                if adaptation.diagnostic is not None:
                    results.append(
                        DoctorResult(
                            Severity.ERROR,
                            "standing_backend_pins_feasibility",
                            f"{config_path}: {dotted_key} pins backend {backend_name!r} "
                            f"for step {target_step.name!r}, but "
                            f"{adaptation.diagnostic}. "
                            "Remove or update this pin, or choose a backend that "
                            "supports the skill's semantic requirements.",
                        )
                    )

    if not results:
        results.append(
            DoctorResult(
                Severity.OK,
                "standing_backend_pins_feasibility",
                "All standing agent_backend pins can adapt declared skill semantics.",
            )
        )
    return results


def _check_local_recipe_validity(project_dir: Path | None = None) -> list[DoctorResult]:
    """Check every recipe in .autoskillit/recipes/ passes semantic validation.

    Local project recipes are not covered by the bundled recipe test suite, so
    a broken local recipe would otherwise only surface when it is dispatched.
    """
    from autoskillit.core import YAMLError
    from autoskillit.recipe import load_recipe, run_semantic_rules

    root = project_dir or Path.cwd()
    recipes_dir = root / ".autoskillit" / "recipes"
    if not recipes_dir.is_dir():
        return [DoctorResult(Severity.OK, "local_recipe_validity", "No local recipes directory")]

    results: list[DoctorResult] = []
    for yaml_path in sorted(recipes_dir.glob("*.yaml")):
        try:
            recipe = load_recipe(yaml_path)
        except (OSError, ValueError, YAMLError) as exc:
            results.append(
                DoctorResult(
                    Severity.ERROR,
                    "local_recipe_validity",
                    f"{yaml_path}: failed to load: {exc}",
                )
            )
            continue

        try:
            findings = run_semantic_rules(recipe)
        except Exception as exc:  # noqa: BLE001 - doctor must never crash
            logger.warning("local_recipe_validation_error", path=str(yaml_path), error=str(exc))
            results.append(
                DoctorResult(
                    Severity.WARNING,
                    "local_recipe_validity",
                    f"{yaml_path}: semantic validation could not run: {exc}",
                )
            )
            continue

        error_findings = [f for f in findings if f.severity == Severity.ERROR]
        if error_findings:
            rule_names = ", ".join(sorted({f.rule for f in error_findings}))
            results.append(
                DoctorResult(
                    Severity.ERROR,
                    "local_recipe_validity",
                    f"{yaml_path}: failed semantic validation ({rule_names}).",
                )
            )

    if not results:
        results.append(
            DoctorResult(
                Severity.OK,
                "local_recipe_validity",
                "All local recipes pass semantic validation.",
            )
        )
    return results
