"""Structural contracts for the token summary pipeline."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from autoskillit.recipe._cmd_rpc import batch_create_issues
from autoskillit.smoke_utils import patch_pr_token_summary

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


# ── Step 1a: Bridge contract: TOKEN_USAGE_FILE_KEYS must match TokenUsageFileEntry ──


def test_token_usage_file_keys_match_typed_dict():
    """Bridge contract: TOKEN_USAGE_FILE_KEYS must equal TokenUsageFileEntry annotations.

    Analogous to test_hook_bridge_coverage.py for the quota guard.
    If this fails, update TOKEN_USAGE_FILE_KEYS and TokenUsageFileEntry together.
    """
    from autoskillit.core.types import TokenUsageFileEntry
    from autoskillit.hooks._hook_settings import TOKEN_USAGE_FILE_KEYS

    assert TOKEN_USAGE_FILE_KEYS == set(TokenUsageFileEntry.__annotations__.keys())


# ── Step 1b: AST contract: hook reads only keys from TOKEN_USAGE_FILE_KEYS ──


def test_hook_load_sessions_reads_only_declared_keys():
    """AST contract: every data.get() key in _load_sessions must be in TOKEN_USAGE_FILE_KEYS.

    Follows test_config_field_coverage.py pattern. Prevents silent key drift
    when fields are renamed in the TypedDict but not in the hook.
    """
    from autoskillit.hooks._hook_settings import TOKEN_USAGE_FILE_KEYS

    hook_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "autoskillit"
        / "hooks"
        / "token_summary_hook.py"
    )
    tree = ast.parse(hook_path.read_text())

    # Find _load_sessions function
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_load_sessions":
            # Collect all data.get("key") string arguments
            disk_keys: set[str] = set()
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "get"
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "data"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)
                ):
                    disk_keys.add(call.args[0].value)

            assert disk_keys, "No data.get() calls found in _load_sessions"
            unexpected = disk_keys - TOKEN_USAGE_FILE_KEYS
            assert not unexpected, (
                f"_load_sessions reads keys not in TOKEN_USAGE_FILE_KEYS: {unexpected}. "
                f"Update the hook or TOKEN_USAGE_FILE_KEYS."
            )
            break
    else:
        raise AssertionError("_load_sessions function not found in token_summary_hook.py")


def test_format_functions_use_no_legacy_cache_keys():
    """AST contract: format functions must not contain legacy cache field name literals.

    Guards against re-introducing cache_creation_input_tokens or cache_read_input_tokens
    in _format_table, _format_efficiency_table, and _format_model_table after the
    canonical migration (P2-A14-WP1).
    """
    from autoskillit.hooks._hook_settings import _V1_TOKEN_FIELD_ALIASES

    hook_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "autoskillit"
        / "hooks"
        / "token_summary_hook.py"
    )
    tree = ast.parse(hook_path.read_text())

    target_functions = {"_format_table", "_format_efficiency_table", "_format_model_table"}
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in target_functions:
            found.add(node.name)
            for const in ast.walk(node):
                if (
                    isinstance(const, ast.Constant)
                    and isinstance(const.value, str)
                    and const.value in _V1_TOKEN_FIELD_ALIASES
                ):
                    pytest.fail(
                        f"{node.name} contains legacy key literal {const.value!r} "
                        f"(maps to {_V1_TOKEN_FIELD_ALIASES[const.value]!r})"
                    )

    assert found == target_functions, (
        f"Expected to find functions {target_functions}, found {found}. "
        "One or more format functions were renamed."
    )


# ── Step 1c: Cross-seam integration test ──


def test_flush_to_hook_cross_seam(tmp_path):
    """Cross-seam: flush_session_log output must produce non-empty hook aggregation.

    This is the missing link between producer tests (test_session_log_flush.py)
    and consumer tests (test_token_summary_appender.py). Proves the hook can
    actually read what the producer writes.
    """
    # Simulate what flush_session_log (session_log.py:384-399) writes to token_usage.json
    session_dir_name = "test-session-abc123"
    # log_root is the sessions/ dir — _load_sessions reads sessions.jsonl from here
    # and looks for sessions/{dir_name}/token_usage.json
    log_root = tmp_path / "sessions"
    log_root.mkdir(parents=True)

    # Write sessions.jsonl index entry so _load_sessions discovers the session dir
    sessions_jsonl = log_root / "sessions.jsonl"
    sessions_jsonl.write_text(
        json.dumps(
            {"session_id": "abc123", "dir_name": session_dir_name, "order_id": "test-order"}
        )
        + "\n"
    )

    # Write the token_usage.json that flush_session_log produces
    session_dir = log_root / "sessions" / session_dir_name
    session_dir.mkdir(parents=True)
    tu_data = {
        "session_label": "plan",
        "input_tokens": 100,
        "output_tokens": 200,
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
        "timing_seconds": 1.5,
        "order_id": "test-order",
        "loc_insertions": 10,
        "loc_deletions": 5,
        "peak_context": 50,
        "turn_count": 3,
        "provider_used": "test-provider",
        "model_identifier": "test-model",
        "dispatch_id": "test-dispatch",
        "campaign_id": "test-campaign",
        "schema_version": 2,
    }
    (session_dir / "token_usage.json").write_text(json.dumps(tu_data))

    # Feed the file through the hook's _load_sessions — proves cross-seam coherence
    from autoskillit.hooks import token_summary_hook

    aggregated = token_summary_hook._load_sessions(log_root, "", order_id="test-order")
    assert aggregated, (
        "_load_sessions returned empty dict — hook cannot read what flush_session_log wrote. "
        "Check key name: flush writes 'session_label' but hook may read 'step_name'."
    )
    key = token_summary_hook._canonical("plan")
    assert key in aggregated, f"Expected key 'plan' in aggregated, got: {list(aggregated.keys())}"
    entry = aggregated[key]
    assert entry["input_tokens"] == 100
    assert entry["output_tokens"] == 200


# ── Step 1d-alias: Bridge contract: _V1_TOKEN_FIELD_ALIASES structural correctness ──


def test_v1_alias_map_structural_correctness():
    """Bridge contract: _V1_TOKEN_FIELD_ALIASES maps old names to canonical names.

    Structural invariants:
    - Every value is a canonical name in TOKEN_USAGE_FILE_KEYS.
    - No key appears in TOKEN_USAGE_FILE_KEYS (old names are excluded).
    """
    from autoskillit.hooks._hook_settings import (
        _V1_TOKEN_FIELD_ALIASES,
        TOKEN_USAGE_FILE_KEYS,
    )

    for old_name, canonical_name in _V1_TOKEN_FIELD_ALIASES.items():
        assert isinstance(old_name, str) and old_name, (
            f"Alias key must be non-empty str: {old_name!r}"
        )
        assert isinstance(canonical_name, str) and canonical_name, (
            f"Alias value must be non-empty str: {canonical_name!r}"
        )
        assert canonical_name in TOKEN_USAGE_FILE_KEYS, (
            f"Alias value {canonical_name!r} not in TOKEN_USAGE_FILE_KEYS"
        )
        assert old_name not in TOKEN_USAGE_FILE_KEYS, (
            f"Alias key {old_name!r} must not be in TOKEN_USAGE_FILE_KEYS "
            "(old name leaked into canonical set)"
        )


def test_v1_alias_map_covers_known_legacy_keys():
    """Content-pinning: alias map must contain the exact known v1 -> v2 mappings."""
    from autoskillit.hooks._hook_settings import _V1_TOKEN_FIELD_ALIASES

    assert _V1_TOKEN_FIELD_ALIASES == {
        "cache_creation_input_tokens": "cache_write_tokens",
        "cache_read_input_tokens": "cache_read_tokens",
    }


# ── Step 1d: patch_pr_token_summary accepts timeout ──


def test_patch_pr_token_summary_accepts_timeout():
    """patch_pr_token_summary must accept timeout kwarg to absorb LLM arg-routing variance."""
    sig = inspect.signature(patch_pr_token_summary)
    assert "timeout" in sig.parameters, (
        "patch_pr_token_summary must accept 'timeout' parameter. "
        "Fleet LLMs may route the recipe with: timeout into args."
    )


# ── Step 1e: batch_create_issues accepts timeout ──


def test_batch_create_issues_accepts_timeout():
    """batch_create_issues must accept timeout kwarg to absorb LLM arg-routing variance."""
    sig = inspect.signature(batch_create_issues)
    assert "timeout" in sig.parameters


# ── Existing structural contracts ──


def test_patch_pr_token_summary_uses_order_id_not_cwd_filter():
    """Structural contract: patch_pr_token_summary must accept order_id parameter.

    The presence of order_id in the signature is the canonical guard against regression
    to cwd_filter-based scoping. If someone removes it, this test fails immediately.
    """
    sig = inspect.signature(patch_pr_token_summary)
    assert "order_id" in sig.parameters, (
        "patch_pr_token_summary must have an 'order_id' parameter. "
        "This is the canonical scoping key for multi-clone pipelines. "
        "Do not remove it or replace it with cwd_filter."
    )


def test_patch_pr_token_summary_cwd_is_optional():
    """cwd must have a default value so callers can omit it when using order_id."""
    sig = inspect.signature(patch_pr_token_summary)
    assert "cwd" in sig.parameters
    cwd_param = sig.parameters["cwd"]
    assert cwd_param.default == "", (
        "cwd must default to '' so fleet callers can omit it and rely on order_id scoping."
    )
