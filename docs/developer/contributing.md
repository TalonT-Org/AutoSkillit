# Contributing

## Development Setup

    git clone https://github.com/TalonT-Org/AutoSkillit.git
    cd AutoSkillit
    uv pip install -e '.[dev]'
    pre-commit install
    autoskillit install

> Developers work on `main`. The `stable` branch is the release branch
> for end users.

## Running Tests

    task test-all

Tests run in parallel via pytest-xdist (`-n 4`). All tests must be safe for
parallel execution. Never use `pytest` directly — always use `task test-all`
(or `task test-check` for CI/automation).

## Branch-Targeted CI Policy

The target branch selects both the test runners and the test-filter mode:

| Event target | Runners | Test filter |
|---|---|---|
| `develop` | `ubuntu-latest` | `conservative` |
| `main` | `ubuntu-latest` | `none` |
| `stable` | `ubuntu-latest`, `macos-15` | `none` |

`pull_request` and `merge_group` each allow `develop|main|stable`.
`push` allows only `main|stable`; push-to-develop is rejected. Pull requests and
merge groups targeting the same branch must resolve to the same policy, including
the immutable event base SHA used for conservative filtering. macOS is release-path
coverage for `stable`; it is not a develop-branch requirement.

PR #4396 introduced an unconditional Linux/macOS runner policy. PR #4405 exposed the
regression in merge-queue integration and carries the correction. Feature and fix work
must not incidentally broaden or narrow CI runners or filtering. An intentional policy
change requires an explicit CI-policy task and matching behavioral tests in
`tests/infra/test_ci_workflow.py`.

## Pre-commit Hooks

Hooks run automatically on commit: ruff format, ruff check, mypy, uv lock check,
gitleaks secret scanning, doc count accuracy.

    pre-commit run --all-files

## Architecture Layers

The codebase uses strict import layering enforced by import-linter:

| Layer | Package | May import |
|-------|---------|-----------|
| L0 | `core/` | Nothing (foundation) |
| L1 | `config/`, `pipeline/`, `execution/`, `workspace/` | L0 only |
| L2 | `recipe/`, `migration/` | L0, L1 (workspace only for recipe) |
| L3 | `server/`, `cli/` | Everything |

## Version Bumps

When bumping the version, update three locations:
1. `pyproject.toml` — `version = "X.Y.Z"`
2. `.claude-plugin/plugin.json` — `"version": "X.Y.Z"`
3. Run `uv lock`
4. Search tests for hardcoded version strings and update them

## Further Reading

- **[Session Diagnostics](diagnostics.md)** — Process-level diagnostic logging for headless sessions
- **[End-Turn Hazards](end-turn-hazards.md)** — Stochastic failure modes in skill authoring and how to prevent them
