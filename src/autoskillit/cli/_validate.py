"""Validate subapp: validate user-override registries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cyclopts import App

from autoskillit.core import YAMLError, load_yaml, pkg_root
from autoskillit.recipe.experiment_type_registry import (
    BUNDLED_EXPERIMENT_TYPES_DIR,
    ExperimentTypeSpec,
    _load_types_from_dir,
    _parse_experiment_type,
)
from autoskillit.recipe.methodology_tradition_registry import (
    _parse_methodology_tradition,
)

validate_app = App(name="validate", help="Validation commands.")

EXPECTED_SCHEMA_VERSION = "1.0"

EXPERIMENT_TYPE_DIMENSION_KEYS = (
    "clarity",
    "methodological_rigor",
    "external_validity",
    "practical_significance",
    "inferential_validity",
    "ethical_compliance",
    "reporting_completeness",
    "transparency",
)


@dataclass
class ValidationResult:
    filename: str
    path: Path
    errors: list[str]
    warnings: list[str]
    spec_name: str | None = None
    schema_version: str | None = None
    priority: int | None = None
    raw_content: str = ""

    @property
    def status(self) -> str:
        if self.errors:
            return "error"
        if self.warnings:
            return "warning"
        return "valid"


def _get_valid_lens_slugs() -> set[str]:
    skills_extended = pkg_root() / "skills_extended"
    if not skills_extended.is_dir():
        return set()
    return {d.name for d in skills_extended.iterdir() if d.is_dir() and "-lens-" in d.name}


def _validate_experiment_type_file(path: Path, valid_lenses: set[str]) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    raw_content = path.read_text()

    try:
        data = load_yaml(path)
    except YAMLError as e:
        return ValidationResult(
            filename=path.name,
            path=path,
            errors=[f"YAML parse error: {e}"],
            warnings=[],
            raw_content=raw_content,
        )

    try:
        spec = _parse_experiment_type(data, path)
    except (ValueError, TypeError) as e:
        return ValidationResult(
            filename=path.name,
            path=path,
            errors=[str(e)],
            warnings=[],
            raw_content=raw_content,
        )

    if spec.schema_version and spec.schema_version != EXPECTED_SCHEMA_VERSION:
        warnings.append(
            f"schema_version is '{spec.schema_version}', expected '{EXPECTED_SCHEMA_VERSION}'"
        )

    primary_lens = spec.applicable_lenses.get("primary")
    if primary_lens and primary_lens not in valid_lenses:
        errors.append(f"applicable_lenses.primary '{primary_lens}' is not a known lens slug")

    secondary_lens = spec.applicable_lenses.get("secondary")
    if secondary_lens and secondary_lens not in valid_lenses:
        errors.append(f"applicable_lenses.secondary '{secondary_lens}' is not a known lens slug")

    if len(spec.classification_triggers) == 0 and not spec.is_fallback:
        errors.append(
            "classification_triggers is empty (non-fallback experiment type requires triggers)"
        )

    if spec.priority <= 0:
        errors.append(f"priority must be a positive integer, got {spec.priority}")

    return ValidationResult(
        filename=path.name,
        path=path,
        errors=errors,
        warnings=warnings,
        spec_name=spec.name,
        schema_version=spec.schema_version,
        priority=spec.priority,
        raw_content=raw_content,
    )


def _validate_methodology_tradition_file(path: Path) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    raw_content = path.read_text()

    try:
        data = load_yaml(path)
    except YAMLError as e:
        return ValidationResult(
            filename=path.name,
            path=path,
            errors=[f"YAML parse error: {e}"],
            warnings=[],
            raw_content=raw_content,
        )

    try:
        spec = _parse_methodology_tradition(data, path)
    except (ValueError, TypeError) as e:
        return ValidationResult(
            filename=path.name,
            path=path,
            errors=[str(e)],
            warnings=[],
            raw_content=raw_content,
        )

    if spec.schema_version and spec.schema_version != EXPECTED_SCHEMA_VERSION:
        warnings.append(
            f"schema_version is '{spec.schema_version}', expected '{EXPECTED_SCHEMA_VERSION}'"
        )

    if len(spec.detection_keywords) == 0:
        errors.append("detection_keywords is empty")

    if spec.priority <= 0:
        errors.append(f"priority must be a positive integer, got {spec.priority}")

    return ValidationResult(
        filename=path.name,
        path=path,
        errors=errors,
        warnings=warnings,
        spec_name=spec.name,
        schema_version=spec.schema_version,
        priority=spec.priority,
        raw_content=raw_content,
    )


def _check_fallback_uniqueness(
    user_results: list[ValidationResult], bundled_types: dict[str, ExperimentTypeSpec]
) -> list[str]:
    fallback_errors: list[str] = []

    all_types: dict[str, ExperimentTypeSpec] = dict(bundled_types)
    for result in user_results:
        if result.spec_name and result.status != "error":
            data = load_yaml(result.path)
            if isinstance(data, dict):
                try:
                    spec = _parse_experiment_type(data, result.path)
                    all_types[spec.name] = spec
                except (ValueError, TypeError):
                    pass

    fallback_entries = [
        (name, spec, result)
        for name, spec in all_types.items()
        for result in user_results
        if result.spec_name == name and spec.is_fallback
    ]

    if len(fallback_entries) > 1:
        seen: set[str] = set()
        for name, spec, result in fallback_entries:
            if name not in seen:
                fallback_errors.append(
                    f"Multiple is_fallback=True entries found: '{name}' in {result.filename}"
                )
                seen.add(name)

    return fallback_errors


def _write_error_report(
    project_dir: Path, filename: str, registry_type: str, result: ValidationResult
) -> Path:
    error_dir = project_dir / ".autoskillit" / "validation-errors"
    error_dir.mkdir(parents=True, exist_ok=True)

    report_path = error_dir / f"{filename}.error.md"

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    expected_schema = f"""```yaml
name: {registry_type}-name
schema_version: "{EXPECTED_SCHEMA_VERSION}"
priority: 1
classification_triggers:
  - trigger_1
applicable_lenses:
  primary: exp-lens-estimand-clarity
  secondary: null
dimension_weights:
  clarity: high
  methodological_rigor: medium
  external_validity: low
  practical_significance: medium
  inferential_validity: high
  ethical_compliance: medium
  reporting_completeness: high
  transparency: high
red_team_focus:
  priority_area: description
l1_severity:
  severity_rating: medium
```"""

    how_to_fix = ""
    if "missing 'name'" in str(result.errors):
        how_to_fix = "Add a `name` field to your YAML file."
    elif "applicable_lenses" in str(result.errors):
        how_to_fix = "Ensure applicable_lenses reference valid lens slugs from skills_extended/ directories."
    elif "classification_triggers" in str(result.errors):
        how_to_fix = "Add at least one classification trigger, or set is_fallback: true."
    elif "priority" in str(result.errors):
        how_to_fix = "Set priority to a positive integer (higher = lower precedence)."
    elif result.errors:
        how_to_fix = "Fix the errors listed above and re-run validation."

    content = f"""# Validation error: {filename}

