"""Methodology tradition disambiguation — resolves multi-tradition candidate sets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from autoskillit.core import load_yaml, pkg_root

BUNDLED_METHODOLOGY_TRADITIONS_DIR: Path = pkg_root() / "recipes" / "methodology-traditions"


@dataclass(frozen=True)
class DisambiguationExceptionDef:
    when_present: str
    add_union_rules: tuple[str, ...]


@dataclass(frozen=True)
class DisambiguationRuleDef:
    name: str
    order: int
    description: str
    trigger_traditions: frozenset[str]
    trigger_mode: Literal["anchor_plus_any", "all_present"]
    anchor: str | None
    primary_tradition: str
    applied_union_rules: tuple[str, ...]
    exceptions: tuple[DisambiguationExceptionDef, ...]


@dataclass(frozen=True)
class CrossTraditionOverlapDef:
    name: str
    order: int
    description: str
    trigger_traditions: frozenset[str]
    primary_tradition: str
    applied_union_rules: tuple[str, ...]
    overrides_primary: bool = False


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
    try:
        data = load_yaml(yaml_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to load disambiguation rules from {yaml_path}") from exc

    rules: list[DisambiguationRuleDef] = []
    for idx, r in enumerate(data.get("disambiguation_rules", [])):
        try:
            exceptions = tuple(
                DisambiguationExceptionDef(
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
        except (KeyError, TypeError) as exc:
            name = r.get("name", f"<index {idx}>") if isinstance(r, dict) else f"<index {idx}>"
            raise RuntimeError(f"Invalid disambiguation_rules entry {name!r}: {exc}") from exc

    overlaps: list[CrossTraditionOverlapDef] = []
    for idx, o in enumerate(data.get("cross_tradition_overlaps", [])):
        try:
            overlaps.append(
                CrossTraditionOverlapDef(
                    name=o["name"],
                    order=o["order"],
                    description=o["description"],
                    trigger_traditions=frozenset(o["trigger_traditions"]),
                    primary_tradition=o["primary_tradition"],
                    applied_union_rules=tuple(o["applied_union_rules"]),
                    overrides_primary=bool(o.get("overrides_primary", False)),
                )
            )
        except (KeyError, TypeError) as exc:
            name = o.get("name", f"<index {idx}>") if isinstance(o, dict) else f"<index {idx}>"
            raise RuntimeError(f"Invalid cross_tradition_overlaps entry {name!r}: {exc}") from exc

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

    for overlap in sorted(overlaps, key=lambda o: o.order):
        if overlap.trigger_traditions.issubset(candidate_names):
            union_rules.extend(overlap.applied_union_rules)
            trace_parts.append(f"overlap_{overlap.name}")
            if primary is None or overlap.overrides_primary:
                primary = overlap.primary_tradition
                trace_parts.append(f"overlap_primary_{overlap.name}")

    if primary is None:
        primary = sorted_candidates[0]
        trace_parts.append("fallthrough_priority")

    return DisambiguationResult(
        primary_tradition=primary,
        applied_union_rules=tuple(union_rules),
        precedence_trace="+".join(trace_parts) if trace_parts else "no_match",
        candidate_set=sorted_candidates,
    )
