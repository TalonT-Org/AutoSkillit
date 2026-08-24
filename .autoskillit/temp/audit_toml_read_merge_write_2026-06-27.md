# TOML Read-Merge-Write Audit Record

**Date:** 2026-06-27
**Scope:** Confirm the TOML read-merge-write path in `_codex_config.py` and `_codex_hooks.py` preserves unknown configuration keys.
**Verdict:** CORRECT — the path preserves all keys by design.

## Finding 1: `_upsert_config_value` is absent

`grep -r '_upsert_config_value' src/ tests/` returns zero hits. No upsert helper exists that could bypass the dict-based pipeline and overwrite keys.

## Finding 2: `ensure_codex_mcp_registered` follows read-mutate-write

File: `src/autoskillit/execution/backends/_codex_config.py`, lines 272–325.

For valid TOML files (lines 313–325):
1. **Read:** `_read_codex_config(config_path)` calls `tomllib.loads()` (lines 195–203); it returns a `ReadResult` wrapper — the caller unpacks the dict via `config = result.data` (line 314).
2. **Mutate in-place:** Three targeted mutations — `config.setdefault("mcp_servers", {})["autoskillit"] = entry`, `config.setdefault("tool_output_token_limit", ...)`, `config["model_auto_compact_token_limit"] = ...` (lines 321–323). No keys are removed.
3. **Write:** `_write_codex_config(config_path, config, source=result)` calls `atomic_write(path, _serialize_toml(data))` (line 212).

The corrupt-file branch (lines 299–312) uses text-level `safe_upsert_section` / `_ensure_top_level_key` edits, which also preserve surrounding content.

## Finding 3: `_serialize_toml` has no key allowlist

File: `src/autoskillit/execution/backends/_codex_config.py`, lines 172–188.

The function iterates `data.items()` three times: once for top-level scalars (line 174), once for top-level dicts (line 180), once for arrays-of-tables (line 183). There is no key exclusion list, no allowlist, no filtering. Every key present in the input dict is unconditionally emitted to the output string. Unknown keys are preserved by design.

## Finding 4: `_format_toml_value` raises `TypeError` on unsupported types

File: `src/autoskillit/execution/backends/_codex_config.py`, lines 49–69.

The function handles `bool`, `str`, `int`, `float`, and `list`. The final `else` branch (lines 68–69) raises `TypeError(f"Unsupported TOML value type: {type(v).__name__}")`. There is no silent data loss — unsupported types cause an immediate, visible failure.

## Finding 5: `sync_hooks_to_codex_config` follows the same pattern, touching only `hooks`

File: `src/autoskillit/execution/backends/_codex_hooks.py`, lines 118–154.

1. **Read:** `_read_codex_config(config_path)` (line 127).
2. **Mutate:** `config["hooks"] = merged` (line 152) — this is the only key modified. All other top-level keys pass through untouched.
3. **Write:** `_write_codex_config(config_path, config, source=result)` (line 153).

The corrupt-file path (lines 128–133) uses `_upsert_hooks_text` for text-level block replacement, also preserving non-hook content.

## Finding 6: Pre-existing coverage gap and how new tests close it

File: `tests/execution/backends/test_codex_config.py`, class `TestDestructiveOverwritePrevention` (line 556).

**Pre-existing tests (5):** Covered corrupt-file append behavior and one valid-TOML scenario (`test_ensure_mcp_registered_full_rewrite_on_valid_toml`) that only checked `mcp_servers.other` preservation — not arbitrary top-level scalars.

**Coverage gap:** No test verified that non-MCP top-level scalar keys (e.g., `model`, `theme`, `disable_telemetry`) survive the read-mutate-write pipeline on the valid-TOML branch. A serializer regression skipping non-dict top-level keys would have gone undetected.

**New tests closing the gap (3):**
1. `test_preserves_unknown_top_level_scalars` (line 605) — fresh registration preserves `model`, `theme`, `disable_telemetry`.
2. `test_preserves_unknown_top_level_scalars_on_re_registration` (line 620) — stale `tool_timeout_sec` forces rewrite; unknown scalars survive.
3. `test_sync_preserves_unknown_top_level_scalars` in `tests/hooks/test_codex_hooks.py` (line 195) — `sync_hooks_to_codex_config` preserves `model`, `theme`.

## Conclusion

The TOML read-merge-write path is correct. Unknown keys are preserved at every stage because: (a) `_read_codex_config` returns the complete parsed dict, (b) mutation functions modify only targeted keys without removing others, and (c) `_serialize_toml` emits all dict entries with no allowlist filtering. Three new tests now provide regression coverage for this property.