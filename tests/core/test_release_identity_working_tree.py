"""WORKING_TREE freshness criterion — core/_release_identity.py."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    AdvanceVerdict,
    ReleaseChannel,
    ReleaseIdentity,
    advance_verdict,
    update_available,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _wt(version: str) -> ReleaseIdentity:
    return ReleaseIdentity(ReleaseChannel.WORKING_TREE, version=version)


def test_update_available_true_for_newer_source_version() -> None:
    assert update_available(_wt("1.0.0"), _wt("1.1.0")) is True


def test_update_available_false_for_older_source_version() -> None:
    assert update_available(_wt("1.1.0"), _wt("1.0.0")) is False


def test_update_available_false_for_equal_source_version() -> None:
    assert update_available(_wt("1.0.0"), _wt("1.0.0")) is False


def test_advance_verdict_not_applicable_without_target() -> None:
    verdict = advance_verdict(previous=_wt("1.0.0"), observed=_wt("1.1.0"), target=None)
    assert verdict is AdvanceVerdict.NOT_APPLICABLE


def test_advance_verdict_unchanged_when_observed_matches_previous() -> None:
    verdict = advance_verdict(previous=_wt("1.0.0"), observed=_wt("1.0.0"), target=_wt("1.1.0"))
    assert verdict is AdvanceVerdict.UNCHANGED


def test_advance_verdict_advanced_when_observed_matches_target() -> None:
    verdict = advance_verdict(previous=_wt("1.0.0"), observed=_wt("1.1.0"), target=_wt("1.1.0"))
    assert verdict is AdvanceVerdict.ADVANCED


def test_advance_verdict_diverged_when_observed_matches_neither() -> None:
    verdict = advance_verdict(previous=_wt("1.0.0"), observed=_wt("1.2.0"), target=_wt("1.1.0"))
    assert verdict is AdvanceVerdict.DIVERGED_FROM_TARGET


def test_update_available_invalid_version_wraps_with_channel_context() -> None:
    """Malformed WORKING_TREE versions raise ValueError naming the channel."""
    from packaging.version import InvalidVersion

    with pytest.raises(ValueError, match="working-tree") as exc:
        update_available(_wt("not-a-version!!"), _wt("1.0.0"))
    assert isinstance(exc.value.__cause__, InvalidVersion)
