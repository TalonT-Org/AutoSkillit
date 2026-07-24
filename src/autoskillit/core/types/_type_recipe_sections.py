"""Recipe-section schema validation and canonical digest helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass

from ._type_constants_registries import (
    _RECIPE_SECTION_CANONICAL_JSON_ENSURE_ASCII,
    _RECIPE_SECTION_CANONICAL_JSON_SEPARATORS,
    _RECIPE_SECTION_CANONICAL_JSON_SORT_KEYS,
    _RECIPE_SECTION_DIGEST_DOMAINS,
    RECIPE_SECTION_REGISTRY,
)

__all__ = [
    "RecipeSectionValidationFinding",
    "canonical_recipe_section_json",
    "recipe_section_digest",
    "recipe_section_element_digest",
    "recipe_section_plan_digest",
    "validate_recipe_artifact_sections",
]

_MISSING = object()
_RECIPE_SECTION_VALIDATION_FINDING_LIMIT = 100


@dataclass(frozen=True, slots=True)
class RecipeSectionValidationFinding:
    """One stable schema mismatch without embedding an artifact value."""

    section: str
    code: str
    path: tuple[str | int, ...]
    expected: str
    actual_type: str
    omitted_count: int = 0

    def diagnostic(self) -> str:
        """Render one bounded value-free diagnostic token."""
        if self.omitted_count:
            return f"{self.omitted_count} additional findings omitted"
        return f"{self.code}@{'.'.join(str(part) for part in self.path)}"


def canonical_recipe_section_json(value: object) -> str:
    """Return the application canonical JSON used by recipe-section identities."""
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    return json.dumps(
        value,
        ensure_ascii=_RECIPE_SECTION_CANONICAL_JSON_ENSURE_ASCII,
        separators=_RECIPE_SECTION_CANONICAL_JSON_SEPARATORS,
        sort_keys=_RECIPE_SECTION_CANONICAL_JSON_SORT_KEYS,
    )


def _domain_digest(domain: str, payload: bytes) -> str:
    digest = hashlib.sha256(domain.encode("ascii") + b"\0" + payload).hexdigest()
    return f"sha256:{digest}"


def recipe_section_digest(value: object, *, raw: bool) -> str:
    """Hash one complete section in its declared raw or structured domain."""
    if raw:
        if type(value) is not str:
            raise TypeError("raw recipe section digest requires a string")
        return _domain_digest(_RECIPE_SECTION_DIGEST_DOMAINS["raw_section"], value.encode("utf-8"))
    return _domain_digest(
        _RECIPE_SECTION_DIGEST_DOMAINS["structured_section"],
        canonical_recipe_section_json(value).encode("utf-8"),
    )


def recipe_section_element_digest(value: object) -> str:
    """Hash one complete canonical structured element."""
    return _domain_digest(
        _RECIPE_SECTION_DIGEST_DOMAINS["element"],
        canonical_recipe_section_json(value).encode("utf-8"),
    )


def recipe_section_plan_digest(manifest: object) -> str:
    """Hash a plan manifest that excludes its resulting digest."""
    return _domain_digest(
        _RECIPE_SECTION_DIGEST_DOMAINS["plan"],
        canonical_recipe_section_json(manifest).encode("utf-8"),
    )


def _finding(
    section: str,
    code: str,
    path: tuple[str | int, ...],
    expected: str,
    value: object,
) -> RecipeSectionValidationFinding:
    actual_type = (
        "missing" if value is _MISSING else "null" if value is None else type(value).__name__
    )
    return RecipeSectionValidationFinding(
        section=section,
        code=code,
        path=path,
        expected=expected,
        actual_type=actual_type,
    )


def validate_recipe_artifact_sections(
    payload: Mapping[str, object],
) -> tuple[RecipeSectionValidationFinding, ...]:
    """Validate pullable fields; an empty tuple is the sole valid result."""
    findings: list[RecipeSectionValidationFinding] = []
    omitted_count = 0

    def record(finding: RecipeSectionValidationFinding) -> None:
        nonlocal omitted_count
        if len(findings) < _RECIPE_SECTION_VALIDATION_FINDING_LIMIT:
            findings.append(finding)
        else:
            omitted_count += 1

    for section, definition in RECIPE_SECTION_REGISTRY.items():
        value = payload.get(section, _MISSING)
        if value is _MISSING:
            if definition.missing_behavior == "invalid":
                record(
                    _finding(
                        section,
                        "missing_required_section",
                        (section,),
                        definition.value_kind,
                        value,
                    )
                )
            continue
        if value is None:
            if definition.none_behavior == "invalid":
                record(
                    _finding(
                        section,
                        "invalid_section_type",
                        (section,),
                        definition.value_kind,
                        value,
                    )
                )
            continue
        if definition.value_kind == "string":
            if type(value) is not str:
                record(
                    _finding(
                        section,
                        "invalid_section_type",
                        (section,),
                        "string",
                        value,
                    )
                )
            continue
        if type(value) is not list:
            record(
                _finding(
                    section,
                    "invalid_section_type",
                    (section,),
                    "array",
                    value,
                )
            )
            continue
        for index, element in enumerate(value):
            if type(element) is not str:
                record(
                    _finding(
                        section,
                        "invalid_section_element_type",
                        (section, index),
                        "string",
                        element,
                    )
                )

    step_names = payload.get("post_prune_step_names", _MISSING)
    if step_names is not _MISSING:
        if type(step_names) is not list:
            record(
                _finding(
                    "post_prune_step_names",
                    "invalid_post_prune_step_names",
                    ("post_prune_step_names",),
                    "array of strings",
                    step_names,
                )
            )
        else:
            for index, step_name in enumerate(step_names):
                if type(step_name) is not str:
                    record(
                        _finding(
                            "post_prune_step_names",
                            "invalid_post_prune_step_name",
                            ("post_prune_step_names", index),
                            "string",
                            step_name,
                        )
                    )
    if omitted_count:
        findings.append(
            RecipeSectionValidationFinding(
                section="$artifact",
                code="additional_findings_omitted",
                path=(),
                expected="valid recipe section values",
                actual_type="multiple",
                omitted_count=omitted_count,
            )
        )
    return tuple(findings)
