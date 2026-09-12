# _installed/

IL-1 install/lease/artifact authority: install-state reconciliation, installed-artifact
verification, projection cache, shared asset store, and publication obligations.

## Architecture Notes

The narrow exception is install-state diagnostics in `_state.py` and `_artifact.py`:
registry entries are read only as evidence that the trusted home/plugin/version-derived
artifact is obligatory. They are never used as path authority, and exact identity is
validated under its stable sidecar lease.

**Changing the shape of an artifact we write under `~/` requires an entry in
`RETIRED_INSTALL_ARTIFACT_SHAPES`** (`core/types/_type_constants_retirements.py`), consumed
at runtime by `reconcile_install_artifacts()` here. `~/.autoskillit/` outlives
years of releases, so a shape change with no registry entry strands every
pre-existing install.

**`_shared_asset_store.py`** hardlinks the verbatim, byte-identical plugin assets
(`assets/`, `hooks/`, `recipes/`, `agents/`) that every projection of the same release
shares, instead of each projection carrying its own copy. Design constraints, all
load-bearing:

- **Store root outside `projections_root`.** `resolve_shared_asset_store_root()` never
  places the store under the projections root `prune_stale_projections`
  (`_projection_cache.py`) enumerates — commit `0949f8a8f` (#4689/#4690) already fixed
  exactly this mistake once (a plugin-generations store misidentified as a stale
  projection).
- **Same device, or no store at all.** The candidate root is resolved from
  `tempfile.gettempdir()` and its `st_dev` must equal `projections_root`'s `st_dev`
  (`os.link()` raises `EXDEV` across filesystems). A mismatch returns `None` — callers
  fall back to `copy2` wholesale for every file, never attempt-and-catch `EXDEV`
  per file.
- **Hardlink with `copy2` fallback, never symlinks.** `link_or_copy_asset()` calls
  `os.link()` and falls back to `shutil.copy2()` on any `OSError` (cross-device, no
  hardlink support, or the store being unavailable). `os.symlink` is never used —
  the shared store must not become a way around the existing symlink prohibition.
- **Bounded populate lease.** `_populate_store_entry()` acquires a timeboxed
  `ArtifactLease` (`_STORE_LEASE_TIMEOUT_SECONDS`) before writing a new store entry,
  defending against the #4511 store-wide capacity exhaustion pattern (an
  un-timeboxed flock on a hot path under concurrent xdist × worktree load). Lease
  contention or failure falls through to `copy2`, never blocks indefinitely.
