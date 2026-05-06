"""Methodology tradition registry — load bundled and user-defined tradition specs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoskillit.core import get_logger, load_yaml, pkg_root

logger = get_logger(__name__)

BUNDLED_METHODOLOGY_TRADITIONS_DIR: Path = pkg_root() / "recipes" / "methodology-traditions"


@dataclass(frozen=True)
class VenueAppendixDef:
    """A venue-specific appendix entry for an ML sub-area within a tradition."""

    sub_area: str
    trigger_keywords: tuple[str, ...]
    expectations: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class MethodologyTraditionSpec:
    """Specification for a single methodology tradition."""

    name: str
    display_name: str
    canonical_guideline: dict[str, str | int | float | bool]
    fields_spanned: list[str]
    detection_keywords: list[str]
    mandatory_figures: list[dict[str, str]]
    strongly_expected_figures: list[dict[str, str]]
    anti_patterns: list[dict[str, str]]
    schema_version: str = ""
    priority: int = 999
    venue_specific_appendices: tuple[VenueAppendixDef, ...] = field(default_factory=tuple)


def _parse_int_field(data: dict, field_name: str, default: int, source_path: Path) -> int:
    val = data.get(field_name, default)
    try:
        return int(val)
    except (ValueError, TypeError) as e:
        name = data.get("name", "?")
        raise TypeError(
            f"Methodology tradition '{name}' field '{field_name}' must be an integer:"
            f" {source_path}"
        ) from e


def _parse_methodology_tradition(data: dict, source_path: Path) -> MethodologyTraditionSpec:
    if "name" not in data:
        raise ValueError(f"Methodology tradition YAML missing 'name' field: {source_path}")

    canonical_guideline = data.get("canonical_guideline")
    if canonical_guideline is not None and not isinstance(canonical_guideline, dict):
        raise TypeError(
            f"Methodology tradition '{data['name']}' field 'canonical_guideline' must be a dict, "
            f"got {type(canonical_guideline).__name__}: {source_path}"
        )

    for list_field in (
        "fields_spanned",
        "detection_keywords",
        "mandatory_figures",
        "strongly_expected_figures",
        "anti_patterns",
    ):
        val = data.get(list_field)
        if val is not None and not isinstance(val, list):
            raise TypeError(
                f"Methodology tradition '{data['name']}' field '{list_field}' must be a list, "
                f"got {type(val).__name__}: {source_path}"
            )

    def _coerce_dict_list(field_name: str, items: list) -> list[dict]:
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise TypeError(
                    f"Methodology tradition '{data['name']}' {field_name}[{i}] must be a"
                    f" dict, got {type(item).__name__}: {source_path}"
                )
        return [dict(item) for item in items]

    return MethodologyTraditionSpec(
        name=data["name"],
        display_name=str(data.get("display_name", "")),
        canonical_guideline=dict(canonical_guideline) if canonical_guideline else {},
        fields_spanned=list(data.get("fields_spanned", [])),
        detection_keywords=list(data.get("detection_keywords", [])),
        mandatory_figures=_coerce_dict_list(
            "mandatory_figures", data.get("mandatory_figures", [])
        ),
        strongly_expected_figures=_coerce_dict_list(
            "strongly_expected_figures", data.get("strongly_expected_figures", [])
        ),
        anti_patterns=_coerce_dict_list("anti_patterns", data.get("anti_patterns", [])),
        schema_version=str(data.get("schema_version", "")),
        priority=_parse_int_field(data, "priority", 999, source_path),
        venue_specific_appendices=_parse_venue_appendices(data, source_path),
    )


def _parse_venue_appendices(data: dict, source_path: Path) -> tuple[VenueAppendixDef, ...]:
    raw = data.get("venue_specific_appendices", [])
    if not isinstance(raw, list):
        raise TypeError(
            f"Methodology tradition '{data['name']}' field 'venue_specific_appendices' "
            f"must be a list, got {type(raw).__name__}: {source_path}"
        )
    result: list[VenueAppendixDef] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TypeError(
                f"Methodology tradition '{data['name']}' venue_specific_appendices[{i}] "
                f"must be a dict, got {type(item).__name__}: {source_path}"
            )
        sub_area = item.get("sub_area")
        if not isinstance(sub_area, str) or not sub_area:
            raise TypeError(
                f"Methodology tradition '{data['name']}' venue_specific_appendices[{i}] "
                f"'sub_area' must be a non-empty string: {source_path}"
            )
        trigger_keywords = item.get("trigger_keywords")
        if not isinstance(trigger_keywords, list):
            raise TypeError(
                f"Methodology tradition '{data['name']}' venue_specific_appendices[{i}] "
                f"'trigger_keywords' must be a list, "
                f"got {type(trigger_keywords).__name__}: {source_path}"
            )
        expectations = item.get("expectations")
        if not isinstance(expectations, list):
            raise TypeError(
                f"Methodology tradition '{data['name']}' venue_specific_appendices[{i}] "
                f"'expectations' must be a list, got {type(expectations).__name__}: {source_path}"
            )
        parsed_expectations: list[dict[str, str]] = []
        for j, exp in enumerate(expectations):
            if not isinstance(exp, dict):
                raise TypeError(
                    f"Methodology tradition '{data['name']}' venue_specific_appendices[{i}] "
                    f"expectations[{j}] must be a dict, got {type(exp).__name__}: {source_path}"
                )
            fig = exp.get("figure")
            src = exp.get("source")
            if not isinstance(fig, str) or not fig:
                raise TypeError(
                    f"Methodology tradition '{data['name']}' venue_specific_appendices[{i}] "
                    f"expectations[{j}] 'figure' must be a non-empty string: {source_path}"
                )
            if not isinstance(src, str) or not src:
                raise TypeError(
                    f"Methodology tradition '{data['name']}' venue_specific_appendices[{i}] "
                    f"expectations[{j}] 'source' must be a non-empty string: {source_path}"
                )
            parsed_expectations.append({"figure": fig, "source": src})
        result.append(
            VenueAppendixDef(
                sub_area=sub_area,
                trigger_keywords=tuple(str(kw) for kw in trigger_keywords),
                expectations=tuple(parsed_expectations),
            )
        )
    return tuple(result)


def _load_traditions_from_dir(directory: Path) -> dict[str, MethodologyTraditionSpec]:
    if not directory.exists():
        return {}
    result: dict[str, MethodologyTraditionSpec] = {}
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        try:
            data = load_yaml(path)
            if isinstance(data, dict):
                spec = _parse_methodology_tradition(data, path)
                result[spec.name] = spec
            else:
                logger.warning(
                    "Skipping methodology tradition file with non-dict top-level structure: %s",
                    path,
                )
        except (ValueError, TypeError, OSError):
            logger.warning(
                "Skipping malformed methodology tradition file: %s", path, exc_info=True
            )
    return result


def parse_methodology_tradition(
    data: dict[str, Any], source_path: Path
) -> MethodologyTraditionSpec:
    return _parse_methodology_tradition(data, source_path)


def load_traditions_from_dir(directory: Path) -> dict[str, MethodologyTraditionSpec]:
    return _load_traditions_from_dir(directory)


def load_all_methodology_traditions(
    project_dir: Path | None = None,
) -> list[MethodologyTraditionSpec]:
    """Load methodology traditions: bundled traditions merged with user-defined overrides.

    User-defined traditions with the same name as a bundled tradition replace the bundled
    tradition entirely — no field merging. User-defined traditions with a new name are added
    alongside bundled traditions.

    The returned list is sorted by ``(priority, name)``.

    Args:
        project_dir: Project root containing optional user-defined overrides at
            ``.autoskillit/methodology-traditions/``. When ``None``, only bundled traditions
            are returned.

    Returns:
        Sorted list of ``MethodologyTraditionSpec``.
    """
    traditions = _load_traditions_from_dir(BUNDLED_METHODOLOGY_TRADITIONS_DIR)

    if project_dir is not None:
        user_dir = Path(project_dir) / ".autoskillit" / "methodology-traditions"
        user_traditions = _load_traditions_from_dir(user_dir)
        for spec in user_traditions.values():
            if spec.schema_version and spec.schema_version != "1.0":
                logger.warning(
                    "User methodology tradition has schema_version mismatch; loading continues",
                    tradition_name=spec.name,
                    schema_version=spec.schema_version,
                    expected_schema_version="1.0",
                )
        traditions.update(user_traditions)

    sorted_traditions = sorted(traditions.values(), key=lambda s: (s.priority, s.name))
    return sorted_traditions


def get_methodology_tradition_by_name(
    name: str,
    project_dir: Path | None = None,
) -> MethodologyTraditionSpec | None:
    """Look up a single methodology tradition by name.

    Returns the matching spec or None if not found.
    """
    by_name = {s.name: s for s in load_all_methodology_traditions(project_dir)}
    return by_name.get(name)


def is_out_of_scope_tradition(spec: MethodologyTraditionSpec) -> bool:
    """Return True when the tradition has no mandatory figures.

    Qualitative traditions do not mandate specific figure types and are therefore
    considered out of scope for automated figure requirement checking.
    """
    return len(spec.mandatory_figures) == 0
