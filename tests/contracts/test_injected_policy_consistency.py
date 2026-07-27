"""Contracts on the relationship between co-injected Codex policy texts (#4351).

Every existing guard asserts a property of one constant in isolation. These tests
assert properties of the *relationship* between the co-injected texts: disjoint
subjects, an honest scope marker, and a byte budget on both the intake digest and
the composed suffix.
"""

from __future__ import annotations

import itertools
import tomllib

import pytest

from autoskillit.core import (
    CODEX_DISCIPLINE_SUFFIX_BYTE_BUDGET,
    CODEX_INTAKE_DISCIPLINE_BYTE_BUDGET,
    CODEX_INTAKE_DISCIPLINE_DIGEST,
)
from autoskillit.execution.backends._claude_prompt import (
    _CO_INJECTED_POLICY_TEXTS,
    CODEX_CO_INJECTED_POLICIES,
    codex_discipline_suffix,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_composed_suffix_contains_no_triple_quote() -> None:
    assert "'''" not in codex_discipline_suffix()


def test_composed_suffix_round_trips_through_agent_toml_literal() -> None:
    suffix = codex_discipline_suffix()
    literal = f"developer_instructions = '''\n{suffix}\n'''\n"
    parsed = tomllib.loads(literal)
    # TOML trims a newline immediately following the opening ''' delimiter but
    # keeps the trailing one — this must match what _generate_agent_tomls writes.
    assert parsed["developer_instructions"] == f"{suffix}\n"


def test_intake_digest_within_byte_budget() -> None:
    assert (
        len(CODEX_INTAKE_DISCIPLINE_DIGEST.encode("utf-8")) <= CODEX_INTAKE_DISCIPLINE_BYTE_BUDGET
    )


def test_composed_suffix_within_byte_budget() -> None:
    assert len(codex_discipline_suffix().encode("utf-8")) <= CODEX_DISCIPLINE_SUFFIX_BYTE_BUDGET


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
    }
    assert {entry.constant_name for entry in CODEX_CO_INJECTED_POLICIES} == expected
    assert set(_CO_INJECTED_POLICY_TEXTS.keys()) == expected
