# GitHub Actions CI — Agent Instructions

## `setup-uv@v7` Cache Behavior

- **Parameter name**: Use `version:` not `uv-version:` — the old name is silently ignored.
- **Cache key**: Exact hash of `uv.lock` + `pyproject.toml` + `*requirements*.txt` + arch/OS/Python. No `restore-keys` fallback — any `uv.lock` change is a total miss.
- **Cache save rule**: GitHub Actions skips save when the key already exists. If job A saves a cache and job B restores it with the same key, job B's changes are never persisted. The first job to complete with a given key "wins."
- **`prune-cache: true` (default)**: Runs `uv cache prune --ci` before save. Source-built wheels survive pruning; pre-built PyPI wheels do not.
- **Implication for source-built deps**: If a job that only validates (e.g. `uv lock --check`) saves the cache before a job that installs (e.g. `uv sync`), the compiled wheel never enters the persisted cache. Either disable caching on the validation job or use a separate `actions/cache` step with a different key.

## `dtolnay/rust-toolchain`

Only needed in jobs that run `uv sync`/`uv pip install` with Rust/PyO3 source deps. Not needed for `uv lock --check` (lockfile validation doesn't build packages).

## `api-simulator` Dependency

Private Rust/PyO3 crate at `TalonT-Org/api-simulator`, built via maturin. Requires `dtolnay/rust-toolchain@stable` and git auth (`GH_USER`/`GH_PAT` secrets via URL rewrite). Gated to `sys_platform == 'linux'` — not built on macOS CI targets.
