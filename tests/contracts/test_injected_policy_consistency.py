"""Contracts on the relationship between co-injected Codex policy texts (#4351).

Every existing guard asserts a property of one constant in isolation. These tests
assert properties of the *relationship* between the co-injected texts: disjoint
subjects, an honest scope marker, a byte budget on both the intake digest and the
composed suffix, and — since #4478's delivery-scoping remediation — that the
universal (session-mechanics) and change-authoring (scope-discipline) channels
stay properly partitioned and that the universal channel stays task-neutral.
"""

from __future__ import annotations

import itertools
import tomllib

import pytest

from autoskillit.core import (
    CODEX_DISCIPLINE_SUFFIX_BYTE_BUDGET,
    CODEX_INTAKE_DISCIPLINE_BYTE_BUDGET,
    CODEX_INTAKE_DISCIPLINE_DIGEST,
    CODEX_SCOPE_DISCIPLINE_BYTE_BUDGET,
    CODEX_SCOPE_DISCIPLINE_DIGEST,
)
from autoskillit.execution.backends._claude_prompt import (
    _CO_INJECTED_POLICY_TEXTS,
    CODEX_CO_INJECTED_POLICIES,
    codex_discipline_suffix,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


@pytest.mark.parametrize("include_scope", [False, True], ids=["universal", "include-scope"])
def test_composed_suffix_contains_no_triple_quote(include_scope: bool) -> None:
    assert "'''" not in codex_discipline_suffix(include_scope=include_scope)


@pytest.mark.parametrize("include_scope", [False, True], ids=["universal", "include-scope"])
def test_composed_suffix_round_trips_through_agent_toml_literal(include_scope: bool) -> None:
    suffix = codex_discipline_suffix(include_scope=include_scope)
    literal = f"developer_instructions = '''\n{suffix}\n'''\n"
    parsed = tomllib.loads(literal)
    # TOML trims a newline immediately following the opening ''' delimiter but
    # keeps the trailing one — this must match what _generate_agent_tomls writes.
    assert parsed["developer_instructions"] == f"{suffix}\n"


def test_intake_digest_within_byte_budget() -> None:
    assert (
        len(CODEX_INTAKE_DISCIPLINE_DIGEST.encode("utf-8")) <= CODEX_INTAKE_DISCIPLINE_BYTE_BUDGET
    )


def test_scope_digest_within_byte_budget() -> None:
    assert len(CODEX_SCOPE_DISCIPLINE_DIGEST.encode("utf-8")) <= CODEX_SCOPE_DISCIPLINE_BYTE_BUDGET


def test_composed_suffix_within_byte_budget() -> None:
    """The universal form (agent TOMLs, food trucks, default resumes) stays bounded."""
    assert len(codex_discipline_suffix().encode("utf-8")) <= CODEX_DISCIPLINE_SUFFIX_BYTE_BUDGET


def test_composed_suffix_with_scope_within_byte_budget() -> None:
    """The change-authoring form (interactive, scoped skill sessions) has its own ceiling."""
    combined_budget = CODEX_DISCIPLINE_SUFFIX_BYTE_BUDGET + CODEX_SCOPE_DISCIPLINE_BYTE_BUDGET
    assert len(codex_discipline_suffix(include_scope=True).encode("utf-8")) <= combined_budget


def test_universal_suffix_excludes_change_authoring_policies() -> None:
    assert CODEX_SCOPE_DISCIPLINE_DIGEST not in codex_discipline_suffix()
    assert CODEX_SCOPE_DISCIPLINE_DIGEST in codex_discipline_suffix(include_scope=True)


def test_delivery_matrix_partitions_policies() -> None:
    universal = {
        entry.constant_name
        for entry in CODEX_CO_INJECTED_POLICIES
        if entry.delivery == "universal"
    }
    change_authoring = {
        entry.constant_name
        for entry in CODEX_CO_INJECTED_POLICIES
        if entry.delivery == "change-authoring"
    }
    assert universal == {
        "OUTPUT_DISCIPLINE_DIGEST",
        "CODEX_INTAKE_DISCIPLINE_DIGEST",
        "CODEX_RECIPE_DELIVERY_CALLING_CONTRACT",
    }
    assert change_authoring == {"CODEX_SCOPE_DISCIPLINE_DIGEST"}


# Task-shaped anchors that a change-authoring policy is expected to use but a
# universal (session-mechanics) policy must not — presupposing a plan, a diff, or
# a scope-stop mechanism most session types don't have would make it task-specific.
_TASK_SHAPED_MARKERS: tuple[str, ...] = (
    "split proposal",
    "added lines",
    "In plans",
    "size_budget",
    "scope_verdict",
    "SCOPE DISCIPLINE",
)


def test_universal_policy_texts_are_task_neutral() -> None:
    """Forces the next task-shaped policy off the universal channel by explicit decision."""
    for entry in CODEX_CO_INJECTED_POLICIES:
        if entry.delivery != "universal":
            continue
        live_text = _CO_INJECTED_POLICY_TEXTS[entry.constant_name]
        offending = [marker for marker in _TASK_SHAPED_MARKERS if marker in live_text]
        assert not offending, (
            f"{entry.constant_name!r} is declared 'universal' but contains task-shaped "
            f"markers {offending} — either the text should be scoped to 'change-authoring' "
            "delivery, or the marker doesn't belong in a universal policy"
        )


def test_co_injected_policies_claim_disjoint_subjects() -> None:
    for entry_a, entry_b in itertools.combinations(CODEX_CO_INJECTED_POLICIES, 2):
        overlap = entry_a.subjects & entry_b.subjects
        assert overlap == frozenset(), (
            f"{entry_a.constant_name!r} and {entry_b.constant_name!r} claim overlapping "
            f"subjects: {overlap}"
        )


def test_every_co_injected_text_evidences_its_declared_scope() -> None:
    for entry in CODEX_CO_INJECTED_POLICIES:
        live_text = _CO_INJECTED_POLICY_TEXTS[entry.constant_name]
        assert entry.scope_marker in live_text, (
            f"{entry.constant_name!r} scope_marker {entry.scope_marker!r} is not present in "
            "the live constant text"
        )


def test_subject_matrix_covers_every_co_injected_text() -> None:
    expected = {
        "OUTPUT_DISCIPLINE_DIGEST",
        "CODEX_INTAKE_DISCIPLINE_DIGEST",
        "CODEX_RECIPE_DELIVERY_CALLING_CONTRACT",
        "CODEX_SCOPE_DISCIPLINE_DIGEST",
    }
    assert {entry.constant_name for entry in CODEX_CO_INJECTED_POLICIES} == expected
    assert set(_CO_INJECTED_POLICY_TEXTS.keys()) == expected
