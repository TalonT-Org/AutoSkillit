# CI Workflow Constraints

Rules for maintaining `.github/workflows/` files. Enforced for both human and AI contributors.

## setup-uv parameter names

The correct input parameter for pinning the uv version is `version`, not `uv-version`.

```yaml
# CORRECT — SHA-pinned with version comment
- uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
  with:
    version: "0.9.21"

# WRONG — floating tag, vulnerable to tag mutation
- uses: astral-sh/setup-uv@v8
  with:
    version: "0.9.21"

# WRONG — silently ignored, installs latest uv instead of pinned version
- uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
  with:
    uv-version: "0.9.21"
```

`uv-version` is an *output* of the action (the resolved installed version), not an input.

## Caching discipline

Jobs that do not run `uv sync` MUST disable setup-uv caching to avoid poisoning the
wheel cache consumed by downstream jobs:

```yaml
- uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
  with:
    version: "0.9.21"
    enable-cache: false   # required for jobs that skip uv sync
```

Jobs that run `uv sync --locked` may normally omit `enable-cache`. In
`.github/workflows/tests.yml`, explicit `actions/cache` owns their uv-cache I/O,
so setup-uv caching stays disabled. `api_simulator_cache` is the sole saver; Linux
matrix jobs restore only and verify imports of the native api-simulator submodules.
The build identity uses the runner image family and toolchain pins, never `uv.lock`,
so package-only version bumps reuse the build. Keep `preflight` independent and
the required `Test (*)` contexts reporting producer failures instead of leaving
the merge queue waiting for missing checks.

If an entry is unusable, delete it with `gh cache delete <key>` and rerun. Cached
api-simulator source is readable by pull requests into this public repository;
the owner of both repositories accepted that visibility.

This single-saver rule applies only to this workflow's uv cache.

## Branch-targeted test policy

`scripts/ci_target_policy.py` is the executable CI policy authority, and
`docs/developer/contributing.md` is its durable contributor-facing record. Feature and
fix work must not incidentally broaden or narrow CI runners or filtering. Any change
requires an explicit CI-policy task and matching behavior-table tests in
`tests/infra/test_ci_workflow.py`.

## Test-checkout history

The `test` job in `.github/workflows/tests.yml` keeps `actions/checkout` at
`fetch-depth: 0` so the conservative test filter (`AUTOSKILLIT_TEST_FILTER=conservative`)
has the full history required to compute the merge base. Do not make this checkout
shallow without providing a coordinated fallback for merge-base computation.

## Rust toolchain scope

`dtolnay/rust-toolchain` is only needed in jobs that compile native Python extensions. In
`.github/workflows/tests.yml` only `api_simulator_cache` compiles, and only on a cache miss;
the test matrix restores that build and must not install Rust. Do not add it to preflight,
lint, or other utility jobs that only run `uv lock --check` or similar commands.

## Lockfile verification

The `preflight` job exists to validate the lockfile early and cheaply. It must NOT install
the full dependency tree — only `uv lock --check`. Adding `uv sync` to preflight defeats
the purpose of the job separation.

Changing the `api-simulator.rev` value under `[tool.uv.sources]` in `pyproject.toml`
requires running `uv lock` and committing the regenerated `uv.lock` in the same change.
Pre-commit and preflight enforce this consistency with `uv lock --check`, and
`uv sync --locked --extra dev` cannot proceed with a stale lockfile.

## Toolchain pins and image publication

`scripts/docker/Dockerfile`'s `ARG` pins are the single authority for tool versions (uv,
Task, Rust, ripgrep, Claude Code, Codex, Node, pre-commit, jq, gh); a workflow step that
installs or requires one must use the same version, enforced by
`tests/infra/test_docker_image.py`. `docker-image.yml` publishes from develop
pushes that change the version and from `v*` tag pushes, which only start workflows when
pushed with `GH_PAT`, so the bump and release workflows check out with it.

Never pass a credential to an image build as an `ARG` or a `docker/build-push-action`
`build-args` value: `docker history` and the `mode=max` provenance attestation the action
attaches by default for this public repository both record build-arg values. Use
`RUN --mount=type=secret` for build-time secrets.
