# Updating AutoSkillit

## How update checks work

On every interactive CLI invocation (excluding headless/MCP sessions and CI),
AutoSkillit checks for available updates and shows a single `[Y/n]` prompt if
any of the following conditions fire:

- **binary** — a newer release is available on your install's branch
- **hooks** — new or changed hook entries have been added since last install
- **branch drift** — the installed commit SHA lags the HEAD of your tracked branch

All three conditions are consolidated into a single prompt listing each reason.
Answering `Y` runs the appropriate upgrade command followed by `autoskillit install`.

## Branch-aware dismissal windows

Dismissal windows vary by install type to balance convenience and safety:

| Install | Window |
|---------|--------|
| stable / main / release-tag | 7 days |
| develop / local-editable | 12 hours |

The window is determined at check time from the current `direct_url.json` —
not from what was stored when you dismissed.

Dismissal expires on two axes:

1. **Time** — the window elapses.
2. **Version delta** — the running version advances past the dismissed version.

## The `autoskillit update` command

To upgrade immediately without waiting for a prompt:

    autoskillit update

This runs the install-type-aware upgrade command, then `autoskillit install`,
then verifies that the version advanced.  On success it clears any active
dismissal state so the next check starts fresh.

For unknown install types (e.g. installed from PyPI without a VCS reference),
`autoskillit update` exits with code 2 and prints a reinstallation hint.

## Escape hatches

Set any of these env vars to silence all update checks for a single invocation:

    AUTOSKILLIT_SKIP_UPDATE_CHECK=1 autoskillit <command>
    AUTOSKILLIT_SKIP_STALE_CHECK=1 autoskillit <command>
    AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK=1 autoskillit <command>

Both are automatically injected by the update logic itself so that subprocesses
launched during an update do not re-enter the check.

## Install detection

Update checks read `direct_url.json` from the installed package metadata
(populated by `uv` or `pip` at install time).  The `~/.autoskillit/dev` marker
file is no longer consulted — install classification is derived entirely from
`direct_url.json`.

Use `autoskillit doctor` to inspect the current classification:

    install_classification: install_type=git-vcs, requested_revision=stable, commit_id=abc12345
