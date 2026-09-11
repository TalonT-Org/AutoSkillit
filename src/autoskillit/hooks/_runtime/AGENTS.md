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

**GitHub-mutation analysis.** `_github_mutation_analysis.py` (558 lines, under
the 750-line REQ-CNST-010 default with no exemption needed) owns the segment
walk and `analyze_github_mutations`, the repeatable-shell/process-substitution
exec-token detection, and a set of `_command_classification` call-through
wrappers. The `gh`/`curl`-specific mutation grammar and request-body vocabulary
it depends on (`GitHubMutationKind`, `GitHubMutationRecord`, `_analyze_gh_segment`,
`_analyze_curl_segment`, `_GH_API_FLAG_SPEC`, `_CURL_FLAG_SPEC`, etc.) live in the
sibling `hooks/_classification/` subpackage (`_github_mutation_cli_analysis.py`
as `_cli`, `_github_mutation_request_analysis.py` as `_request`) — imported via
the same dual dotted/bare-name branch used for `_command_classification`'s own
delegation to `_classification/_flags.py`/`_interpreters.py`/`_tokenizer.py`. An
earlier further decomposition of this file into `_github_mutation_types.py` /
`_github_input.py` / `_gh_analysis.py` / `_curl_analysis.py` was retired: it
duplicated (and had fallen behind) the `_classification` package's own analysis
logic, with zero consumers outside this one file.

**Inter-peer coupling** (bare-name imports within the move set):

- `_github_mutation_analysis.py`, `_command_classification.py` → `hooks/_classification/`
  (a sibling subpackage outside the move set, not itself moved)

**Move-set exclusions** (remain at `hooks/` top level):

- `_dispatch.py` — path-contract-pinned by `hooks/AGENTS.md:13-14` and
  `tests/contracts/test_projection_hook_relocatability.py`.
- `_session_binding.py` and `_join_ledger.py` — dual-import contract per
  their module docstrings.
