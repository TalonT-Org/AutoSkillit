"""Release availability and upgrade advancement must use one criterion."""

from __future__ import annotations

from typing import assert_never

import pytest

from autoskillit.core import (
    AdvanceVerdict,
    ReleaseChannel,
    ReleaseIdentity,
    advance_verdict,
    update_available,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _representative_pairs(
    channel: ReleaseChannel,
) -> tuple[tuple[ReleaseIdentity, ReleaseIdentity], ...]:
    match channel:
        case ReleaseChannel.RELEASED:
            installed = ReleaseIdentity(channel, version="1.0.0")
            return (
                (installed, ReleaseIdentity(channel, version="1.1.0")),
                (installed, ReleaseIdentity(channel, version="1.0.0")),
            )
        case ReleaseChannel.BRANCH:
            installed = ReleaseIdentity(channel, version="1.0.0", commit="a" * 40, ref="develop")
            return (
                (
                    installed,
                    ReleaseIdentity(channel, version="1.0.0", commit="b" * 40, ref="develop"),
                ),
                (installed, installed),
            )
        case ReleaseChannel.WORKING_TREE:
            installed = ReleaseIdentity(channel, version="1.0.0")
            return ((installed, ReleaseIdentity(channel, version="1.1.0")),)
        case unhandled:
            assert_never(unhandled)


@pytest.mark.parametrize("channel", list(ReleaseChannel))
def test_available_update_is_always_satisfiable(channel: ReleaseChannel) -> None:
    for installed, target in _representative_pairs(channel):
        verdict = advance_verdict(previous=installed, observed=target, target=target)
        if update_available(installed, target):
            assert verdict == AdvanceVerdict.ADVANCED
        else:
            assert verdict in (AdvanceVerdict.UNCHANGED, AdvanceVerdict.NOT_APPLICABLE)
