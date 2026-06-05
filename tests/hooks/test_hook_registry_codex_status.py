"""Tests for HookDef.codex_status field and codex config filtering."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.execution.backends._codex_hooks import generate_codex_hooks_config
from autoskillit.hook_registry import HookDef
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
