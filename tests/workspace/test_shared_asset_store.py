"""C1-C5: the machine-scoped content-addressed hardlink asset store.

Exercises the mechanism directly (resolve_shared_asset_store_root, link_or_copy_asset,
_copy_non_skill_plugin_assets) rather than the full ProjectedPluginArtifactAuthority
end-to-end path -- ~188 existing test functions across 13 files already exercise that real
authority; these tests focus on proving the sharing mechanism itself is correct, safe, and
falls back cleanly, which is the new surface this stage adds.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from autoskillit.core import SkillContractError
from autoskillit.workspace._projected_artifact.materialization import (
    _copy_non_skill_plugin_assets,
)
from autoskillit.workspace._shared_asset_store import (
    link_or_copy_asset,
    resolve_shared_asset_store_root,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


def _make_source_plugin(root: Path, *, payload: bytes = b"x" * 4096) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text("{}")
    assets = root / "assets"
    assets.mkdir()
    (assets / "shared.bin").write_bytes(payload)
    return root


def test_identical_assets_share_one_inode_across_projections(tmp_path: Path) -> None:
    """C1: materialize two projections in two isolated homes on the same filesystem;
    the shared asset's st_ino matches and st_nlink >= 2."""
    source = _make_source_plugin(tmp_path / "source")

    home_a = tmp_path / "home-a" / ".autoskillit" / "plugin-projections"
    home_b = tmp_path / "home-b" / ".autoskillit" / "plugin-projections"
    dest_a = home_a / "proj-a"
    dest_b = home_b / "proj-b"
    dest_a.mkdir(parents=True)
    dest_b.mkdir(parents=True)

    _copy_non_skill_plugin_assets(source, dest_a)
    _copy_non_skill_plugin_assets(source, dest_b)

    asset_a = dest_a / "assets" / "shared.bin"
    asset_b = dest_b / "assets" / "shared.bin"
    assert asset_a.read_bytes() == asset_b.read_bytes()
    stat_a = asset_a.stat()
    stat_b = asset_b.stat()
    assert stat_a.st_ino == stat_b.st_ino, "identical assets did not share one inode"
    assert stat_a.st_nlink >= 2


def test_a_projections_incremental_cost_is_bounded(tmp_path: Path) -> None:
    """C2: the incremental bytes of a second projection (beyond the shared store's own
    copy) stay far below the source payload size -- the cost-shaped assertion this store
    exists to make true."""
    payload = b"y" * (200 * 1024)  # 200 KiB, well above filesystem block-size noise
    source = _make_source_plugin(tmp_path / "source", payload=payload)

    dest_a = tmp_path / "home-a" / ".autoskillit" / "plugin-projections" / "proj-a"
    dest_b = tmp_path / "home-b" / ".autoskillit" / "plugin-projections" / "proj-b"
    dest_a.mkdir(parents=True)
    dest_b.mkdir(parents=True)

    _copy_non_skill_plugin_assets(source, dest_a)
    before = shutil.disk_usage(tmp_path).used
    _copy_non_skill_plugin_assets(source, dest_b)
    after = shutil.disk_usage(tmp_path).used

    incremental = after - before
    assert incremental < len(payload) / 4, (
        f"second projection cost {incremental} bytes incrementally; expected it to share "
        f"the {len(payload)}-byte asset via hardlink, not duplicate it"
    )


def test_shared_store_never_introduces_a_symlink(tmp_path: Path) -> None:
    """C4a: a plugin source containing a symlinked asset is still rejected -- the shared
    store must never become a way around the existing symlink prohibition."""
    source = _make_source_plugin(tmp_path / "source")
    (source / "assets" / "linked.bin").symlink_to(source / "assets" / "shared.bin")

    dest = tmp_path / "home" / ".autoskillit" / "plugin-projections" / "proj"
    dest.mkdir(parents=True)

    with pytest.raises(SkillContractError, match="symlink"):
        _copy_non_skill_plugin_assets(source, dest)