**File:** `{result.filename}`
**Validated at:** {timestamp}
**Registry type:** {registry_type}

## Errors

{chr(10).join(f"- {err}" for err in result.errors)}

## Warnings

{chr(10).join(f"- {warn}" for warn in result.warnings) if result.warnings else "_No warnings_"}

## Expected schema

{expected_schema}

## Your file

```yaml
{result.raw_content}
```

## How to fix

{how_to_fix}
"""

    report_path.write_text(content)
    return report_path


def _format_stdout_report(
    et_results: list[ValidationResult],
    mt_results: list[ValidationResult],
) -> str:
    lines: list[str] = []

    all_results = [("experiment-type", r) for r in et_results] + [
        ("methodology-tradition", r) for r in mt_results
    ]

    for _registry_type, result in all_results:
        if result.status == "error":
            symbol = "✗"
        elif result.status == "warning":
            symbol = "⚠"
        else:
            symbol = "✓"

        status_label = result.status.upper()
        lines.append(f"{symbol} [{status_label}] {result.filename}")

        if result.warnings:
            for w in result.warnings:
                lines.append(f"  ⚠ {w}")
        if result.errors:
            for e in result.errors:
                lines.append(f"  ✗ {e}")

    valid_count = sum(1 for r in et_results + mt_results if r.status == "valid")
    warn_count = sum(1 for r in et_results + mt_results if r.status == "warning")
    error_count = sum(1 for r in et_results + mt_results if r.status == "error")

    lines.append("")
    lines.append(f"Summary: {valid_count} valid  |  {warn_count} warning  |  {error_count} error")

    return "\n".join(lines)


@validate_app.command(name="registries")
def validate_registries() -> None:
    """Validate user-override registries in .autoskillit/."""
    project_dir = Path.cwd()

    et_dir = project_dir / ".autoskillit" / "experiment-types"
    mt_dir = project_dir / ".autoskillit" / "methodology-traditions"

    if not et_dir.exists() and not mt_dir.exists():
        print(
            "No user registry directories found (.autoskillit/experiment-types/ or .autoskillit/methodology-traditions/)."
        )
        return

    valid_lenses = _get_valid_lens_slugs()

    et_results: list[ValidationResult] = []
    if et_dir.exists():
        for path in sorted(et_dir.glob("*.yaml")):
            result = _validate_experiment_type_file(path, valid_lenses)
            et_results.append(result)

    mt_results: list[ValidationResult] = []
    if mt_dir.exists():
        for path in sorted(mt_dir.glob("*.yaml")):
            result = _validate_methodology_tradition_file(path)
            mt_results.append(result)

    bundled_types = _load_types_from_dir(BUNDLED_EXPERIMENT_TYPES_DIR)
    fallback_errors = _check_fallback_uniqueness(et_results, bundled_types)

    if fallback_errors:
        for result in et_results:
            if result.spec_name:
                for error in fallback_errors:
                    if result.spec_name in error:
                        result.errors.append(error)

    print(_format_stdout_report(et_results, mt_results))

    for result in et_results + mt_results:
        if result.errors:
            registry_type = "experiment-type"
            if result in mt_results:
                registry_type = "methodology-tradition"
            _write_error_report(project_dir, result.filename, registry_type, result)

    if any(r.status == "error" for r in et_results + mt_results):
        raise SystemExit(1)
