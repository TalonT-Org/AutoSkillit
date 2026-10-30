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
| Clean installation | `f84bae324` | Passed | Same explicitly selected workflow run; clean-install execution and aggregate gate both completed successfully |
| Headless sandbox smoke | `f84bae324` | Running | [E2E run 41089434602](https://github.com/TalonT-Org/AutoSkillit/actions/runs/41089434602); execution and exact teardown still to be checked |
| Seeded implementation | `f84bae324` | Queued | [E2E run 41089453034](https://github.com/TalonT-Org/AutoSkillit/actions/runs/41089453034); same candidate, fixed seed, PR/CI evidence and teardown still to be checked |
| Real package upgrade | Old source to candidate | Running independently | Scratch home and uv tool/bin directories; actual resulting source and install invariance still to be checked |
| Configured local gate | `f84bae324` | Incomplete | Lint and Linux/macOS type checks passed; test process timed out after 1,200 seconds around 80%, with failure markers and no final failure summary |

## Reproduction

The existing workflow accepts at most two catalog test names in one dispatch. Run against the intended branch, and confirm each resulting run's full `headSha` before attributing a pass to the candidate:

```bash
gh workflow run e2e.yml --repo TalonT-Org/AutoSkillit --ref develop -f tests=canary,clean-install
gh workflow run e2e.yml --repo TalonT-Org/AutoSkillit --ref develop -f tests=headless-smoke
gh workflow run e2e.yml --repo TalonT-Org/AutoSkillit --ref develop -f tests=implementation
```

Wait for each scenario's actual execution and preserve first-attempt failures. The existing repository-wide E2E queue and session limits remain in effect. The implementation scenario needs the configured canonical sandbox seed issue, OPEN and unclaimed, and a passing sandbox baseline.

Run the repository's local checks with `task test-local-gate`. Use `task install-worktree` for linked-worktree setup; do not run `autoskillit init` inside a linked worktree.

## Evidence required before marking a row passed

- A selected test job actually executed, rather than only the selector and aggregate gate passing.
- Installed source/version and provider/backend identity match the recorded case.
- A recipe produced its required artifacts and a PR with the expected diff; sandbox CI passed for that exact PR head.
- Implementation's PR is OPEN and unmerged when validated. After teardown, owned PRs are CLOSED, owned remote branches are gone, the claim is released, and the seed remains OPEN.
- Failure, cancellation and cleanup results remain recorded even if a subsequent retry succeeds.
- Known clean-install doctor warnings #5242/#5243 are distinguished from unexpected warnings and from actual plugin/MCP failures.

## Additional probes and limits

The current catalog has no seeded remediation scenario; #5234 tracks that gap. A separate feasibility check is investigating the smallest additional sandbox probe without introducing a new framework. A real upgrade rehearsal uses pristine old source commit `5c4924749b3b52fabc487233c3747e511ab35512` (`0.10.1013`) in a scratch installation; installed local hot fixes are outside this experiment.

Current live E2E uses Claude Code through MiniMax. Its success does not establish every native-provider or Codex workflow. Fixed-join Codex skill admission remains a separate adoption requirement under #4711. Release acceptance must be repeated against the final candidate after the separate fixes land.
