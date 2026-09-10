# _runtime/

Hook utility modules — shared command classification, hook payload parsing,
hook settings/overlay resolution, policy-event emission, GitHub mutation
analysis, and exploration-request record management.

Moved out of `hooks/` top level so `hooks/` holds only the atomic-registry
fixtures that the `hooks.json` plugin-manifest contract pins at that path:
the 11 hook-event scripts, `_dispatch.py`, `_session_binding.py`,
`_join_ledger.py`, and the package's bare-name bootstrap convention.

## Architecture Notes

**Bare-name importability preservation.** Hook scripts at `hooks/` top level
import these utilities via `from _<name> import …` (bare-name) using a
`hooks/`-on-`sys.path` bootstrap. After this move, each hook script's local
sys.path bootstrap adds `hooks/_runtime/` so bare-name imports resolve there
too — preserving the stdlib-only `hooks/AGENTS.md` contract.

**Inter-peer coupling** (preserved as absolute imports within the move set):

- `_github_mutation_analysis.py:39` → `_command_classification`

**Move-set exclusions** (remain at `hooks/` top level):

- `_dispatch.py` — path-contract-pinned by `hooks/AGENTS.md:13-14` and
  `tests/contracts/test_projection_hook_relocatability.py`.
- `_session_binding.py` and `_join_ledger.py` — dual-import contract per
  their module docstrings.
