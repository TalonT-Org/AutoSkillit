# CI Workflow Constraints

Rules for maintaining `.github/workflows/` files. Enforced for both human and AI contributors.

## setup-uv parameter names

The correct input parameter for pinning the uv version is `version`, not `uv-version`.

```yaml
# CORRECT — SHA-pinned with version comment
- uses: astral-sh/setup-uv@94527f2e458b27549849d47d273a16bec83a01e9 # v7
  with:
    version: "0.9.21"

# WRONG — floating tag, vulnerable to tag mutation
- uses: astral-sh/setup-uv@v7
  with:
    version: "0.9.21"

# WRONG — silently ignored, installs latest uv instead of pinned version
- uses: astral-sh/setup-uv@94527f2e458b27549849d47d273a16bec83a01e9 # v7
  with:
    uv-version: "0.9.21"
```

`uv-version` is an *output* of the action (the resolved installed version), not an input.

## Caching discipline

Jobs that do not run `uv sync` MUST disable setup-uv caching to avoid poisoning the
wheel cache consumed by downstream jobs:

```yaml
- uses: astral-sh/setup-uv@94527f2e458b27549849d47d273a16bec83a01e9 # v7
  with:
    version: "0.9.21"
    enable-cache: false   # required for jobs that skip uv sync
```

Jobs that run `uv sync --locked` may omit `enable-cache` (defaults to `auto`, enabled on
GitHub-hosted runners).

## Rust toolchain scope

`dtolnay/rust-toolchain` is only needed in jobs that compile native Python extensions
(i.e., jobs that run `uv sync`). Do not add it to preflight, lint, or other utility jobs
that only run `uv lock --check` or similar commands.

## Lockfile verification

The `preflight` job exists to validate the lockfile early and cheaply. It must NOT install
the full dependency tree — only `uv lock --check`. Adding `uv sync` to preflight defeats
the purpose of the job separation.