"""Feature flag dependency and registry consistency doctor checks."""

from __future__ import annotations

import autoskillit.core as _core
from autoskillit.core import Severity, get_logger

from ._doctor_types import DoctorResult

logger = get_logger(__name__)


def _check_feature_dependencies(features: dict[str, bool]) -> DoctorResult:
    """Verify all enabled features have their dependencies satisfied."""
    violations: list[str] = []
    for name, defn in _core.FEATURE_REGISTRY.items():
        if not features.get(name, defn.default_enabled) or not defn.depends_on:
            continue
        for dep in defn.depends_on:
            dep_defn = _core.FEATURE_REGISTRY.get(dep)
            dep_default = dep_defn.default_enabled if dep_defn is not None else False
            if not features.get(dep, dep_default):
                violations.append(
                    f"Feature '{name}' requires '{dep}' but '{dep}' is disabled. "
                    f"Enable '{dep}' in features config first."
                )
    if violations:
        return DoctorResult(
            severity=Severity.ERROR,
            check="feature_dependencies",
            message="; ".join(violations),
        )
    return DoctorResult(
        severity=Severity.OK,
        check="feature_dependencies",
        message="All feature dependencies satisfied",
    )


def _check_feature_registry_consistency() -> DoctorResult:
    """Verify FEATURE_REGISTRY import_package entries resolve to importable modules."""
    import importlib

    failures: list[str] = []
    for name, defn in _core.FEATURE_REGISTRY.items():
        if not defn.import_package:
            continue
        try:
            importlib.import_module(defn.import_package)
        except ImportError as exc:
            failures.append(
                f"Feature '{name}' import_package {defn.import_package!r} "
                f"cannot be imported: {exc}"
            )
    if failures:
        return DoctorResult(
            severity=Severity.ERROR,
            check="feature_registry_consistency",
            message="; ".join(failures),
        )
    return DoctorResult(
        severity=Severity.OK,
        check="feature_registry_consistency",
        message="All FEATURE_REGISTRY import packages resolve",
    )
