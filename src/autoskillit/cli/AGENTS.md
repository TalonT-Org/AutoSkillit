# cli/

IL-3 CLI layer — entry points for all user-facing commands.
Sub-packages: doctor/ (see doctor/AGENTS.md), fleet/ (see fleet/AGENTS.md),
session/ (see session/AGENTS.md), ui/ (see ui/AGENTS.md), update/ (see update/AGENTS.md).

## Architecture Notes

`install()` in `_marketplace.py` is transactional: every check that can decline
the install runs before the first persistent mutation, and every failure
afterwards restores the pre-attempt `marketplace.json`, `installed_plugins.json`,
and retiring queue via `_InstallSnapshot`. Do not add a mutation above the
preflight block, and do not add a failure path that bypasses the rollback —
retiring the live plugin cache before its replacement was secured is what
produced a dangling registry pointer two hours later.

`app.py` is the Click application root; all sub-packages register their subcommand groups
against the root Click group. `_serve_guard.py` was extracted from `app.py` to isolate
the asyncio/signal machinery for testability.
