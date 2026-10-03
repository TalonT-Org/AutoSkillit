"""Registry-driven checks for the hook output protocol."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests._hook_channel_scan import (
    HOOKS_DIR,
    all_registered_hook_defs,
    scan_script_channels,
    scan_source_sinks,
    validate_emitting_helper_inventory,
)
from tests._hook_protocol_oracle import KNOWN_UNSUPPORTED_CHANNELS

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_EMITTER = "autoskillit.hooks._runtime._hook_output"
_CODEX_ROUTES: tuple[tuple[str, str | None], ...] = (
    ("unmanaged", None),
    ("parent", "parent"),
    ("leaf", "leaf"),
    ("interactive-parent", "interactive-parent"),
)

_CODEX_BASE = frozenset(
    {
        ("PostToolUse", "lifecycle/child_outcome_hook.py"),
        ("PostToolUse", "lint_after_edit_hook.py"),
        ("PostToolUse", "quota_guard_state_post_hook.py"),
        ("PostToolUse", "quota_post_hook.py"),
        ("PostToolUse", "recipe_confirmed_post_hook.py"),
        ("PostToolUse", "resume_gate_post_hook.py"),
        ("PostToolUse", "review_gate_post_hook.py"),
        ("PostToolUse", "token_summary_hook.py"),
        ("PreCompact", "guards/auto_compact_guard.py"),
        ("PreToolUse", "guards/artifact_download_guard.py"),
        ("PreToolUse", "guards/background_exec_guard.py"),
        ("PreToolUse", "guards/branch_protection_guard.py"),
        ("PreToolUse", "guards/compose_pr_body_guard.py"),
        ("PreToolUse", "guards/fabricated_completion_guard.py"),
        ("PreToolUse", "guards/fleet_claim_guard.py"),
        ("PreToolUse", "guards/fleet_dispatch_guard.py"),
        ("PreToolUse", "guards/generated_file_write_guard.py"),
        ("PreToolUse", "guards/git_ops_guard.py"),
        ("PreToolUse", "guards/github_mutation_guard.py"),
        ("PreToolUse", "guards/ingredient_lock_guard.py"),
        ("PreToolUse", "guards/installation_integrity_guard.py"),
        ("PreToolUse", "guards/open_kitchen_guard.py"),
        ("PreToolUse", "guards/pipeline_step_guard.py"),
        ("PreToolUse", "guards/planner_gh_discovery_guard.py"),
        ("PreToolUse", "guards/planner_result_naming_guard.py"),
        ("PreToolUse", "guards/pr_create_guard.py"),
        ("PreToolUse", "guards/quota_guard.py"),
        ("PreToolUse", "guards/recipe_read_guard.py"),
        ("PreToolUse", "guards/remove_clone_guard.py"),
        ("PreToolUse", "guards/reset_resume_gate.py"),
        ("PreToolUse", "guards/resource_exhaustion_guard.py"),
        ("PreToolUse", "guards/resume_ownership_guard.py"),
        ("PreToolUse", "guards/review_loop_gate.py"),
        ("PreToolUse", "guards/skill_cmd_guard.py"),
        ("PreToolUse", "guards/skill_command_guard.py"),
        ("PreToolUse", "guards/skill_load_guard.py"),
        ("PreToolUse", "guards/skill_orchestration_guard.py"),
        ("PreToolUse", "guards/test_runner_guard.py"),
        ("PreToolUse", "guards/unsafe_install_guard.py"),
        ("PreToolUse", "guards/write_guard.py"),
        ("PreToolUse", "shell_capture_hook.py"),
        ("SessionStart", "capture_lifecycle_hook.py"),
    }
)
_MANAGED_PARENT_ADDITIONS = frozenset(
    {
        ("PreToolUse", "guards/join_followup_guard.py"),
        ("Stop", "guards/join_stop_guard.py"),
        ("Stop", "lifecycle/child_outcome_hook.py"),
    }
)
_INTERACTIVE_REMOVALS = frozenset(
    {
        ("PostToolUse", "lint_after_edit_hook.py"),
        ("PreToolUse", "guards/compose_pr_body_guard.py"),
        ("PreToolUse", "guards/fleet_dispatch_guard.py"),
        ("PreToolUse", "guards/planner_gh_discovery_guard.py"),
        ("PreToolUse", "guards/planner_result_naming_guard.py"),
        ("PreToolUse", "guards/recipe_read_guard.py"),
        ("PreToolUse", "guards/resume_ownership_guard.py"),
        ("PreToolUse", "guards/skill_load_guard.py"),
        ("PreToolUse", "guards/skill_orchestration_guard.py"),
        ("PreToolUse", "guards/test_runner_guard.py"),
    }
)
_INTERACTIVE_ADDITIONS = frozenset(
    {
        ("PostToolUse", "session_lifetime_notice_hook.py"),
        ("PreToolUse", "guards/join_followup_guard.py"),
        ("PreToolUse", "guards/mcp_health_advisor.py"),
        ("PreToolUse", "guards/recipe_write_advisor.py"),
        ("SessionStart", "session_start_hook.py"),
        ("Stop", "guards/join_stop_guard.py"),
        ("Stop", "lifecycle/child_outcome_hook.py"),
    }
)
_EXPECTED_CODEX_EMISSION = {
    "unmanaged": _CODEX_BASE,
    "parent": _CODEX_BASE | _MANAGED_PARENT_ADDITIONS,
    "leaf": _CODEX_BASE,
    "interactive-parent": (_CODEX_BASE - _INTERACTIVE_REMOVALS) | _INTERACTIVE_ADDITIONS,
}


def _emitted_hooks() -> Iterator[tuple[str, str, Any]]:
    """Yield each definition the two hook renderers publish or execute."""
    from autoskillit.execution.backends._codex_hooks import (
        MANAGED_CODEX_ROUTE_NAMES,
        codex_emitted_hook_defs,
    )
    from autoskillit.hook_registry import HOOK_REGISTRY, published_hook_defs

    assert tuple(route for _, route in _CODEX_ROUTES[1:]) == MANAGED_CODEX_ROUTE_NAMES
    for route_label, route in _CODEX_ROUTES:
        for include_runtime_only in (False, True):
            for hook_def in codex_emitted_hook_defs(
                managed_route=route,
                include_runtime_only=include_runtime_only,
            ):
                yield "codex", route_label, hook_def
    for hook_def in published_hook_defs(HOOK_REGISTRY):
        yield "claude", "published", hook_def


def _registered_events_by_script() -> dict[str, set[str]]:
    registered: dict[str, set[str]] = {}
    for hook_def in all_registered_hook_defs():
        for script in hook_def.scripts:
            registered.setdefault(script, set()).add(hook_def.event_type)
    return registered


def _render_channel(emitter: Any, channel: str, event: str) -> Any:
    if channel == "deny":
        return emitter.render_deny("permission denied by protocol contract test")
    if channel == "block":
        return emitter.render_block("blocked by protocol contract test")
    if channel == "context":
        return emitter.render_context(event, "context from protocol contract test")
    if channel == "rewrite_input":
        return emitter.render_allow_with_updated_input({"command": "safe"})
    if channel == "rewrite_mcp_output":
        return emitter.render_mcp_tool_output({"formatted": True})
    if channel == "notify":
        kwargs = {"context": "context"} if event == "PostToolUse" else {}
        return emitter.render_notify(event, "notice from protocol contract test", **kwargs)
    if channel == "halt":
        return emitter.render_halt(
            "stop requested by protocol contract test",
            system_message="stop message",
        )
    raise AssertionError(f"unknown hook output channel {channel}")


def test_emitted_hook_channels_take_effect() -> None:
    emitter = importlib.import_module(_EMITTER)
    from tests._hook_protocol_oracle import EXPECTED_EFFECT, claude_verdict, codex_verdict

    registered_events = _registered_events_by_script()
    for backend, route, hook_def in _emitted_hooks():
        for script in hook_def.scripts:
            channels = scan_script_channels(script)
            for channel, events in channels.items():
                literal_events = {event for event in events if event is not None}
                script_events = registered_events.get(script, set())
                assert literal_events <= script_events, (
                    f"{script} emits {channel} for {sorted(literal_events - script_events)} "
                    f"but is registered for {sorted(script_events)}"
                )
                targets = (
                    [hook_def.event_type]
                    if not literal_events or hook_def.event_type in literal_events
                    else []
                )
                for event in targets:
                    emission = _render_channel(emitter, channel, event)
                    oracle = codex_verdict if backend == "codex" else claude_verdict
                    verdict = oracle(
                        event,
                        exit_code=emission.exit_code,
                        stdout=emission.stdout,
                        stderr=emission.stderr,
                    )
                    if (backend, channel, event) in KNOWN_UNSUPPORTED_CHANNELS:
                        assert not EXPECTED_EFFECT[channel](verdict)
                        continue
                    assert EXPECTED_EFFECT[channel](verdict), (
                        f"{backend} route {route} script {script} channel {channel} "
                        f"event {event} was not honored. Use a supported channel or declare "
                        "the hook codex_status='not-applicable' with "
                        "enforcement_strength['codex']."
                    )


def test_every_emitting_script_declares_channels_through_the_emitter() -> None:
    failures: list[str] = []
    for backend, route, hook_def in _emitted_hooks():
        for script in hook_def.scripts:
            path = HOOKS_DIR / script
            sinks = scan_source_sinks(path.read_text(encoding="utf-8"), filename=script)
            if sinks and not scan_script_channels(script):
                locations = ", ".join(f"{sink.line}:{sink.kind}" for sink in sinks)
                failures.append(f"{backend} route {route} {script}: {locations}")

    assert not failures, (
        "Emitting hook scripts must call a channel in _runtime/_hook_output.py: "
        + "; ".join(failures)
    )


def test_emitting_helper_inventory_is_exact() -> None:
    validate_emitting_helper_inventory()


def test_codex_emitted_events_exist_in_codex() -> None:
    from tests._hook_protocol_oracle import CODEX_EVENTS

    for backend, route, hook_def in _emitted_hooks():
        if backend == "codex":
            assert hook_def.event_type in CODEX_EVENTS, (
                f"Codex route {route} emits unsupported event {hook_def.event_type}"
            )


def test_codex_protocol_oracle_tracks_min_version() -> None:
    from autoskillit.execution.backends._codex_discovery import CODEX_CLI_MIN_VERSION
    from tests._hook_protocol_oracle import (
        CODEX_EVENTS,
        CODEX_OUTPUT_EVENTS,
        CODEX_PROTOCOL_VERSION,
    )

    assert CODEX_PROTOCOL_VERSION == CODEX_CLI_MIN_VERSION, (
        "Bumping CODEX_CLI_MIN_VERSION requires re-vendoring "
        "tests/fixtures/codex_hook_protocol/<version>/ and re-verifying the oracle's "
        "semantic rules at that tag"
    )
    fixture_dir = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "codex_hook_protocol"
        / str(CODEX_PROTOCOL_VERSION)
    )
    assert fixture_dir.is_dir()
    assert CODEX_EVENTS - CODEX_OUTPUT_EVENTS == {"SessionEnd"}


def test_codex_emission_inventory_is_pinned() -> None:
    from autoskillit.execution.backends._codex_hooks import codex_emitted_hook_defs

    for route_label, route in _CODEX_ROUTES:
        actual = {
            (hook_def.event_type, script)
            for hook_def in codex_emitted_hook_defs(managed_route=route, include_runtime_only=True)
            for script in hook_def.scripts
        }
        expected = _EXPECTED_CODEX_EMISSION[route_label]
        added = sorted(actual - expected)
        removed = sorted(expected - actual)
        assert actual == expected, (
            f"A routing/scope change altered which hooks Codex runs on route {route_label}: "
            f"added {added}, removed {removed}. Review each added hook's output channels "
            "(test_emitted_hook_channels_take_effect) and its payload assumptions "
            "(Codex tool names/apply_patch, SessionStart source) before updating this inventory."
        )


def test_known_unsupported_channels_are_declared() -> None:
    violations = []
    for backend, _route, hook_def in _emitted_hooks():
        for script in hook_def.scripts:
            for channel, events in scan_script_channels(script).items():
                event = hook_def.event_type
                if (backend, channel, event) in KNOWN_UNSUPPORTED_CHANNELS and (
                    None in events or event in events
                ):
                    violations.append((backend, channel, event, script))

    assert not violations, f"unsupported hook channels are still emitted: {sorted(violations)}"
