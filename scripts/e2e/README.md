# E2E tests in the user image

`.github/workflows/e2e.yml` builds the `user` Docker image and runs catalog tests inside it.
`clean-install` checks installation and doctor without credentials or model calls.
Canary and recipe tests use MiniMax through its Anthropic-compatible endpoint.
This directory holds everything the workflow runs:

| File | Role |
|------|------|
| `catalog.json` | Registered E2E tests (the only source of test names, budgets and trigger paths) |
| `e2e_catalog.py` | Stdlib loader and validator for the catalog |
| `e2e_select.py` | Picks the tests an event runs (`select`) and judges the required check (`gate`) |
| `e2e_harness.py` | Runs one test (`run`), performs exact sandbox teardown (`cleanup`) and redacts artifacts (`redact`) |
| `autoskillit-config.yaml` | AutoSkillit user-layer config the harness installs |
| `post-e2e-failure.sh` | Appends a failure to the test's tracking issue |

`e2e_select.py` runs on the runner's system `python3`; `e2e_harness.py` runs inside the
image as `/usr/bin/python3`. Both are stdlib-only, which is why the catalog is JSON.

## Maintainer setup

`clean-install` needs no provider or sandbox secrets. The following secrets support live
model tests:

1. **`MINIMAX_API_KEY`** — an organization or repository secret holding a MiniMax API key.
2. **`E2E_SANDBOX_TOKEN`** — a repository secret holding a fine-grained personal access
   token scoped to the sandbox repository (`sandbox_repository` in `catalog.json`) only.
   Start with these permissions and grow them only when a recipe needs more:

   | Permission | Needed by |
   |------------|-----------|
   | Contents: read and write | `gh repo clone`, and recipes pushing branches |
   | Pull requests: read and write | recipes opening PRs; the harness listing and closing them |
   | Issues: read and write | recipes editing issue bodies |
   | Actions: read | `wait_for_ci` |
   | Commit statuses: read | `wait_for_ci` |

   The canary needs only `MINIMAX_API_KEY`; no Anthropic API key or Claude OAuth credential
   is supplied. The harness writes MiniMax's key into the Claude-compatible settings file
   because MiniMax serves the Anthropic Messages API.
3. Create the `e2e` label in this repository.
4. In branch protection, mark only `e2e-gate` as required — never `select` or `run (…)`.

## Sandbox repository requirements

Recipe tests clone the sandbox and let a recipe open a real pull request there. The sandbox
must have:

- a pull-request CI workflow, so `wait_for_ci` has something to wait for;
- a `Taskfile.yml` with `install-worktree` and `test-check` tasks;
- a `.autoskillit/config.yaml` that sets none of `features`, `quota_guard` or
  `agent_backend` — as the project layer it outranks the harness's user-layer config;
- no committed `.claude/settings.json` or `.claude/settings.local.json` with `env` provider
  or model keys — project and local settings outrank user settings.

## How a run reaches MiniMax

AutoSkillit strips `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_BASE_URL` from
every session it launches and never passes `--settings`, `--setting-sources` or
`CLAUDE_CONFIG_DIR`. The only route left is the `env` block of `~/.claude/settings.json`,
which Claude Code applies over inherited variables and passes to every subprocess it starts.
The harness writes that block (mode `0600`):

| Variable | Value |
|----------|-------|
| `ANTHROPIC_BASE_URL` | `https://api.minimax.io/anthropic` |
| `ANTHROPIC_AUTH_TOKEN` | `MINIMAX_API_KEY` |
| `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_{SONNET,OPUS,HAIKU}_MODEL` | `MiniMax-M3[1m]` |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | `1000000` |
| `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` | `6` |

