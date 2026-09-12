"""Focused edge cases for the acceptance-policy checker."""

from __future__ import annotations

import pytest

from scripts import check_policy_relaxation as check

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_exemption_rejects_unknown_keyword() -> None:
    surface = check.PolicySurface("p.py", "EXEMPTIONS", "exemption_map")

    with pytest.raises(check.UnsupportedSurfaceShape, match="unexpected exemption argument"):
        check.extract_surface_values(
            'EXEMPTIONS = {"entry": Exemption(limit=1, owner="team")}',
            surface,
        )
