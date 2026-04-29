"""Config, gitignore, and secret scanning doctor checks."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import Severity, get_logger

from ._doctor_types import DoctorResult

logger = get_logger(__name__)


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
    from autoskillit.config import ConfigSchemaError, validate_layer_keys
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
