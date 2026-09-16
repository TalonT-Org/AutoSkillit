# cli/

IL-3 CLI layer — entry points for all user-facing commands.
Sub-packages: doctor/ (see doctor/AGENTS.md), fleet/, install/ (see
install/AGENTS.md), ops/ (see ops/AGENTS.md), prompts/ (see prompts/AGENTS.md),
session/ (see session/AGENTS.md), ui/ (see ui/AGENTS.md), update/ (see
update/AGENTS.md).

## Architecture Notes

`install()` in `cli/install/_marketplace.py` completes preflight before persistent
mutation, then publishes and reconciles the installed generation under the managed-home
install lock. Failures release the lock without compensating rollback. Keep publication
and artifact verification inside that lock, and keep declining checks in preflight.
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

`app.py` is the cyclopts application root; all sub-packages register their subcommand groups
against the root cyclopts `App` via `app.command(...)`. `_serve_guard.py` was extracted from `app.py` to isolate
the asyncio/signal machinery for testability.
