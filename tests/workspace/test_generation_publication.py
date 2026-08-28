"""Generation-publication filesystem-race regressions."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from autoskillit.core import generation_store_root, managed_home_for

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]

_PLUGIN_REF = "autoskillit@autoskillit-local"


@pytest.mark.parametrize(
    ("vanishing_directory", "vanished_error"),
    [("store", FileNotFoundError), ("version", NotADirectoryError)],
)
def test_prune_stale_generations_skips_a_disappearing_enumeration_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vanishing_directory: str,
    vanished_error: type[OSError],
) -> None:
    """Enumeration loss is fail-open before any generation is reconciled."""
    import autoskillit.core.fs_observation as fs_observation
    from autoskillit.workspace._projected_artifact import _generation_publication as publication

    home = managed_home_for(tmp_path)
    store_root = generation_store_root(home.root, _PLUGIN_REF)
    version_root = store_root / "1.0.0"
    version_root.mkdir(parents=True)
    vanishing_path = store_root if vanishing_directory == "store" else version_root
    original_scandir = os.scandir
    original_iterdir = Path.iterdir

    def disappearing_scandir(path):  # type: ignore[no-untyped-def]
        if path == vanishing_path:
            raise vanished_error("injected disappearance")
        return original_scandir(path)

    def disappearing_iterdir(self: Path) -> Iterator[Path]:
        if self == vanishing_path:
            raise vanished_error("unexpected bare-path enumeration")
        return original_iterdir(self)

    monkeypatch.setattr(fs_observation.os, "scandir", disappearing_scandir)
    monkeypatch.setattr(Path, "iterdir", disappearing_iterdir)

    with publication._InstallLock(home):
        assert publication.prune_stale_generations(home, _PLUGIN_REF) == 0
