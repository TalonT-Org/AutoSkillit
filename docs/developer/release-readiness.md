# Develop release readiness experiment — 2026-10-30

This is an experimental evidence branch. Keep its PR in draft and do not merge or enable auto-merge. Product fixes for #4751, #5249, and #4711 are being handled separately. This branch records independent release testing; it does not certify release readiness from a green selection-only gate.

## Candidate and isolation

- Baseline product commit: `f84bae324c3497ec0851d5ac887770d0e895fbad` (`0.10.1306`).
- Test branch: `test/release-readiness-20261030`.
- Playground: [TalonT-Org/autoskillit-e2e-sandbox](https://github.com/TalonT-Org/autoskillit-e2e-sandbox).
- Work takes place in an isolated linked worktree. The primary checkout and active AutoSkillit installation are preserved.
- Workflow dispatches record their actual `headSha`; results from another candidate are listed separately. A documentation commit on this branch does not replace the recorded product baseline.

## Campaign results

| Check | Candidate | Result | Evidence |
|---|---|---|---|
| Canary | `f84bae324` | Passed | [E2E run 41088605318](https://github.com/TalonT-Org/AutoSkillit/actions/runs/41088605318) — actual canary job completed successfully |
| Clean installation | `f84bae324` | Expected warnings accepted | Workflow/gate succeeded, while the downloaded harness result is `passed: false`, `outcome: expected_failure`, with no unexpected failures; allowlisted doctor findings are #5242/#5243 |
| Headless sandbox smoke | `f84bae324` | Passed, cleanup independently verified | [E2E run 41089434602](https://github.com/TalonT-Org/AutoSkillit/actions/runs/41089434602); downloaded harness result has `passed: true`, no failures; scenario ran 16:23:38–16:27:17 UTC |
| Seeded implementation | `f84bae324` | Passed, cleanup independently verified | [E2E run 41089453034](https://github.com/TalonT-Org/AutoSkillit/actions/runs/41089453034); scenario passed 16:27:21–17:07:21 UTC, followed by a passing gate |
| Real package upgrade | `5c4924749` to `f84bae324` | Install passed; CLI exit status failed | Real packages in scratch Docker; candidate version/doctor passed and old worker survived, but update re-exec returned status 20 |
| Configured local gate | Product baseline plus report fix `f12e4872` | Passed | 13,123 passed, 187 skipped, 12 xfailed; conservative filter selected 13,321 and deselected 40,860; lint and both platform type checks passed |
| PR test shards | `f12e4872` | Passed | [Tests run 41094167109](https://github.com/TalonT-Org/AutoSkillit/actions/runs/41094167109); Channel B is unfiltered, other shards retain configured path filtering |
| Unfiltered local test task | Evidence commit `4fb145b2`, product baseline `f84bae324` | Passed | 56,876 passed, 809 skipped, 26 xfailed in 25m 54s; exit 0, with path filtering disabled; smoke/canary markers and feature gates still apply |

## Reproduction

The existing workflow accepts at most two catalog test names in one dispatch. Run against the intended branch, and confirm each resulting run's full `headSha` before attributing a pass to the candidate:

```bash
gh workflow run e2e.yml --repo TalonT-Org/AutoSkillit --ref develop -f tests=canary,clean-install
gh workflow run e2e.yml --repo TalonT-Org/AutoSkillit --ref develop -f tests=headless-smoke
gh workflow run e2e.yml --repo TalonT-Org/AutoSkillit --ref develop -f tests=implementation
```

Wait for each scenario's actual execution and preserve first-attempt failures. The existing repository-wide E2E queue and session limits remain in effect. The implementation scenario needs the configured canonical sandbox seed issue, OPEN and unclaimed, and a passing sandbox baseline.

Run the repository's local checks with `task test-local-gate`. Use `task install-worktree` for linked-worktree setup; do not run `autoskillit init` inside a linked worktree.

The additional path-unfiltered run used the existing task, explicitly selecting the entire test tree and clearing inherited path ignores:

```bash
AUTOSKILLIT_TEST_FILTER=none PYTEST_TEST_PATHS=tests/ PYTEST_IGNORE_PATHS= task test-check
```

It ran on Python 3.11.14. Reported feature scope had Codex backend disabled and exploration, fleet, headless fleet execution, planner, and providers enabled. The active host entrypoint hash and installed source commit matched the pre-test snapshot after this full run.

## Evidence required before marking a row passed

- A selected test job actually executed, rather than only the selector and aggregate gate passing.
- Installed source/version and provider/backend identity match the recorded case.
- A recipe produced its required artifacts and a PR with the expected diff; sandbox CI passed for that exact PR head.
- Implementation's PR is OPEN and unmerged when validated. After teardown, owned PRs are CLOSED, owned remote branches are gone, the claim is released, and the seed remains OPEN.
- Failure, cancellation and cleanup results remain recorded even if a subsequent retry succeeds.
- Known clean-install doctor warnings #5242/#5243 are distinguished from unexpected warnings and from actual plugin/MCP failures.

The smoke artifacts identify [sandbox PR #59](https://github.com/TalonT-Org/autoskillit-e2e-sandbox/pull/59), head `b83fc3ae4ceb05cb5eb8d69e3a1323c3bc1ff497`, and branch `e2e-smoke-071e3714980e4da1b92be7f495614abf`. A separate GitHub query verified the PR is CLOSED and unmerged and the branch is absent.

Implementation produced [sandbox PR #60](https://github.com/TalonT-Org/autoskillit-e2e-sandbox/pull/60), head `1942f885c49652b61e778cf290f53ca217c113a3`, on `e2e-implementation-fixture-normalize-whitespace/1-3`. Downloaded harness results report no failures, complete lifecycle coverage, and peak concurrency of six sessions without violations. A separate GitHub query verified matching-head CI SUCCESS, the PR CLOSED and unmerged, its owned branch absent, and seed issue #1 OPEN with no claim label. Unrelated fixture PR #50 remains OPEN at `a7e6d89767a9659d0d8854306defbe5e675c8b55`; both pre-existing fixture branches remain at `d6c143d2494488deeb6680d7d5cf2e55e3e01732`.

## Reproduced upgrade failure

The real-package upgrade from pristine develop-track `0.10.1013` (`5c4924749b3b52fabc487233c3747e511ab35512`) to `0.10.1306` (`f84bae324c3497ec0851d5ac887770d0e895fbad`) installed and pivoted successfully, then returned `failed-upgrade`, exit **20**. A scratch-only Git mirror pinned the source commits, and the supported uv version ran in a container with only the owned probe directory mounted. No fake package or active host installation was used.

Reproduction sequence:

1. Start with the pristine older develop-track install in an isolated home outside any checkout; start an old-version worker.
2. Run `autoskillit update` with the candidate as the remote develop target. It installs the candidate and prints success.
3. Restart re-executes the original `update` arguments. That second invocation observes previous, observed and target commits all equal and exits 20.
4. Confirm the candidate's `--version` and `doctor` exit 0; a standalone already-current `update` also exits 20. The existing worker remains alive on the old version.

Current code dispatches restart at `src/autoskillit/cli/update/_update.py:63`, re-executes original arguments in `_restart.py:10`, and rejects unchanged advance verdicts in `_transaction.py:304`. Existing updater tests mock restart, leaving this real exec boundary uncovered. The transaction's no-advance guard is intentional; the user-visible problem is a second update replacing the successful transaction's result. No matching ticket was found in the saved issue snapshot; this PR includes evidence, not a product fix.

Host executable path, hash, version, direct-URL provenance and uv tool inventory matched the pre-test snapshot afterward. Complete reproduction commands and logs are retained in the local campaign's `upgrade/` directory.

## Additional probes and limits

The current catalog has no seeded remediation scenario; #5234 tracks that gap. The fixed seed is an implementation fixture, so it was preserved rather than rewritten into a bug fixture. The upgrade rehearsal used pristine old source; installed local hot fixes were outside this experiment.

An earlier main-checkout local gate timed out after 1,200 seconds around 80% completion without a final failure summary. The isolated worktree's configured gate later passed after correcting the experiment report's filename/index mistakes; the separate unfiltered task also passed. The current Tests workflow exposes no manual dispatch or PR-label override for full path-unfiltered CI, so the broader run was performed locally.

Current live E2E uses Claude Code through MiniMax. Its success does not establish every native-provider or Codex workflow. Fixed-join Codex skill admission remains a separate adoption requirement under #4711. Release acceptance must be repeated against the final candidate after the separate fixes land.
