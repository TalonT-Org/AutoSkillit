"""Stage B venue-appendix resolution — conditional-branching ML sub-area matching."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from autoskillit.core import load_yaml
from autoskillit.recipe.methodology_tradition_registry import (
    BUNDLED_METHODOLOGY_TRADITIONS_DIR,
    VenueAppendixDef,
    load_all_methodology_traditions,
)


@dataclass(frozen=True)
class AlternateParentDef:
    parent: str
    trigger_keywords: tuple[str, ...]
    constraint: str | None = None


@dataclass(frozen=True)
class MLSubAreaFoldingEntry:
    sub_area: str
    display_name: str
    primary_parent: str
    alternate_parents: tuple[AlternateParentDef, ...]


@dataclass(frozen=True)
class VenueAppendixMatch:
    sub_area: str
    resolved_parent: str
    appendix: VenueAppendixDef
    re_routed: bool


_CONSTRAINT_EVALUATORS: dict[str, Callable[[str], bool]] = {
    "only_if_explicit_construct_measurement": lambda text: bool(
        re.search(
            r"(?<!\w)(construct\s+measurement|measurement\s+construct|"
            r"item\s+response\s+theory|latent\s+trait\s+model)(?!\w)",
            text,
            re.IGNORECASE,
        )
    ),
}


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    return _keyword_pattern_cached(keyword.lower())


@cache
def _keyword_pattern_cached(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(keyword)
    return re.compile(r"(?<!\w)" + escaped + r"(?!\w)", re.IGNORECASE)


def _has_keyword_match(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    return any(_keyword_pattern(kw).search(text) for kw in keywords)


def load_ml_sub_area_folding() -> list[MLSubAreaFoldingEntry]:
    yaml_path = BUNDLED_METHODOLOGY_TRADITIONS_DIR / "_ml_sub_area_folding.yaml"
    data = load_yaml(yaml_path)
    entries: list[MLSubAreaFoldingEntry] = []
    for item in data.get("ml_sub_area_folding", []):
        alternates = tuple(
            AlternateParentDef(
                parent=a["parent"],
                trigger_keywords=tuple(a["trigger_keywords"]),
                constraint=a.get("constraint"),
            )
            for a in item.get("alternate_parents", [])
        )
        entries.append(
            MLSubAreaFoldingEntry(
                sub_area=item["sub_area"],
                display_name=item["display_name"],
                primary_parent=item["primary_parent"],
                alternate_parents=alternates,
            )
        )
    return entries


def _resolve_conditional_parent(
    entry: MLSubAreaFoldingEntry,
    plan_text: str,
) -> tuple[str, bool]:
    """Return (resolved_parent, re_routed)."""
    for alt in entry.alternate_parents:
        if _has_keyword_match(plan_text, alt.trigger_keywords):
            if alt.constraint is not None:
                evaluator = _CONSTRAINT_EVALUATORS.get(alt.constraint)
                if evaluator is None or not evaluator(plan_text):
                    continue
            return alt.parent, True
    return entry.primary_parent, False


def resolve_venue_appendices(
    plan_text: str,
    project_dir: Path | None = None,
) -> list[VenueAppendixMatch]:
    """Two-stage venue appendix resolution.

    1. Detect ML sub-areas from plan text using folding map keywords
    2. For each detected sub-area, resolve conditional parent
    3. Collect venue appendices from resolved parent tradition
    """
    folding = load_ml_sub_area_folding()
    traditions = {s.name: s for s in load_all_methodology_traditions(project_dir)}

    matches: list[VenueAppendixMatch] = []
    for entry in folding:
        parent, re_routed = _resolve_conditional_parent(entry, plan_text)
        spec = traditions.get(parent)
        if spec is None:
            continue
        for appendix in spec.venue_specific_appendices:
            if appendix.sub_area == entry.sub_area:
                if _has_keyword_match(plan_text, appendix.trigger_keywords):
                    matches.append(
                        VenueAppendixMatch(
                            sub_area=entry.sub_area,
                            resolved_parent=parent,
                            appendix=appendix,
                            re_routed=re_routed,
                        )
                    )
                    break
    return matches
