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

Jobs that run `uv sync --locked` may normally omit `enable-cache` (defaults to `auto`,
enabled on GitHub-hosted runners). In `.github/workflows/tests.yml`, however, the `test`
and `cache_prime` jobs deliberately set `enable-cache: false` despite syncing because
explicit `actions/cache` owns their uv-cache I/O. That workflow's matrix jobs restore only;
its schedule-or-`main`-push default-branch primer is the sole saver for the uv dependency
cache. This single-saver rule does not apply to unrelated workflows or non-uv caches.

## Branch-targeted test policy

`scripts/ci_target_policy.py` is the executable CI policy authority, and
`docs/developer/contributing.md` is its durable contributor-facing record. Feature and
fix work must not incidentally broaden or narrow CI runners or filtering. Any change
requires an explicit CI-policy task and matching behavior-table tests in
`tests/infra/test_ci_workflow.py`.

## Rust toolchain scope

`dtolnay/rust-toolchain` is only needed in jobs that compile native Python extensions
(i.e., jobs that run `uv sync`). Do not add it to preflight, lint, or other utility jobs
that only run `uv lock --check` or similar commands.

## Lockfile verification

The `preflight` job exists to validate the lockfile early and cheaply. It must NOT install
the full dependency tree — only `uv lock --check`. Adding `uv sync` to preflight defeats
the purpose of the job separation.

Changing the `api-simulator.rev` value under `[tool.uv.sources]` in `pyproject.toml`
requires running `uv lock` and committing the regenerated `uv.lock` in the same change.
Pre-commit and preflight enforce this consistency with `uv lock --check`, and
`uv sync --locked --extra dev` cannot proceed with a stale lockfile.
