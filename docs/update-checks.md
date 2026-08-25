# Updating AutoSkillit

## How update checks work

On every interactive CLI invocation (excluding headless/MCP sessions and CI),
AutoSkillit checks for available updates and shows a single `[Y/n]` prompt if
any of the following conditions fire:

- **binary** — a newer released package version is available
- **hooks** — new or changed hook entries have been added since last install
- **branch drift** — the installed commit SHA differs from the HEAD of your tracked branch

All three conditions are consolidated into a single prompt listing each reason.
Answering `Y` runs the appropriate upgrade command followed by `autoskillit install`.

## Release channels and their criteria

An install has one release channel and therefore one answer to "is an update
available?" The binary and branch-drift lines are presentations of that shared
answer; they are not independent freshness checks.

| Channel | Installs | Update available | Successful advance |
|---------|----------|------------------|--------------------|
| released | stable, main, and release tags | target PEP 440 version is greater | observed version is greater |
| branch | develop and other non-stable VCS refs | resolved target commit differs | observed commit equals the resolved target commit |
| working tree | local editable/path installs | never automatic | monotonic advance is not applicable |

For a branch channel, the tracked ref is the authority. The update pins the SHA
resolved during the check, and success means convergence to that exact commit even
when the package version is unchanged. A ref rewind is therefore not treated as a
version downgrade: if the branch now points at an older commit, converging to that
commit is the requested branch-tracking behavior. Working-tree installs have no
meaningful monotonic identity; their update still has to complete its subprocess,
install, and artifact-verification checks, but no invented ordering is imposed.

For the `develop`-tracking (dev) branch, the upgrade command no longer force-replaces
the shared `uv`-managed tool root in place. It installs into a fresh, version-addressed
destination via `UV_TOOL_DIR` (`uv tool install` in `uv` 0.9.21 has no `--target` flag —
`UV_TOOL_DIR` is the sole supported redirection) and publishes it as a new generation
under `~/.autoskillit/plugin-generations/autoskillit-install/`. Any AutoSkillit process
already running keeps executing out of the generation it resolved at startup — see
[Runtime Health](version-pipeline.md#runtime-health) for how the exec-time entrypoint
shim makes that possible. The `stable` and `local-editable` tracks are unaffected: they
still run `uv tool upgrade` / `uv pip install -e` against the existing install in place.

## Branch-aware dismissal windows

Dismissal windows vary by install type to balance convenience and safety:

| Install | Window |
|---------|--------|
| stable / main / release-tag | 7 days |
| develop / local-editable | 12 hours |

The window is determined at check time from the current `direct_url.json` —
not from what was stored when you dismissed.

**Recommendation: pin to a release tag and upgrade deliberately.** The
`develop` track's HEAD advances 3.7–5.1 times per day, so a `develop`-tracking
install finds a different target commit on essentially every invocation — every one of
those is a prompt (or an auto-accepted upgrade) you didn't ask for. Phase 3
(see [Runtime Health](version-pipeline.md#runtime-health)) means an accepted
upgrade can no longer destroy in-flight work on either track, but it does not
reduce how often `develop` finds something to prompt about. Tracking a
release tag or `stable` gets you the 7-day dismissal window above instead of
`develop`'s 12 hours, and upgrading on your own schedule rather than
`develop`'s. This is an operational choice, not a code change — nothing here
requires it.

Dismissal expires on two axes:

1. **Time** — the window elapses.
2. **Release-identity delta** — the running released version or tracked-branch
   commit differs from the identity recorded at dismissal time.

## The `autoskillit update` command

To upgrade immediately without waiting for a prompt:

    autoskillit update

This runs the install-type-aware upgrade command, then `autoskillit install`,
then verifies that the release identity advanced under the channel contract above.
On success it clears any active
dismissal state so the next check starts fresh.

For unknown install types (e.g. installed from PyPI without a VCS reference),
`autoskillit update` exits with code 2 and prints a reinstallation hint.

## Escape hatches

Set any of these env vars to silence all update checks for a single invocation:

    AUTOSKILLIT_SKIP_UPDATE_CHECK=1 autoskillit <command>
    AUTOSKILLIT_SKIP_STALE_CHECK=1 autoskillit <command>

These are automatically injected by the update logic itself so that subprocesses
launched during an update do not re-enter the check.

## Install detection

Update checks read `direct_url.json` from the installed package metadata
(populated by `uv` or `pip` at install time).  The `~/.autoskillit/dev` marker
file is no longer consulted — install classification is derived entirely from
`direct_url.json`.

`detect_install()` reads this metadata via
`importlib.metadata.Distribution.from_name("autoskillit")`, which resolves
against the *currently running* interpreter's own site-packages — wherever
that happens to live. For a dev-track install this is inside whichever
version-addressed generation directory
(`~/.autoskillit/plugin-generations/autoskillit-install/{version}/{incarnation_id}/`)
the running process resolved into at exec time. `uv tool install` still writes
its own `dist-info/direct_url.json` into that generation's venv with
`vcs_info.commit_id` and `vcs_info.requested_revision` populated exactly as
before, so `InstallType.GIT_VCS` classification is unaffected by which
generation is currently selected — confirmed by spike against a real
git-sourced install.

Use `autoskillit doctor` to inspect the current classification:

    install_classification: install_type=git-vcs, requested_revision=stable, commit_id=abc12345
