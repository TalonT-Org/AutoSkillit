"""Pure tests for cumulative quota-constraint selection."""

from __future__ import annotations

import pytest

from autoskillit.quota_constraints import (
    QuotaConstraint,
    QuotaEvidenceSource,
    effective_quota_block,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _constraint(source, scope, deadline, window=""):
    return QuotaConstraint(source, scope, deadline, 100, window)


@pytest.mark.parametrize(
    ("first", "second", "expected_source"),
    [
        (
            QuotaEvidenceSource.PROVIDER_POLL,
            QuotaEvidenceSource.OBSERVED_TERMINAL,
            QuotaEvidenceSource.OBSERVED_TERMINAL,
        ),
        (
            QuotaEvidenceSource.OBSERVED_TERMINAL,
            QuotaEvidenceSource.PROVIDER_POLL,
            QuotaEvidenceSource.PROVIDER_POLL,
        ),
    ],
)
def test_latest_live_deadline_wins_across_sources(first, second, expected_source) -> None:
    winner = effective_quota_block(
        [_constraint(first, "acct", 200), _constraint(second, "acct", 300)],
        account_scope="acct",
        now_epoch=150,
    )
    assert winner is not None
    assert winner.source is expected_source
    assert winner.blocked_until_epoch == 300


def test_expired_wrong_scope_and_empty_constraints_do_not_block() -> None:
    assert effective_quota_block([], account_scope="acct", now_epoch=100) is None
    assert (
        effective_quota_block(
            [_constraint(QuotaEvidenceSource.OBSERVED_TERMINAL, "acct", 99)],
            account_scope="acct",
            now_epoch=100,
        )
        is None
    )
    assert (
        effective_quota_block(
            [_constraint(QuotaEvidenceSource.OBSERVED_TERMINAL, "other", 200)],
            account_scope="acct",
            now_epoch=100,
        )
        is None
    )


def test_absent_nonblocking_poll_cannot_cancel_observed_terminal() -> None:
    observed = _constraint(QuotaEvidenceSource.OBSERVED_TERMINAL, "acct", 300, "seven_day")
    assert effective_quota_block([observed], account_scope="acct", now_epoch=100) == observed
