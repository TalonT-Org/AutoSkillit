"""Methodology tradition disambiguation — resolves multi-tradition candidate sets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import load_yaml

BUNDLED_METHODOLOGY_TRADITIONS_DIR: Path = (
    Path(__file__).parent.parent / "recipes" / "methodology-traditions"
)


@dataclass(frozen=True)
class DisambiguationException:
    when_present: str
    add_union_rules: tuple[str, ...]


@dataclass(frozen=True)
class DisambiguationRuleDef:
    name: str
    order: int
    description: str
    trigger_traditions: frozenset[str]
    trigger_mode: str
    anchor: str | None
    primary_tradition: str
    applied_union_rules: tuple[str, ...]
    exceptions: tuple[DisambiguationException, ...]


@dataclass(frozen=True)
class CrossTraditionOverlapDef:
    name: str
    order: int
    description: str
    trigger_traditions: frozenset[str]
    primary_tradition: str
    applied_union_rules: tuple[str, ...]


@dataclass(frozen=True)
class DisambiguationResult:
    primary_tradition: str | None
    applied_union_rules: tuple[str, ...]
    precedence_trace: str
    candidate_set: tuple[str, ...]


def load_disambiguation_rules() -> tuple[
    list[DisambiguationRuleDef], list[CrossTraditionOverlapDef]
]:
    yaml_path = BUNDLED_METHODOLOGY_TRADITIONS_DIR / "_disambiguation.yaml"
    data = load_yaml(yaml_path)

    rules: list[DisambiguationRuleDef] = []
    for r in data.get("disambiguation_rules", []):
        exceptions = tuple(
            DisambiguationException(
                when_present=e["when_present"],
                add_union_rules=tuple(e["add_union_rules"]),
            )
            for e in r.get("exceptions", [])
        )
        rules.append(
            DisambiguationRuleDef(
                name=r["name"],
                order=r["order"],
                description=r["description"],
                trigger_traditions=frozenset(r["trigger_traditions"]),
                trigger_mode=r["trigger_mode"],
                anchor=r.get("anchor"),
                primary_tradition=r["primary_tradition"],
                applied_union_rules=tuple(r["applied_union_rules"]),
                exceptions=exceptions,
            )
        )

    overlaps: list[CrossTraditionOverlapDef] = []
    for o in data.get("cross_tradition_overlaps", []):
        overlaps.append(
            CrossTraditionOverlapDef(
                name=o["name"],
                order=o["order"],
                description=o["description"],
                trigger_traditions=frozenset(o["trigger_traditions"]),
                primary_tradition=o["primary_tradition"],
                applied_union_rules=tuple(o["applied_union_rules"]),
            )
        )

    rules.sort(key=lambda r: r.order)
    overlaps.sort(key=lambda o: o.order)

    return rules, overlaps


def _rule_matches(rule: DisambiguationRuleDef, candidates: set[str]) -> bool:
    if rule.trigger_mode == "anchor_plus_any":
        return rule.anchor is not None and rule.anchor in candidates and len(candidates) >= 2
    elif rule.trigger_mode == "all_present":
        return rule.trigger_traditions.issubset(candidates)
    return False


def disambiguate(
    candidate_names: set[str],
    *,
    rules: list[DisambiguationRuleDef] | None = None,
    overlaps: list[CrossTraditionOverlapDef] | None = None,
    tradition_priority: dict[str, int] | None = None,
) -> DisambiguationResult:
    if not candidate_names:
        return DisambiguationResult(
            primary_tradition=None,
            applied_union_rules=(),
            precedence_trace="no_candidates",
            candidate_set=(),
        )

    if len(candidate_names) == 1:
        name = next(iter(candidate_names))
        return DisambiguationResult(
            primary_tradition=name,
            applied_union_rules=(),
            precedence_trace="single_candidate",
            candidate_set=(name,),
        )

    if rules is None or overlaps is None:
        loaded_rules, loaded_overlaps = load_disambiguation_rules()
        if rules is None:
            rules = loaded_rules
        if overlaps is None:
            overlaps = loaded_overlaps

    if tradition_priority is None:
        from autoskillit.recipe.methodology_tradition_registry import (
            load_all_methodology_traditions,
        )

        tradition_priority = {s.name: s.priority for s in load_all_methodology_traditions()}

    primary: str | None = None
    union_rules: list[str] = []
    trace_parts: list[str] = []
    sorted_candidates = tuple(
        sorted(candidate_names, key=lambda n: (tradition_priority.get(n, 999), n))
    )

    # Phase 1: Sequential rule check (first match wins)
    for rule in sorted(rules, key=lambda r: r.order):
        if _rule_matches(rule, candidate_names):
            primary = rule.primary_tradition
            union_rules.extend(rule.applied_union_rules)
            trace_parts.append(f"rule_{rule.name}")
            for exc in rule.exceptions:
                if exc.when_present in candidate_names:
                    union_rules.extend(exc.add_union_rules)
                    trace_parts.append(f"exception_{exc.when_present}")
            break

    # Phase 2: Overlap check (all matching overlaps accumulate)
    for overlap in sorted(overlaps, key=lambda o: o.order):
        if overlap.trigger_traditions.issubset(candidate_names):
            union_rules.extend(overlap.applied_union_rules)
            trace_parts.append(f"overlap_{overlap.name}")
            if primary is None:
                primary = overlap.primary_tradition
                trace_parts.append(f"overlap_primary_{overlap.name}")

    # Phase 3: Fallthrough
    if primary is None:
        primary = sorted_candidates[0]
        trace_parts.append("fallthrough_priority")

    return DisambiguationResult(
        primary_tradition=primary,
        applied_union_rules=tuple(union_rules),
        precedence_trace="+".join(trace_parts) if trace_parts else "no_match",
        candidate_set=sorted_candidates,
    )