The values follow MiniMax's Claude Code guide
(<https://platform.minimax.io/docs/token-plan/claude-code>) and the repository's own
`providers.profiles.minimax`. The `[1m]` suffix makes Claude Code assume a 1M-token window
for the unrecognized model id; the compact window is capped at it. Six concurrent subagents
covers `implement` (four children) and `resolve-review` (three to six); a spawn past the
limit fails without retry.

The harness relies on this provider contract: the Anthropic Messages API at
`ANTHROPIC_BASE_URL`, Bearer authentication from `ANTHROPIC_AUTH_TOKEN`, tool use, and
acceptance of the explicit `claude-*` model ids AutoSkillit passes with `--model`. The
canary passes no `--model`, so `ANTHROPIC_MODEL` covers it; `ANTHROPIC_DEFAULT_HAIKU_MODEL`
covers the bare `haiku` alias.

Every child process the harness starts gets an environment with `MINIMAX_API_KEY`,
`E2E_SANDBOX_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN` and every `ANTHROPIC_*` variable removed, so
the settings file is the single source of provider configuration. The sandbox token reaches
`gh auth login` on stdin only. Because settings `env` reaches every agent subprocess, the key
can still surface in session output, so redaction is mandatory (see below).

## Clean installation and doctor

`clean-install` starts a fresh container with only script and artifact mounts. Its actual
HOME, PATH, XDG values and uv tool layout are preserved, and provider credentials are
removed from every child environment. The harness runs:

1. `autoskillit install`.
2. `git init` in a fresh artifact-local `.autoskillit/temp/clean-install/` directory.
3. Write a `.pre-commit-config.yaml` using the gitleaks hook pinned to `v8.30.0`.
4. `autoskillit init --test-command "git diff --check"`.
5. `autoskillit doctor --output-json` in that repository.

One monotonic deadline bounds the command sequence. The scratch repository is removed in
`finally`; command stdout, stderr and status records remain in the artifact directory,
including partial timeout output. `doctor.json` retains the raw doctor stdout.

A clean pass requires successful commands and a complete nonempty JSON `results` list
with valid `ok`, `info`, `warning` or `error` rows. Warnings require an exact check/message
entry with a documented reason in `CLEAN_INSTALL_ALLOWED_WARNINGS`. Only observed
environmental or advisory warnings qualify; installation, plugin and hook defects need
bug tracking.

Catalog `expected_failures` match exact severity/check/message diagnostics linked to
open AutoSkillit bug issues. Reproduce and verify the bug before adding its expectation.
Every expected diagnostic must appear and every other problem fails the test. Remove
an expectation when its diagnostic disappears; stale expectations fail. Command failures
and malformed JSON always fail.

`result.json` records `outcome` and `expected_findings`: a clean `passed` outcome has
`passed: true`; an accepted `expected_failure` has `passed: false` and exits zero for the
CI gate; an unexpected `failed` outcome exits nonzero. The console uses the same outcome.
Select this test directly with workflow dispatch's `tests=clean-install` input.

## Installation in model tests

`fleet run` builds its context with `make_context(cfg, project_dir=Path.cwd(), ...)`
(`src/autoskillit/cli/fleet/_fleet_run.py`). Without an installed plugin it falls back to
`project_default_plugin_authority()` → `DirectInstall(plugin_dir=pkg_root())`
(`src/autoskillit/workspace/_projected_artifact/authority.py`), and `acquire_launch_binding`
publishes the projection on demand. The food-truck errors for a missing installation fire
only for backends that inject skills without plugin installs (Codex); the `claude-code`
backend receives the projection as `--plugin-dir`, and the harness routes every step to
`claude-code`. These model tests skip `autoskillit install`; the clean-install harness
executes it explicitly.

## AutoSkillit user config

For model tests, `autoskillit-config.yaml` becomes `~/.autoskillit/config.yaml` in the container:

- `quota_guard.enabled: false` — the guard measures Anthropic account quota through Claude
  credentials (`~/.claude/.credentials.json`) the container does not have; `fleet run` also
  gets `--disable-quota-guard`;
- `features.fleet` and `features.fleet_headless_run` — `fleet run` requires both;
- `agent_backend.recipe_overrides` — every (recipe, step) the packaged defaults route to
  `codex` is set to `claude-code`. Config layers deep-merge, so each pair is listed
  explicitly; `tests/infra/test_e2e_harness.py` derives the set from
  `src/autoskillit/config/defaults.yaml`, so a new Codex default fails CI until it is added.

## Selection policy

`e2e_select.py select` emits `selected`, `matrix` and `reason` as GitHub Actions outputs. The
matrix is always parseable (`{"include":[]}` when nothing is selected).

| Event | Decision |
|-------|----------|
| `pull_request` from a fork, or with a deleted head repository | nothing |
| `pull_request` below both thresholds without the `e2e` label | nothing |
| `pull_request` with the `e2e` label | selected, bypassing sampling |
| `pull_request` at or above a threshold | selected when sampled |
| `workflow_dispatch` | the named tests (one or two, all in the catalog) |
| `merge_group` | nothing — the pull request already decided |

- **Thresholds:** at least `MIN_CHANGED_FILES` (10) changed files or `MIN_CHANGED_LINES`
  (300) added plus deleted lines.
- **Sampling:** probability `SAMPLE_PROBABILITY` (0.5), seeded by `"{pr number}:{head sha}"`
  — the first eight bytes of its SHA-256 over 2⁶⁴ must fall below the probability. The label
  bypasses sampling because a labelled pull request whose sample fell out would otherwise
  never run for that commit.
- **Picking:** tests whose `trigger_paths` match a changed path, up to `MAX_TESTS` (2); when
  none match, exactly one test. Ties are ordered by the SHA-256 hex digest of
  `"{seed}:{name}"`, so the decision is a pure function of the payload and the changed paths.
  Patterns use `fnmatch.fnmatchcase`, where `*` also matches `/` (`scripts/e2e/*` matches
  every file below `scripts/e2e/`).
- **Relabelling:** any `labeled` event recomputes the same decision, because a new run's
  `e2e-gate` supersedes the previous one for the commit; skipping it would let an unrelated
  label turn a failed gate green.

`e2e_select.py gate` passes only when `select` succeeded and either a selected `run`
succeeded or nothing was selected and `run` was skipped. Every other combination fails.

## Concurrency

The `run` job uses one repository-wide concurrency group (`group: e2e`, `queue: max`,
`cancel-in-progress: false`) and `max-parallel: 1`, so every selected test of every run
waits its turn and never cancels another. `select` and `e2e-gate` are not queued. A
catalog entry's `peak_sessions` is capped at `MAX_PEAK_SESSIONS` (8): one step's fan-out of
up to six subagents plus the orchestrator and step session. With one test running at a
time, CI stays at or below eight of the roughly twelve concurrent MiniMax sessions.
`clean-install` requires exactly zero `peak_sessions`.

Every harness subprocess has a timeout that fires before the job's: `fleet run` gets the
test's `timeout_sec` plus `HARNESS_GRACE_SEC`, and the job gets that budget in minutes plus
`JOB_OVERHEAD_MINUTES` for building the image, redaction and upload. So `result.json`, PR
cleanup and redaction never depend on GitHub cancelling the job.

## Artifacts and redaction

For model tests, the container's AutoSkillit data directory (`~/.local/share/autoskillit`) is bind-mounted
from the runner, so session logs survive a killed container. Those files belong to uid 1000
and are often `0600`, so a second container run copies `out/` and `data/logs/` into
`upload/` world-readable with both secrets replaced by `[REDACTED]`, skips symlinks, and
fails if any secret survives. Only `upload/` is uploaded, and only when redaction
succeeded.

`clean-install` mounts no home directories and retains evidence under `out/`. Its redactor
receives the same required `--secret-env` names with variables unset and skips the absent
home-log tree. Redaction and upload still run after test failures.

## Failure issues

When the test or redaction step fails, `post-e2e-failure.sh` runs inside the built image
with only the workflow token (`issues: write`) and appends a section — stage, pull request
or `workflow_dispatch`, commit and run URL — to the open issue titled
`[E2E] <test> failure`, opening it when none exists. An append that would cross GitHub's
65,536-character body limit closes the full issue and opens a successor that links it.
There is no flake guard: Actions caches are ref-scoped, so no streak survives across pull
requests. The test container never sees the workflow token.

## Recipe test assertions

Regular recipe tests pass when `fleet run` exits 0, its envelope — the last non-empty stdout
line, since earlier lines can be plain-text notices — reports `"success": true`, and exactly
one sandbox pull request numbered above the pre-run baseline exists in the catalog's
`expected_pull_request_state` with a non-empty diff. `gh` reports states in upper case; the
harness lower-cases them. Its existing cleanup closes newly opened PRs with their branches.

`headless-smoke` exercises one orchestrator and one recipe worker (`peak_sessions: 2`). The
harness stages `sandbox-smoke.yaml` from the read-only `/opt/e2e` mount into the disposable
sandbox clone's `.autoskillit/recipes/` directory before invoking fleet by recipe name. This
lets the clone's project name resolve the staged recipe; the host scripts remain read-only.
Fleet resolves `base_branch` from the sandbox project's existing
`branching.default_base_branch` configuration, matching the clone's base branch.
The worker changes and commits only `sandbox/text.py` and `tests/test_smoke_canary.py`.
The orchestrator runs the sandbox test gate, pushes the assigned per-run branch, opens a PR
and closes it. Before cleanup, the harness verifies the successful
envelope, that exactly one PR for the assigned branch is closed, the complete PR diff contains
only those prescribed paths, and the PR head, remote ref and fetched commit agree. The source
and test must contain the requested sentinel and an executable assertion. It saves the PR,
commit, diff and branch evidence for redaction and upload.

The harness writes a lifecycle descriptor before fleet starts, so cleanup does not depend on
the model process or a fleet result. The workflow wraps this test's model container in a
per-run Docker CID file. Its exit trap captures the model result, removes the recorded
container by its exact ID, verifies that ID is absent, then launches a fresh user-image
container for `e2e_harness.py cleanup`. That command uses only `E2E_SANDBOX_TOKEN`, closes any
remaining PR for the assigned branch, deletes that exact remote branch and verifies no open
PR remains and the ref is absent. Cleanup runs after model success, failure or timeout, and its result is part of the step
status. The normal artifact redaction step then runs even when the test step failed. Recipes
without this fixture keep their existing in-container cleanup path.

Recipe `expected_failures` rows identify an individual, verified product defect by exact
`severity: error`, `check` equal to the failed envelope's `error`, `message` equal to its
`user_visible_message`, and an open bug issue URL. Only that exact failed envelope can be
accepted; process timeouts, malformed results, other failures and cleanup failures remain
fatal. An accepted defect is provisional until mandatory branch/PR cleanup succeeds. A clean
run that observes a configured row makes it stale and fails with instructions to remove the
row and close the bug. The smoke catalog starts without expected defects.

## Catalog schema

```json
{
  "sandbox_repository": "OWNER/REPO",
  "tests": [
    {
      "name": "recipe-example",
      "kind": "recipe",
      "peak_sessions": 1,
      "timeout_sec": 300,
      "trigger_paths": ["scripts/e2e/*"],
      "recipe": "implementation",
      "ingredients": {"task": "..."},
      "expected_pull_request_state": "open"
    },
    {
      "name": "clean-install",
      "kind": "clean-install",
      "peak_sessions": 0,
      "timeout_sec": 300,
      "trigger_paths": ["src/autoskillit/cli/*", "scripts/e2e/*"]
    }
  ]
}
```

`recipe`, `ingredients` and `expected_pull_request_state` are required for `recipe` tests
and forbidden for `canary` and `clean-install` tests. `expected_failures` is optional for
`clean-install` and recipe tests. Clean-install rows use the exact doctor `severity`, `check`
and `message`; recipe rows require `severity: error`, `check` and `message` matching the failed
envelope. Every row includes an open bug issue URL
(`https://github.com/TalonT-Org/AutoSkillit/issues/<number>`). Duplicate diagnostic identities
and unknown keys are rejected. `peak_sessions` must be
1–8 for model tests and exactly zero for clean-install. `timeout_sec` must keep the
derived job timeout within 360 minutes.

### Adding a test

1. Add the entry to `catalog.json` with a realistic `peak_sessions` and `timeout_sec`.
2. Point `trigger_paths` at the code the test exercises.
3. Check the sandbox repository supports the recipe (CI, Taskfile tasks, token permissions).
4. Run it once with `workflow_dispatch` (available once `e2e.yml` is on `main`) or locally.

## Local reproduction

```bash
git archive HEAD | docker build --file scripts/docker/Dockerfile --target user \
  --tag autoskillit-e2e:local -
mkdir -p .autoskillit/temp/e2e/{out,data}
chmod 0777 .autoskillit/temp/e2e/{out,data}
MINIMAX_API_KEY=... docker run --rm \
  --env MINIMAX_API_KEY --env E2E_SANDBOX_TOKEN \
  --volume "$PWD/scripts/e2e:/opt/e2e:ro" \
  --volume "$PWD/.autoskillit/temp/e2e/out:/artifacts" \
  --volume "$PWD/.autoskillit/temp/e2e/data:/home/autoskillit/.local/share/autoskillit" \
  autoskillit-e2e:local \
  python3 /opt/e2e/e2e_harness.py run --test canary --out /artifacts
```

For clean-install, use a fresh artifact directory and omit credentials and home mounts:

```bash
mkdir -p .autoskillit/temp/e2e/clean-install-out
chmod 0777 .autoskillit/temp/e2e/clean-install-out
docker run --rm \
  --volume "$PWD/scripts/e2e:/opt/e2e:ro" \
  --volume "$PWD/.autoskillit/temp/e2e/clean-install-out:/artifacts" \
  autoskillit-e2e:local \
  python3 /opt/e2e/e2e_harness.py run --test clean-install --out /artifacts
```

### Sandbox smoke with teardown

Set `MINIMAX_API_KEY` and `E2E_SANDBOX_TOKEN` in the local environment first. Build the same
user image and run this wrapper from the repository root. It creates the `out`, `data` and
`upload` directories used by CI. The model and cleanup containers see E2E scripts read-only;
the cleanup container receives the sandbox token without the MiniMax key. The EXIT trap
keeps the model's status, removes and verifies the exact container, runs remote cleanup, then
runs redaction even if an earlier stage failed. It retains the CID file if container absence
cannot be verified.

```bash
git archive HEAD | docker build --file scripts/docker/Dockerfile --target user \
  --tag autoskillit-e2e:local -

set -euo pipefail
IMAGE=autoskillit-e2e:local
TEST=headless-smoke
E2E_DIR="$PWD/.autoskillit/temp/e2e"
mkdir -p "$E2E_DIR"/{out,data,upload}
chmod 0777 "$E2E_DIR"/{out,data,upload}
RUN_ID="$(date +%s)-$$"
CID_FILE="$E2E_DIR/out/${TEST}-${RUN_ID}.cid"
[[ ! -e "$CID_FILE" ]]

source "$PWD/scripts/e2e/smoke-cleanup.sh"
cleanup_smoke() {
  model_status=$?
  set +e
  redact_status=0
  lifecycle_file="$E2E_DIR/out/${TEST}-lifecycle.txt"
  cleanup_smoke_resources "$CID_FILE" "$PWD/scripts/e2e" "$E2E_DIR/out" "$IMAGE" "$TEST"

  docker run --rm --env MINIMAX_API_KEY --env E2E_SANDBOX_TOKEN \
    --volume "$PWD/scripts/e2e:/opt/e2e:ro" \
    --volume "$E2E_DIR:/e2e" \
    "$IMAGE" python3 /opt/e2e/e2e_harness.py redact --dest /e2e/upload \
      --secret-env MINIMAX_API_KEY --secret-env E2E_SANDBOX_TOKEN \
      /e2e/out /e2e/data/logs
  redact_status=$?

  printf 'model_exit=%s\ndocker_rm=%s\ncontainer_verify=%s\ncontainer_removal=%s\nremote_cleanup=%s\nredaction=%s\n' \
    "$model_status" "$rm_status" "$verify_status" "$removal_status" \
    "$cleanup_status" "$redact_status" > "$lifecycle_file"
  trap - EXIT
  if (( model_status != 0 || removal_status != 0 || cleanup_status != 0 || redact_status != 0 )); then
    exit 1
  fi
  exit 0
}
trap cleanup_smoke EXIT

timeout --signal=TERM --kill-after=30s 1020s docker run --rm --cidfile "$CID_FILE" \
  --env MINIMAX_API_KEY --env E2E_SANDBOX_TOKEN \
  --volume "$PWD/scripts/e2e:/opt/e2e:ro" \
  --volume "$E2E_DIR/out:/artifacts" \
  --volume "$E2E_DIR/data:/home/autoskillit/.local/share/autoskillit" \
  "$IMAGE" python3 /opt/e2e/e2e_harness.py run --test "$TEST" --out /artifacts
```

Select it directly with workflow dispatch input `tests=headless-smoke`.
