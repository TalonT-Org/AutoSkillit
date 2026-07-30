"""Clause-scoped assertions for fleet retry-disposition prompt contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequiredClause:
    clause_id: str
    text: str


REQUIRED_PROVENANCE_CLAUSES: tuple[RequiredClause, ...] = (
    RequiredClause("fresh-only-on-proof", "[fresh-only-on-proof]"),
    RequiredClause("resume-confirmed-effect", "[resume-confirmed-effect]"),
    RequiredClause("reconcile-ambiguity", "[reconcile-ambiguity]"),
    RequiredClause("missing-provenance-fails-closed", "[missing-provenance-fails-closed]"),
    RequiredClause("cleanup-is-orthogonal", "[cleanup-is-orthogonal]"),
    RequiredClause("remote-effects-survive-cleanup", "[remote-effects-survive-cleanup]"),
)


def infrastructure_recovery_section(prompt_source: str) -> str:
    marker = "## INFRASTRUCTURE FAILURE RECOVERY"
    start = prompt_source.index(marker)
    next_section = prompt_source.find("\n## ", start + len(marker))
    return prompt_source[start:] if next_section == -1 else prompt_source[start:next_section]


def assert_provenance_contract(prompt_source: str) -> None:
    section = infrastructure_recovery_section(prompt_source)
    for clause in REQUIRED_PROVENANCE_CLAUSES:
        if clause.text not in section:
            raise AssertionError(f"{clause.clause_id}: missing {clause.text!r}")
