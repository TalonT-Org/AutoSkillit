"""Tests for HookDef.codex_status field and codex config filtering."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.execution.backends._codex_hooks import generate_codex_hooks_config
from autoskillit.hook_registry import (
    HOOK_REGISTRY_HASH,
    LIFECYCLE_CONTRACTS,
    RETIRED_SCRIPT_BASENAMES,
    HookDef,
    _canonical_registry_payload,
    compute_registry_hash,
)
from autoskillit.hooks import HOOK_REGISTRY

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]


class TestHookDefCodexStatus:
    def test_hook_def_has_codex_status_field(self):
        field_names = {f.name for f in dataclasses.fields(HookDef)}
        assert "codex_status" in field_names

    def test_all_hook_registry_entries_have_codex_status(self):
        for hook_def in HOOK_REGISTRY:
            assert hook_def.codex_status in (
                "works-as-is",
                "degraded",
                "fix-required",
                "not-applicable",
            ), f"Hook {hook_def.scripts} has invalid codex_status: {hook_def.codex_status!r}"

    def test_generate_codex_hooks_config_excludes_fix_required(self):
        fix_required_matchers = {
            hd.matcher for hd in HOOK_REGISTRY if hd.codex_status == "fix-required"
        }
        config = generate_codex_hooks_config()
        for _event_type, entries in config.items():
            for entry in entries:
                assert entry.get("matcher") not in fix_required_matchers, (
                    f"fix-required hook with matcher={entry['matcher']!r} "
                    "must not appear in Codex config"
                )

    def test_generate_codex_hooks_config_excludes_not_applicable(self):
        not_applicable_matchers = {
            hd.matcher for hd in HOOK_REGISTRY if hd.codex_status == "not-applicable"
        }
        config = generate_codex_hooks_config()
        for _event_type, entries in config.items():
            for entry in entries:
                assert entry.get("matcher") not in not_applicable_matchers, (
                    f"not-applicable hook with matcher={entry.get('matcher')!r} "
                    "must not appear in Codex config"
                )

    def test_hookdef_has_mechanism_field(self):
        field_names = {f.name for f in dataclasses.fields(HookDef)}
        assert "mechanism" in field_names

    def test_all_registry_entries_have_valid_mechanism(self):
        for hook_def in HOOK_REGISTRY:
            assert hook_def.mechanism in (
                "deny",
                "additionalContext",
                "output-rewrite",
                "input-rewrite",
                "side-effect",
            ), f"Hook {hook_def.scripts} has invalid mechanism: {hook_def.mechanism!r}"

    def test_pretooluse_deny_mechanism_is_set(self):
        for hook_def in HOOK_REGISTRY:
            if (
                hook_def.event_type == "PreToolUse"
                and hook_def.codex_status != "not-applicable"
                and hook_def.mechanism != "input-rewrite"
            ):
                assert hook_def.mechanism == "deny", (
                    f"Hook {hook_def.scripts} has mechanism={hook_def.mechanism!r}, "
                    "expected 'deny' for PreToolUse hooks"
                )

    def test_posttooluse_pretty_output_is_output_rewrite(self):
        for hook_def in HOOK_REGISTRY:
            if (
                hook_def.event_type == "PostToolUse"
                and "formatters/pretty_output_hook.py" in hook_def.scripts
            ):
                assert hook_def.mechanism == "output-rewrite", (
                    f"Hook {hook_def.scripts} has mechanism={hook_def.mechanism!r}, "
                    "expected 'output-rewrite' for pretty_output_hook"
                )

    def test_hookdef_has_enforcement_strength_field(self):
        field_names = {f.name for f in dataclasses.fields(HookDef)}
        assert "enforcement_strength" in field_names

    def test_enforcement_strength_default_is_empty_dict(self):
        hd = HookDef(matcher="test", scripts=["s.py"])
        assert hd.enforcement_strength == {}

    def test_all_registry_entries_have_enforcement_strength_keys(self):
        for hd in HOOK_REGISTRY:
            assert "claude_code" in hd.enforcement_strength, (
                f"Hook {hd.scripts} missing 'claude_code' key"
            )
            assert "codex" in hd.enforcement_strength, f"Hook {hd.scripts} missing 'codex' key"

    def test_enforcement_strength_claude_code_values_are_valid(self):
        for hd in HOOK_REGISTRY:
            assert hd.enforcement_strength["claude_code"] in (
                "hard",
                "soft",
                "not-applicable",
            ), (
                f"Hook {hd.scripts} has invalid claude_code: "
                f"{hd.enforcement_strength['claude_code']!r}"
            )

    def test_enforcement_strength_codex_matches_codex_status(self):
        for hd in HOOK_REGISTRY:
            assert hd.enforcement_strength["codex"] == hd.codex_status, (
                f"Hook {hd.scripts}: enforcement_strength codex="
                f"{hd.enforcement_strength['codex']!r} != "
                f"codex_status={hd.codex_status!r}"
            )

    def test_compute_registry_hash_changes_on_enforcement_strength(self):
        original = HOOK_REGISTRY[0]
        mutated_entry = dataclasses.replace(
            original,
            enforcement_strength={"claude_code": "hard", "codex": "test-sentinel"},
        )
        mutated_registry = [mutated_entry, *HOOK_REGISTRY[1:]]
        new_hash = compute_registry_hash(
            mutated_registry,
            RETIRED_SCRIPT_BASENAMES,
            LIFECYCLE_CONTRACTS,
        )
        assert new_hash != HOOK_REGISTRY_HASH

    def test_format_version_is_4(self):
        import json

        payload = json.loads(
            _canonical_registry_payload(
                HOOK_REGISTRY,
                RETIRED_SCRIPT_BASENAMES,
                LIFECYCLE_CONTRACTS,
            )
        )
        assert payload["format_version"] == 4

    def test_enforcement_strength_round_trip(self):
        es = {"claude_code": "hard", "codex": "works-as-is"}
        hd = HookDef(matcher="test", scripts=["s.py"], enforcement_strength=es)
        assert hd.enforcement_strength == es
        assert hd.enforcement_strength["claude_code"] == "hard"
        assert hd.enforcement_strength["codex"] == "works-as-is"

    def test_canonical_registry_payload_includes_enforcement_strength(self):
        import json

        payload_str = _canonical_registry_payload(
            HOOK_REGISTRY,
            RETIRED_SCRIPT_BASENAMES,
            LIFECYCLE_CONTRACTS,
        )
        payload = json.loads(payload_str)
        for row in payload["registry"]:
            assert "enforcement_strength" in row, (
                f"Registry row for matcher={row.get('matcher')!r} is missing"
                " 'enforcement_strength'. Add it to _canonical_registry_payload."
            )
