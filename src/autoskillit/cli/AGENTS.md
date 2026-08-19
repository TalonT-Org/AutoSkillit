# cli/

IL-3 CLI layer — entry points for all user-facing commands.
Sub-packages: doctor/ (see doctor/AGENTS.md), fleet/, install/,
session/ (see session/AGENTS.md), prompts/, ui/ (see ui/AGENTS.md),
update/ (see update/AGENTS.md).

## Architecture Notes

`install()` in `cli/install/_marketplace.py` is transactional: every check that can decline
the install runs before the first persistent mutation, and every failure
afterwards restores the pre-attempt `marketplace.json`, `installed_plugins.json`,
and retiring queue via `_InstallSnapshot`. Do not add a mutation above the
preflight block, and do not add a failure path that bypasses the rollback —
retiring the live plugin cache before its replacement was secured is what
produced a dangling registry pointer two hours later.
`cli/install/_install_contract.py` is the dependency leaf that preserves install semantics
across the Python, CLI, and update-child process boundaries. It exports typed
requests/results (`InstallRequest`, `InstallResult`, `InstallMode`, etc.)
**and** a typed argv builder (`MaintenanceInstallArgv.to_argv()`) for the
canonical ``autoskillit install --maintenance-update`` child subprocess.
Every site that spawns that child MUST construct its argv via
`MaintenanceInstallArgv.to_argv()` — hand-built argv literals bypass the
type contract and were the root cause of issue #4485. An AST-based
architectural guard at `tests/arch/test_maintenance_install_argv_contract.py`
makes this structural invariant permanent.

`app.py` is the Click application root; all sub-packages register their subcommand groups
against the root Click group. `_serve_guard.py` was extracted from `app.py` to isolate
the asyncio/signal machinery for testability.
