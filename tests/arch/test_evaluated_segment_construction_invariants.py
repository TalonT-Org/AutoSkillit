"""Lock in EvaluatedSegment construction invariants from PR #5071 resolve-review.

The sole sanctioned projection in ``_interpreters._iter_evaluated_segments``
already guarantees non-empty tokens and tokens/provenance parity. These
tests pin those invariants at construction so any future regression in the
projection (or any direct call to ``EvaluatedSegment(...)`` from a test or
newly-added helper) trips loudly.
"""

from __future__ import annotations

import pytest

from autoskillit.hooks._classification._tokenizer import (
    ArgvToken,
    EvaluatedSegment,
    _CommandSegment,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_evaluated_segment_rejects_empty_token_list() -> None:
    """Empty tokens would let a malformed segment reach downstream consumers."""
    with pytest.raises(ValueError, match="non-empty token list"):
        EvaluatedSegment(tokens=[], provenance=None)


def test_evaluated_segment_rejects_tokens_provenance_mismatch() -> None:
    """When provenance is set, tokens must equal provenance.tokens."""
    provenance = _CommandSegment(
        tokens=["git", "status"],
        redirect_syntax=[False, False],
        argv_tokens=[
            ArgvToken(text="git", fully_single_quoted=False, raw_span="git"),
            ArgvToken(text="status", fully_single_quoted=False, raw_span="status"),
        ],
    )
    with pytest.raises(ValueError, match="must match provenance.tokens"):
        EvaluatedSegment(tokens=["git", "show"], provenance=provenance)


def test_evaluated_segment_accepts_matching_provenance() -> None:
    """The happy path: tokens equal provenance.tokens."""
    provenance = _CommandSegment(
        tokens=["git", "status"],
        redirect_syntax=[False, False],
        argv_tokens=[
            ArgvToken(text="git", fully_single_quoted=False, raw_span="git"),
            ArgvToken(text="status", fully_single_quoted=False, raw_span="status"),
        ],
    )
    segment = EvaluatedSegment(tokens=["git", "status"], provenance=provenance)
    assert segment.tokens == provenance.tokens


def test_evaluated_segment_accepts_provenance_none() -> None:
    """Evaluated payloads (shell substitutions) carry no provenance."""
    segment = EvaluatedSegment(tokens=["echo", "hello"], provenance=None)
    assert segment.provenance is None
