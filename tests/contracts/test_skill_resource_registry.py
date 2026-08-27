"""Registry contracts for packaged skill resources."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from autoskillit.workspace.skill_resources import load_skill_resource

from autoskillit.core import RETIRED_SKILL_RESOURCE_IDS, SkillContractError, pkg_root
from autoskillit.workspace._projection_cache import iter_public_plugin_asset_files
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_RESOURCE_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


def _resource_paths() -> tuple[Path, ...]:
    return tuple(sorted((pkg_root() / "skill_resources").glob("*.md")))


def _declared_resource_ids() -> dict[str, set[str]]:
    consumers: dict[str, set[str]] = {}
    for skill in DefaultSkillResolver().list_all():
        for resource_id in skill.required_resources:
            consumers.setdefault(resource_id, set()).add(skill.name)
    return consumers


def test_packaged_skill_resources_are_valid_and_match_their_filenames() -> None:
    """Every packaged resource has one valid, discoverable canonical contract."""
    paths = _resource_paths()
    assert paths, "expected at least one packaged skill resource"

    for path in paths:
        resource = load_skill_resource(path.stem)
        assert _RESOURCE_ID.fullmatch(resource.id), f"invalid resource id: {resource.id!r}"
        assert resource.id == path.stem, f"{path}: id must match the filename stem"
        assert isinstance(resource.title, str) and resource.title.strip(), f"{path}: empty title"
        assert isinstance(resource.summary, str) and resource.summary.strip(), (
            f"{path}: empty summary"
        )
        assert resource.body.strip(), f"{path}: resource body must not be empty"
        assert resource.digest, f"{path}: resource digest must be source-derived"


def test_duplicate_discovered_resource_ids_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate frontmatter ids cannot silently pick one resource file."""
    import autoskillit.workspace.skill_resources as resource_module

    resource_dir = tmp_path / "skill_resources"
    resource_dir.mkdir()
    resource_text = (
        "---\nid: duplicate\ntitle: Duplicate\nsummary: Duplicate fixture.\n---\nfixture body\n"
    )
    (resource_dir / "duplicate.md").write_text(resource_text, encoding="utf-8")
    (resource_dir / "other.md").write_text(resource_text, encoding="utf-8")
    monkeypatch.setattr(resource_module, "pkg_root", lambda: tmp_path)
    load_skill_resource.cache_clear()
    try:
        with pytest.raises(SkillContractError, match="duplicate"):
            load_skill_resource("duplicate")
    finally:
        load_skill_resource.cache_clear()


def test_every_declared_resource_resolves_and_every_resource_has_a_consumer() -> None:
    """Resource files are shared policy/data, never unowned packaged artifacts."""
    consumers = _declared_resource_ids()
    discovered_ids = {path.stem for path in _resource_paths()}

    for resource_id in consumers:
        assert load_skill_resource(resource_id).id == resource_id
    assert discovered_ids <= set(consumers), (
        "Orphan skill resource(s) without a declaring consumer: "
        f"{sorted(discovered_ids - set(consumers))}"
    )


def test_no_retired_skill_resource_id_has_a_live_file() -> None:
    """A retired id cannot be revived by a packaged resource file."""
    live_ids = {path.stem for path in _resource_paths()}
    assert not RETIRED_SKILL_RESOURCE_IDS & live_ids, (
        "Retired skill resource id(s) still have live files: "
        f"{sorted(RETIRED_SKILL_RESOURCE_IDS & live_ids)}"
    )


def test_skill_resources_are_not_public_plugin_assets() -> None:
    """Resource bodies are projection-only literal reference data."""
    public_paths = {
        path.relative_to(pkg_root()).as_posix()
        for path in iter_public_plugin_asset_files(pkg_root())
    }
    assert not any(path.startswith("skill_resources/") for path in public_paths)
