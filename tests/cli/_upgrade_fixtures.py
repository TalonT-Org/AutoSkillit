"""Upgrade matrix: pre-existing on-disk states an install can land on top of.

Every contract test over `~/.autoskillit/` builds it fresh in `tmp_path`, so the
whole suite only ever exercised *install-from-nothing*. In production that
directory had existed since 2026-02-19 and had been a symlink since 2026-07-20 —
a state no test could reach, and the one that broke `autoskillit install` for
every install created before 0.10.892.

`legacy_home` seeds `tmp_path` **before** `Path.home` is patched, so a test
parameterized over it runs against each shape a real machine can actually be in.

Add a case here whenever a release changes an install artifact's shape; pair it
with a `RETIRED_INSTALL_ARTIFACT_SHAPES` entry so the reconciler repairs it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

__all__ = ["CONTAINED_STATES", "LEGACY_HOME_STATES", "legacy_home", "seed_legacy_home"]


def _marketplace_plugin_root(home: Path) -> Path:
    return home / ".autoskillit" / "marketplace" / "plugins" / "autoskillit"


def _clean(home: Path) -> None:
    """Today's only tested state: nothing pre-exists."""


def _legacy_symlink(home: Path) -> None:
    """The plugin root is a symlink into the package root (pre-0.10.892).

    Reproduces F1 exactly: `Path.resolve()` follows the final component, so the
    destination resolves onto its own source root and the containment guard
    rejects a write it should have allowed.
    """
    from autoskillit.core import pkg_root

    root = _marketplace_plugin_root(home)
    root.parent.mkdir(parents=True, exist_ok=True)
    root.symlink_to(pkg_root())


def _legacy_symlink_dangling(home: Path) -> None:
    """Same shape, but the symlink target no longer exists."""
    root = _marketplace_plugin_root(home)
    root.parent.mkdir(parents=True, exist_ok=True)
    root.symlink_to(home / "gone" / "nowhere")


def _real_dir_stale_version(home: Path) -> None:
    """A materialized plugin root left over from an older release."""
    import json

    root = _marketplace_plugin_root(home)
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "autoskillit", "version": "0.0.1"})
    )
    (root / "skills").mkdir(exist_ok=True)
    manifest_dir = home / ".autoskillit" / "marketplace" / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "marketplace.json").write_text(
        json.dumps({"name": "autoskillit-local", "plugins": [{"version": "0.0.1"}]})
    )


def _plain_file(home: Path) -> None:
    """A regular file where the plugin directory belongs."""
    root = _marketplace_plugin_root(home)
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_text("not a directory")


def _symlinked_ancestor(home: Path) -> None:
    """The destination's *parent* is a symlink into the source root.

    The adversarial control. The corrected containment predicate narrows the
    guard to the destination's own location; it must still reject a destination
    that genuinely lands inside the source root by way of an ancestor link.
    A fix that merely stopped resolving would pass every other case here and
    silently defeat the guard.
    """
    from autoskillit.core import pkg_root

    plugins_dir = home / ".autoskillit" / "marketplace" / "plugins"
    plugins_dir.parent.mkdir(parents=True, exist_ok=True)
    plugins_dir.symlink_to(pkg_root())


#: name -> seeder. Parameterize over the keys; call `seed_legacy_home(name, home)`
#: before patching `Path.home`.
_SEEDERS: dict[str, Callable[[Path], None]] = {
    "clean": _clean,
    "legacy_symlink": _legacy_symlink,
    "legacy_symlink_dangling": _legacy_symlink_dangling,
    "real_dir_stale_version": _real_dir_stale_version,
    "plain_file": _plain_file,
    "symlinked_ancestor": _symlinked_ancestor,
}

#: The set of state names, for parameterization.
LEGACY_HOME_STATES = frozenset(_SEEDERS)

#: States in which the destination genuinely lies inside the source root, so a
#: correct containment guard must still refuse to write there.
CONTAINED_STATES = frozenset({"symlinked_ancestor"})


def seed_legacy_home(state: str, home: Path) -> None:
    """Seed *home* with the named pre-existing install state."""
    _SEEDERS[state](home)


@pytest.fixture(params=sorted(_SEEDERS))
def legacy_home(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch) -> Iterator[str]:
    """Seed tmp_path with a pre-existing install state, then patch Path.home to it.

    Yields the state name so a test can assert state-specific expectations.
    """
    state: str = request.param
    seed_legacy_home(state, tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield state
