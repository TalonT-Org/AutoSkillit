"""Two-stage methodology tradition router for Tier-C lens selection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from autoskillit.recipe.methodology_tradition_registry import (
    MethodologyTraditionSpec,
    load_all_methodology_traditions,
)


@dataclass(frozen=True)
class UnionRuleDef:
    """Resolves multi-match ambiguity when candidate_set ⊆ member_traditions."""

    name: str
    member_traditions: frozenset[str]
    resolved_tradition: str

    def __post_init__(self) -> None:
        if self.resolved_tradition not in self.member_traditions:
            raise ValueError(
                f"UnionRuleDef '{self.name}': resolved_tradition "
                f"'{self.resolved_tradition}' must be a member of "
                f"member_traditions {self.member_traditions}"
            )


@dataclass(frozen=True)
class TraditionRouterResult:
    """Result from two-stage methodology classification."""

    primary_tradition: str | None
    applied_union_rules: tuple[str, ...]
    precedence_trace: str
    candidate_set: tuple[str, ...]


@cache
def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(keyword)
    return re.compile(r"(?<!\w)" + escaped + r"(?!\w)", re.IGNORECASE)


def _count_keyword_matches(text: str, spec: MethodologyTraditionSpec) -> int:
    count = 0
    for kw in spec.detection_keywords:
        if _keyword_pattern(kw.lower()).search(text):
            count += 1
    return count


def _try_union_rules(
    candidate_names: set[str],
    union_rules: list[UnionRuleDef],
) -> tuple[str | None, tuple[str, ...], str]:
    for rule in union_rules:
        if candidate_names.issubset(rule.member_traditions):
            return (
                rule.resolved_tradition,
                (rule.name,),
                f"stage2_tiebreak_by_rule_{rule.name}",
            )
    return None, (), ""


def classify_methodology(
    plan_text: str,
    project_dir: Path | None = None,
    *,
    min_keyword_matches: int = 2,
    union_rules: list[UnionRuleDef] | None = None,
    resolve_by_priority: bool = False,
) -> TraditionRouterResult:
    """Stage-1 deterministic keyword classification of plan methodology."""
    if min_keyword_matches < 1:
        raise ValueError(f"min_keyword_matches must be >= 1, got {min_keyword_matches}")
    traditions = load_all_methodology_traditions(project_dir)

    scored: list[tuple[MethodologyTraditionSpec, int]] = []
    for spec in traditions:
        hits = _count_keyword_matches(plan_text, spec)
        if hits >= min_keyword_matches:
            scored.append((spec, hits))

    # Lower priority number = higher precedence (priority=1 outranks priority=999)
    scored.sort(key=lambda pair: (pair[0].priority, pair[0].name))
    candidate_set = tuple(spec.name for spec, _ in scored)

    if len(candidate_set) == 0:
        return TraditionRouterResult(
            primary_tradition=None,
            applied_union_rules=(),
            precedence_trace="stage1_no_match_fallback",
            candidate_set=(),
        )

    if len(candidate_set) == 1:
        return TraditionRouterResult(
            primary_tradition=candidate_set[0],
            applied_union_rules=(),
            precedence_trace="stage1_single_match",
            candidate_set=candidate_set,
        )

    effective_rules = union_rules or []
    candidate_names = set(candidate_set)
    resolved, applied, trace = _try_union_rules(candidate_names, effective_rules)
    if resolved is not None:
        return TraditionRouterResult(
            primary_tradition=resolved,
            applied_union_rules=applied,
            precedence_trace=trace,
            candidate_set=candidate_set,
        )

    if resolve_by_priority:
        return TraditionRouterResult(
            primary_tradition=candidate_set[0],
            applied_union_rules=(),
            precedence_trace="stage1_multi_match_resolved_by_priority",
            candidate_set=candidate_set,
        )

    return TraditionRouterResult(
        primary_tradition=None,
        applied_union_rules=(),
        precedence_trace="stage1_multi_match",
        candidate_set=candidate_set,
    )
