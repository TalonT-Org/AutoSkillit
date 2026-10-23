"""Pin the standalone collector's identities, tools and hook policy to the product."""

import json
from pathlib import Path

import pytest

from autoskillit.core import DIRECT_PREFIX, PLUGIN_PREFIX, ClaudeFlags, pkg_root
from autoskillit.core.types import GATED_TOOLS, UNGATED_TOOLS
from autoskillit.execution.backends import claude_resolvable_agent_names
from tests._hook_protocol_oracle import claude_matcher_admits
from tests.infra._complexity_helpers import load_check_script

pytestmark = pytest.mark.medium

sessions = load_check_script(
    "_autoskillit_e2e_sessions_contract",
    Path(__file__).resolve().parents[2] / "scripts" / "e2e" / "e2e_sessions.py",
)


def test_server_identities_match_canonical_prefixes() -> None:
    assert sessions._AUTOSKILLIT_SERVERS == frozenset(
        tuple(prefix.split("__")[:-1]) for prefix in (DIRECT_PREFIX, PLUGIN_PREFIX)
    )


def test_tracked_tools_are_registered_product_tools() -> None:
    assert sessions._TRACKED_TOOLS
    assert sessions._TRACKED_TOOLS <= GATED_TOOLS | UNGATED_TOOLS


def test_agent_hooks_admit_every_resolvable_agent_and_reject_an_unnamed_one() -> None:
    names = claude_resolvable_agent_names()
    for event in sessions._AGENT_EVENTS:
        matcher = sessions.LIFECYCLE_HOOK_MATCHERS[event]
        assert matcher is not None
        assert not claude_matcher_admits(matcher, "")
        assert all(claude_matcher_admits(matcher, name) for name in names)


def test_session_hooks_stay_unfiltered() -> None:
    matchers = sessions.LIFECYCLE_HOOK_MATCHERS
    assert set(matchers) == sessions._LIFECYCLE_EVENTS
    assert all(matchers[event] is None for event in sessions._SESSION_EVENTS)


def test_product_selects_no_main_agent() -> None:
    # Internal-agent callbacks are unnamed only while no main agent is set.
    assert {"--agent", "--settings"}.isdisjoint(flag.value for flag in ClaudeFlags)
    root = pkg_root()
    manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "agent" not in manifest.get("settings", {})
    plugin_settings = root / "settings.json"
    assert not plugin_settings.exists() or "agent" not in json.loads(
        plugin_settings.read_text(encoding="utf-8")
    )
