"""Unit tests for declared managed skill discovery routes."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _route(**overrides: object):
    from autoskillit.core import (
        SkillDiscoveryMechanism,
        SkillDiscoveryRouteDef,
        UpstreamSupportStatus,
    )

    values: dict[str, object] = {
        "name": "test-route",
        "mechanism": SkillDiscoveryMechanism.CODEX_HOME_SKILLS,
        "upstream_status": UpstreamSupportStatus.DEPRECATED,
        "tracking_issue": 4717,
        "catalog_relpath": "add-dir/skills",
        "discovery_root_relpath": "skills",
        "upstream_citation": "upstream/path:1@revision",
    }
    values.update(overrides)
    return SkillDiscoveryRouteDef(**values)


def test_route_status_requires_tracking_issue_exactly_for_deprecation() -> None:
    from autoskillit.core import UpstreamSupportStatus

    expected = "tracking_issue is required exactly for deprecated routes"
    with pytest.raises(ValueError) as deprecated_error:
        _route(tracking_issue=None)
    with pytest.raises(ValueError) as supported_error:
        _route(upstream_status=UpstreamSupportStatus.SUPPORTED)

    assert str(deprecated_error.value) == expected
    assert str(supported_error.value) == expected


@pytest.mark.parametrize("relpath", ("", "/skills", "add-dir/../skills"))
def test_route_rejects_empty_absolute_or_parent_catalog_paths(relpath: str) -> None:
    with pytest.raises(ValueError, match="relative POSIX path"):
        _route(catalog_relpath=relpath)


@pytest.mark.parametrize("relpath", ("", "/skills", "../skills"))
def test_route_rejects_empty_absolute_or_parent_discovery_paths(relpath: str) -> None:
    with pytest.raises(ValueError, match="relative POSIX path"):
        _route(discovery_root_relpath=relpath)


def test_alias_target_is_relative_to_declared_discovery_root() -> None:
    assert _route().alias_target == "add-dir/skills"


def test_alias_target_rejects_route_without_an_alias_entry_point() -> None:
    with pytest.raises(ValueError, match="does not declare an alias"):
        _route(discovery_root_relpath=None).alias_target


def test_declared_paths_are_resolved_from_launch_home() -> None:
    home = Path("/tmp/session-home")
    direct_route = _route(discovery_root_relpath=None)

    assert direct_route.discovery_root(home) is None
    assert direct_route.catalog_dir(home) == home / "add-dir/skills"


def test_backend_conventions_rejects_route_with_different_catalog_layout() -> None:
    from autoskillit.core import BackendConventions

    with pytest.raises(ValueError) as error:
        BackendConventions(
            skills_subdir=Path("skills"),
            managed_skill_discovery=_route(catalog_relpath="different/skills"),
        )

    assert str(error.value) == "catalog 'different/skills' must match 'add-dir/skills'"
