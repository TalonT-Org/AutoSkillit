"""Recipe-section schema validation and canonical digest helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from types import MappingProxyType
from typing import Literal

__all__ = [
    "RecipeSectionDef",
    "RECIPE_SECTION_REGISTRY",
    "DYNAMIC_RECIPE_SECTION_DEF",
    "RECIPE_SECTION_PAGINATION_VERSION",
    "RECIPE_SECTION_REGISTRY_DIGEST",
    "RECIPE_SECTION_PAGINATION_POLICY_DIGEST",
    "RecipeSectionContentFormatDef",
    "RECIPE_SECTION_CONTENT_FORMAT_REGISTRY",
    "RECIPE_SECTION_MANDATORY_FAILURE_CODES",
    "RECIPE_SECTION_RESPONSE_FLOOR_BYTES",
    "RecipeSectionValidationFinding",
    "canonical_recipe_section_json",
    "recipe_section_digest",
    "recipe_section_element_digest",
    "recipe_section_plan_digest",
    "validate_recipe_artifact_sections",
]


@dataclass(frozen=True, slots=True)
class RecipeSectionDef:
    """Static schema and pagination definition for one pullable recipe section."""

    name: str
    value_kind: Literal["string", "array"]
    element_kind: Literal["string"] | None
    missing_behavior: Literal["invalid", "absent", "default"]
    none_behavior: Literal["invalid", "absent"]
    section_strategy: Literal["raw", "scalar", "array"]
    range_unit: Literal["utf8-bytes", "decoded-utf8-bytes", "elements"]
    ordinary_content_format: Literal["raw-text", "json-scalar-page", "json-array-page"]
    oversized_content_format: Literal["json-element-fragment"] | None
    has_default: bool = False
    default_value: tuple[()] | None = None

    def __post_init__(self) -> None:
        """Reject contradictory public definitions at their construction boundary."""
        if not self.name:
            raise ValueError("recipe section definition name must not be empty")
        if self.missing_behavior not in {"invalid", "absent", "default"}:
            raise ValueError("invalid recipe section missing behavior")
        if self.none_behavior not in {"invalid", "absent"}:
            raise ValueError("invalid recipe section null behavior")
        layout = (
            self.value_kind,
            self.element_kind,
            self.section_strategy,
            self.range_unit,
            self.ordinary_content_format,
            self.oversized_content_format,
        )
        valid_layouts = {
            ("string", None, "raw", "utf8-bytes", "raw-text", None),
            (
                "string",
                None,
                "scalar",
                "decoded-utf8-bytes",
                "json-scalar-page",
                None,
            ),
            (
                "array",
                "string",
                "array",
                "elements",
                "json-array-page",
                "json-element-fragment",
            ),
        }
        if layout not in valid_layouts:
            raise ValueError("invalid recipe section strategy and content-format combination")
        if self.has_default != (self.missing_behavior == "default"):
            raise ValueError("recipe section default flag must match missing behavior")
        if self.has_default:
            if self.value_kind != "array" or self.default_value != ():
                raise ValueError("defaulted recipe sections require an empty array default")
        elif self.default_value is not None:
            raise ValueError("recipe sections without defaults must not declare a default value")


def _validated_recipe_section_registry(
    definitions: dict[str, RecipeSectionDef],
) -> Mapping[str, RecipeSectionDef]:
    for name, definition in definitions.items():
        if definition.name != name:
            raise ValueError(f"recipe section registry key {name!r} must match definition name")
    return MappingProxyType(definitions)


RECIPE_SECTION_REGISTRY: Mapping[str, RecipeSectionDef] = _validated_recipe_section_registry(
    {
        "content": RecipeSectionDef(
            name="content",
            value_kind="string",
            element_kind=None,
            missing_behavior="invalid",
            none_behavior="invalid",
            section_strategy="raw",
            range_unit="utf8-bytes",
            ordinary_content_format="raw-text",
            oversized_content_format=None,
        ),
        "ingredients_table": RecipeSectionDef(
            name="ingredients_table",
            value_kind="string",
            element_kind=None,
            missing_behavior="absent",
            none_behavior="absent",
            section_strategy="scalar",
            range_unit="decoded-utf8-bytes",
            ordinary_content_format="json-scalar-page",
            oversized_content_format=None,
        ),
        "orchestration_rules": RecipeSectionDef(
            name="orchestration_rules",
            value_kind="string",
            element_kind=None,
            missing_behavior="absent",
            none_behavior="invalid",
            section_strategy="raw",
            range_unit="utf8-bytes",
            ordinary_content_format="raw-text",
            oversized_content_format=None,
        ),
        "stop_step_semantics": RecipeSectionDef(
            name="stop_step_semantics",
            value_kind="string",
            element_kind=None,
            missing_behavior="absent",
            none_behavior="invalid",
            section_strategy="raw",
            range_unit="utf8-bytes",
            ordinary_content_format="raw-text",
            oversized_content_format=None,
        ),
        "errors": RecipeSectionDef(
            name="errors",
            value_kind="array",
            element_kind="string",
            missing_behavior="default",
            none_behavior="invalid",
            section_strategy="array",
            range_unit="elements",
            ordinary_content_format="json-array-page",
            oversized_content_format="json-element-fragment",
            has_default=True,
            default_value=(),
        ),
        "flow_records": RecipeSectionDef(
            name="flow_records",
            value_kind="array",
            element_kind="string",
            missing_behavior="invalid",
            none_behavior="invalid",
            section_strategy="array",
            range_unit="elements",
            ordinary_content_format="json-array-page",
            oversized_content_format="json-element-fragment",
        ),
        "warnings": RecipeSectionDef(
            name="warnings",
            value_kind="array",
            element_kind="string",
            missing_behavior="default",
            none_behavior="invalid",
            section_strategy="array",
            range_unit="elements",
            ordinary_content_format="json-array-page",
            oversized_content_format="json-element-fragment",
            has_default=True,
            default_value=(),
        ),
    },
)

DYNAMIC_RECIPE_SECTION_DEF = RecipeSectionDef(
    name="post_prune_step",
    value_kind="string",
    element_kind=None,
    missing_behavior="absent",
    none_behavior="invalid",
    section_strategy="raw",
    range_unit="utf8-bytes",
    ordinary_content_format="raw-text",
    oversized_content_format=None,
)

RECIPE_SECTION_PAGINATION_VERSION = 2
_RECIPE_SECTION_CANONICAL_JSON_ENSURE_ASCII = False
_RECIPE_SECTION_CANONICAL_JSON_SEPARATORS = (",", ":")
_RECIPE_SECTION_CANONICAL_JSON_SORT_KEYS = True
_RECIPE_SECTION_DIGEST_DOMAINS: Mapping[str, str] = MappingProxyType(
    {
        "raw_section": "autoskillit.recipe-section.raw.v1",
        "structured_section": "autoskillit.recipe-section.structured.v1",
        "element": "autoskillit.recipe-section.element.v1",
        "plan": "autoskillit.recipe-section.plan.v1",
    }
)


def _qualified_registry_digest(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


RECIPE_SECTION_REGISTRY_DIGEST = _qualified_registry_digest(
    {name: asdict(definition) for name, definition in sorted(RECIPE_SECTION_REGISTRY.items())}
    | {"$dynamic": asdict(DYNAMIC_RECIPE_SECTION_DEF)}
)


@dataclass(frozen=True, slots=True)
class RecipeSectionContentFormatDef:
    range_fields: tuple[str, ...]
    reconstruction: str


RECIPE_SECTION_CONTENT_FORMAT_REGISTRY: Mapping[str, RecipeSectionContentFormatDef] = (
    MappingProxyType(
        {
            "raw-text": RecipeSectionContentFormatDef(
                range_fields=("byte_start", "byte_end", "byte_total"),
                reconstruction="concatenate-content",
            ),
            "json-array-page": RecipeSectionContentFormatDef(
                range_fields=("element_start", "element_end", "element_total"),
                reconstruction="json-load-each-and-extend",
            ),
            "json-scalar-page": RecipeSectionContentFormatDef(
                range_fields=("scalar_byte_start", "scalar_byte_end", "scalar_byte_total"),
                reconstruction="json-load-each-and-concatenate-strings",
            ),
            "json-element-fragment": RecipeSectionContentFormatDef(
                range_fields=(
                    "element_index",
                    "element_sha256",
                    "fragment_index",
                    "fragment_count",
                    "fragment_byte_start",
                    "fragment_byte_end",
                    "fragment_byte_total",
                ),
                reconstruction="json-load-fragments-concatenate-verify-and-json-load",
            ),
        }
    )
)


def _recipe_section_policy_definitions() -> tuple[RecipeSectionDef, ...]:
    return (*RECIPE_SECTION_REGISTRY.values(), DYNAMIC_RECIPE_SECTION_DEF)


def _declared_recipe_section_content_formats() -> tuple[str, ...]:
    formats = {
        content_format
        for definition in _recipe_section_policy_definitions()
        for content_format in (
            definition.ordinary_content_format,
            definition.oversized_content_format,
        )
        if content_format is not None
    }
    if formats != RECIPE_SECTION_CONTENT_FORMAT_REGISTRY.keys():
        raise ValueError("recipe section definitions and content-format metadata must agree")
    return tuple(sorted(formats))


_DECLARED_RECIPE_SECTION_CONTENT_FORMATS = _declared_recipe_section_content_formats()

_RECIPE_SECTION_PAGINATION_POLICY = {
    "version": RECIPE_SECTION_PAGINATION_VERSION,
    "canonical_json": {
        "ensure_ascii": _RECIPE_SECTION_CANONICAL_JSON_ENSURE_ASCII,
        "separators": list(_RECIPE_SECTION_CANONICAL_JSON_SEPARATORS),
        "sort_keys": _RECIPE_SECTION_CANONICAL_JSON_SORT_KEYS,
    },
    "success_fields": [
        "success",
        "pagination_version",
        "section_registry_sha256",
        "section",
        "content_format",
        "content",
        "part",
        "total_parts",
        "has_more",
        "next_part",
        "section_sha256",
        "page_plan_sha256",
        "payload_sha256",
        "body_sha256",
    ],
    "optional_fields": {"next_part": "omit_on_terminal"},
    "content_formats": {
        content_format: list(RECIPE_SECTION_CONTENT_FORMAT_REGISTRY[content_format].range_fields)
        for content_format in _DECLARED_RECIPE_SECTION_CONTENT_FORMATS
    },
    "range_units": {
        definition.section_strategy: definition.range_unit
        for definition in _recipe_section_policy_definitions()
    },
    "digest_domains": dict(_RECIPE_SECTION_DIGEST_DOMAINS),
    "reconstruction": {
        content_format: RECIPE_SECTION_CONTENT_FORMAT_REGISTRY[content_format].reconstruction
        for content_format in _DECLARED_RECIPE_SECTION_CONTENT_FORMATS
    },
}
RECIPE_SECTION_PAGINATION_POLICY_DIGEST = _qualified_registry_digest(
    _RECIPE_SECTION_PAGINATION_POLICY
)

RECIPE_SECTION_MANDATORY_FAILURE_CODES: tuple[str, ...] = (
    "invalid_recipe_artifact_identity",
    "invalid_recipe_initialization_identity",
    "invalid_recipe_page_plan_identity",
    "invalid_recipe_section_continuation",
    "invalid_recipe_section_part",
    "recipe_artifact_identity_required",
    "recipe_artifact_parse_failed",
    "recipe_artifact_schema_mismatch",
    "recipe_artifact_unavailable",
    "recipe_section_bound_too_small",
    "recipe_section_cancelled",
    "recipe_section_internal_error",
    "recipe_section_pagination_nonconvergent",
    "recipe_section_serialization_failed",
    "section_not_found",
)


_RECIPE_SECTION_FAILURE_ENVELOPE_WITH_EMPTY_CODE_BYTES = len(b'{"error":"","success":false}')
RECIPE_SECTION_RESPONSE_FLOOR_BYTES = _RECIPE_SECTION_FAILURE_ENVELOPE_WITH_EMPTY_CODE_BYTES + max(
    len(code.encode("utf-8")) for code in RECIPE_SECTION_MANDATORY_FAILURE_CODES
)

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