def test_shared_store_uses_hardlinks_never_symlinks(tmp_path: Path) -> None:
    """C4b: the populated store entry and the published copy are both regular files
    (hardlinks), never symlinks -- os.link, never os.symlink."""
    source = _make_source_plugin(tmp_path / "source")
    dest = tmp_path / "home" / ".autoskillit" / "plugin-projections" / "proj"
    dest.mkdir(parents=True)

    _copy_non_skill_plugin_assets(source, dest)

    published = dest / "assets" / "shared.bin"
    assert not published.is_symlink()
    store_root = resolve_shared_asset_store_root(dest.parent)
    assert store_root is not None
    for entry in store_root.iterdir():
        assert not entry.is_symlink()


def test_falls_back_to_copy_across_filesystems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C5a: when os.link raises (simulating a cross-device store or a filesystem without
    hardlink support), link_or_copy_asset degrades to copy2, not a crash."""
    source = tmp_path / "source.bin"
    source.write_bytes(b"content")
    store_root = tmp_path / "store"
    store_root.mkdir()
    dest = tmp_path / "dest.bin"

    def _raise_exdev(*args: object, **kwargs: object) -> None:
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", _raise_exdev)

    link_or_copy_asset(source, dest, store_root=store_root)

    assert dest.read_bytes() == b"content"


def test_concurrent_workers_share_one_store_entry(tmp_path: Path) -> None:
    """C5b: spawn concurrent materializations under the lease; assert one store entry,
    no corruption, no FileExistsError. Directly addresses #3449's xdist
    pool-fragmentation finding."""
    import threading

    payload = b"z" * (64 * 1024)
    source = _make_source_plugin(tmp_path / "source", payload=payload)
    projections_root = tmp_path / "home" / ".autoskillit" / "plugin-projections"
    projections_root.mkdir(parents=True)

    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def _materialize(index: int) -> None:
        try:
            barrier.wait(timeout=5)
            dest = projections_root / f"proj-{index}"
            dest.mkdir(parents=True)
            _copy_non_skill_plugin_assets(source, dest)
        except BaseException as exc:  # noqa: BLE001 - collected and re-raised on the main thread
            errors.append(exc)

    threads = [threading.Thread(target=_materialize, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"concurrent materialization raised: {errors}"
    digests = {
        (projections_root / f"proj-{i}" / "assets" / "shared.bin").read_bytes() for i in range(8)
    }
    assert digests == {payload}
    inodes = {
        (projections_root / f"proj-{i}" / "assets" / "shared.bin").stat().st_ino for i in range(8)
    }
    assert len(inodes) == 1, f"expected one shared inode, got {len(inodes)}: {inodes}"


def test_resolve_shared_asset_store_root_is_disjoint_from_projections_root(
    tmp_path: Path,
) -> None:
    """The store must never live inside projections_root -- prune_stale_projections
    enumerates that root directly and would retire the store as a stale projection."""
    projections_root = tmp_path / "home" / ".autoskillit" / "plugin-projections"
    projections_root.mkdir(parents=True)

    store_root = resolve_shared_asset_store_root(projections_root)

    assert store_root is not None
    assert not store_root.is_relative_to(projections_root)


def test_resolve_shared_asset_store_root_returns_none_on_device_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A device mismatch must return None (skip linking wholesale), never attempt-and-
    catch EXDEV once per file."""
    import autoskillit.workspace._shared_asset_store as store_module

    real_stat = os.stat
    calls = {"n": 0}

    def _fake_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        result = real_stat(path, *args, **kwargs)  # type: ignore[arg-type]
        calls["n"] += 1
        if calls["n"] == 2:
            # Second stat() call is the candidate store's device -- force a mismatch.
            return os.stat_result(
                (result.st_mode, result.st_ino, result.st_dev + 1, *tuple(result)[3:])
            )
        return result

    monkeypatch.setattr(store_module.os, "stat", _fake_stat)
    projections_root = tmp_path / "home" / ".autoskillit" / "plugin-projections"
    projections_root.mkdir(parents=True)

    assert resolve_shared_asset_store_root(projections_root) is None
