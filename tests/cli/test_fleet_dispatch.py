"""Tests: fleet CLI dispatch command."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from autoskillit.cli.fleet import fleet_dispatch as _fleet_dispatch
from tests.cli._fleet_helpers import (
    _capture_subprocess,
    _stub_guards,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium, pytest.mark.feature("fleet")]


def _fake_recipe(name: str, source: str, description: str) -> object:
    """Create a minimal RecipeInfo-like object for test stubs."""
    return type(
        "RecipeInfo",
        (),
        {
            "name": name,
            "source": source,
            "description": description,
            "kind": "standard",
            "path": Path(f"/fake/{name}.yaml"),
            "summary": "",
            "version": None,
            "recipe_version": None,
            "content_hash": "",
            "content": None,
        },
    )()


def _stub_list_recipes(monkeypatch: pytest.MonkeyPatch, recipes: list) -> None:
    """Stub list_recipes to return the given recipe list."""
    result = type("LoadResult", (), {"items": recipes, "errors": []})()
    monkeypatch.setattr("autoskillit.recipe.list_recipes", lambda *a, **kw: result)


# ---------------------------------------------------------------------------
# T1. fleet dispatch — CLAUDECODE guard
# ---------------------------------------------------------------------------


def test_fleet_dispatch_rejects_inside_claude_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """fleet dispatch exits 1 when CLAUDECODE env is set."""
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(SystemExit, match="1"):
        _fleet_dispatch()


# ---------------------------------------------------------------------------
# T2. fleet dispatch — SESSION_TYPE=skill guard
# ---------------------------------------------------------------------------


def test_fleet_dispatch_rejects_skill_session_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """fleet dispatch exits 1 when ambient SESSION_TYPE is skill."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    monkeypatch.delenv("CLAUDECODE", raising=False)
    with pytest.raises(SystemExit, match="1"):
        _fleet_dispatch()


def test_fleet_dispatch_rejects_deprecated_leaf_session_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fleet dispatch exits 1 when ambient SESSION_TYPE is deprecated 'leaf'."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
    monkeypatch.delenv("CLAUDECODE", raising=False)
    with pytest.raises(SystemExit, match="1"):
        _fleet_dispatch()


# ---------------------------------------------------------------------------
# T3. fleet dispatch — claude not on PATH
# ---------------------------------------------------------------------------


def test_fleet_dispatch_exits_when_claude_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet dispatch exits 1 when claude is not on PATH."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    monkeypatch.setattr("autoskillit.cli.fleet.is_feature_enabled", lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda path: type("C", (), {"features": {}, "experimental_enabled": False})(),
    )
    _stub_list_recipes(monkeypatch, [])
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *a, **kw: "")
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(SystemExit, match="1"):
        _fleet_dispatch()


# ---------------------------------------------------------------------------
# T_GUARD_1: fleet_dispatch exits 1 when fleet feature disabled
# ---------------------------------------------------------------------------


def test_fleet_dispatch_exits_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet_dispatch exits 1 when fleet feature is disabled via feature gate."""
    monkeypatch.chdir(tmp_path)
    _stub_guards(monkeypatch)
    checked_features: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli.fleet.is_feature_enabled",
        lambda name, features, *, experimental_enabled=False: (
            checked_features.append(name) or False
        ),
    )
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda path: type("C", (), {"features": {}, "experimental_enabled": False})(),
    )
    with pytest.raises(SystemExit) as exc_info:
        _fleet_dispatch()
    assert exc_info.value.code == 1
    assert "fleet" in checked_features


# ---------------------------------------------------------------------------
# T_GUARD_4: fleet_dispatch proceeds normally when fleet enabled
# ---------------------------------------------------------------------------


def test_fleet_dispatch_proceeds_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet_dispatch passes the feature guard and proceeds to campaign resolution."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "autoskillit.cli.fleet.is_feature_enabled",
        lambda name, features, *, experimental_enabled=False: True,
    )
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda path: type("C", (), {"features": {}, "experimental_enabled": False})(),
    )
    _stub_list_recipes(monkeypatch, [])
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *a, **kw: "")
    captured = _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    assert captured.get("cmd") is not None, "Expected fleet session subprocess to be invoked"
    assert captured["env"].get("AUTOSKILLIT_FLEET_MODE") == "dispatch"


# ---------------------------------------------------------------------------
# T_ADHOC. Ad-hoc fleet dispatch mode (campaign_name=None)
# ---------------------------------------------------------------------------


def test_fleet_dispatch_sets_fleet_mode_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet_dispatch() must launch an interactive session, not exit 1."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_list_recipes(monkeypatch, [])
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *a, **kw: "")
    captured = _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    assert captured["env"].get("AUTOSKILLIT_FLEET_MODE") == "dispatch"


def test_fleet_dispatch_writes_no_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fleet_dispatch must not create a state.json under .autoskillit/temp/fleet/."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_list_recipes(monkeypatch, [])
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *a, **kw: "")
    _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    fleet_dir = tmp_path / ".autoskillit" / "temp" / "fleet"
    assert not fleet_dir.exists() or not any(fleet_dir.rglob("state.json"))


def test_fleet_dispatch_no_campaign_env_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ad-hoc session must not set AUTOSKILLIT_CAMPAIGN_ID or AUTOSKILLIT_CAMPAIGN_STATE_PATH."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_list_recipes(monkeypatch, [])
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *a, **kw: "")
    captured = _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    env = captured["env"]
    assert "AUTOSKILLIT_CAMPAIGN_ID" not in env
    assert "AUTOSKILLIT_CAMPAIGN_STATE_PATH" not in env


def test_build_fleet_dispatch_prompt_no_open_kitchen() -> None:
    """Fleet dispatch prompt must NOT instruct calling open_kitchen."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "open_kitchen" not in prompt


def test_build_fleet_dispatch_prompt_references_dispatch_tool() -> None:
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "dispatch_food_truck" in prompt


def test_build_fleet_dispatch_prompt_no_campaign_manifest() -> None:
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "DISPATCH MANIFEST" not in prompt
    assert "CAMPAIGN OVERVIEW" not in prompt
    assert "CAMPAIGN DISCIPLINE" not in prompt


def test_build_fleet_dispatch_prompt_accepts_marketplace_prefix() -> None:
    from autoskillit.cli._mcp_names import MARKETPLACE_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(MARKETPLACE_PREFIX)
    assert MARKETPLACE_PREFIX + "open_kitchen" not in prompt
    assert MARKETPLACE_PREFIX + "dispatch_food_truck" in prompt


def test_build_fleet_dispatch_prompt_lists_all_10_tools() -> None:
    """Dispatch prompt must enumerate all 10 tools in the TOOL SURFACE section."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "TOOL SURFACE" in prompt, "Expected TOOL SURFACE section in dispatch prompt"
    tool_surface_start = prompt.index("TOOL SURFACE")
    next_section = prompt.find("\n##", tool_surface_start + 1)
    tool_surface = (
        prompt[tool_surface_start:next_section]
        if next_section != -1
        else prompt[tool_surface_start:]
    )
    for tool in (
        "dispatch_food_truck",
        "batch_cleanup_clones",
        "get_pipeline_report",
        "get_token_summary",
        "get_timing_summary",
        "get_quota_events",
        "list_recipes",
        "load_recipe",
        "fetch_github_issue",
        "get_issue_title",
    ):
        assert tool in tool_surface, (
            f"Expected tool {tool!r} in TOOL SURFACE section of dispatch prompt"
        )


def test_build_fleet_dispatch_prompt_includes_sous_chef_sections() -> None:
    """Dispatch prompt must include the 4 L3 sous-chef sections."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt
    from autoskillit.fleet import _build_l3_sous_chef_block

    sous_chef_block = _build_l3_sous_chef_block()
    assert sous_chef_block
    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "SOUS-CHEF DISCIPLINE" in prompt
    for section in (
        "CONTEXT LIMIT ROUTING",
        "STEP NAME IMMUTABILITY",
        "MERGE PHASE",
        "QUOTA WAIT PROTOCOL",
    ):
        assert section in prompt, f"Expected sous-chef section {section!r} in dispatch prompt"


def test_build_fleet_dispatch_prompt_has_recipe_discovery_guidance() -> None:
    """Dispatch prompt must guide recipe discovery flow."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "list_recipes" in prompt
    assert "load_recipe" in prompt


def test_build_fleet_dispatch_prompt_role_text() -> None:
    """Dispatch prompt must identify role as fleet dispatcher."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "fleet dispatcher" in prompt.lower()


def test_build_fleet_dispatch_prompt_has_cleanup_protocol() -> None:
    """Dispatch prompt must include batch_cleanup_clones exit instruction."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "batch_cleanup_clones" in prompt


def test_build_fleet_dispatch_prompt_no_sleep_toolsearch_preamble() -> None:
    """Dispatch prompt must NOT include sleep/ToolSearch boot sequence."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "sleep 2" not in prompt


def test_build_fleet_dispatch_prompt_uses_ingredients_only() -> None:
    """_build_fleet_dispatch_prompt recipe discovery must mention ingredients_only."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "ingredients_only" in prompt


# ---------------------------------------------------------------------------
# T_PREVIEW: Pre-launch display
# ---------------------------------------------------------------------------


def test_fleet_dispatch_prints_recipe_roster(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """fleet_dispatch prints a food truck roster table before launching."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_list_recipes(
        monkeypatch,
        [
            _fake_recipe("smoke-test", "BUILTIN", "Run smoke tests"),
            _fake_recipe("review-pr", "BUILTIN", "Review a pull request"),
        ],
    )
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *a, **kw: "")
    _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    out = capsys.readouterr().out
    assert "smoke-test" in out
    assert "review-pr" in out


def test_fleet_dispatch_shows_permissions_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """fleet_dispatch prints the permissions_warning text."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_list_recipes(monkeypatch, [])
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *a, **kw: "")
    _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    out = capsys.readouterr().out
    assert "dangerously-skip-permissions" in out


def test_fleet_dispatch_aborts_on_no(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fleet_dispatch returns without launching when user types 'n'."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_list_recipes(monkeypatch, [])
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *a, **kw: "n")
    captured = _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    assert "cmd" not in captured, "Session should not launch when user aborts"


def test_fleet_dispatch_prints_fleet_tool_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """fleet_dispatch prints the 10 fleet tool names before launching."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_list_recipes(monkeypatch, [])
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *a, **kw: "")
    _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    out = capsys.readouterr().out
    assert "dispatch_food_truck" in out
    assert "batch_cleanup_clones" in out
    assert "list_recipes" in out


def test_fleet_dispatch_prints_version_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """fleet_dispatch prints AUTOSKILLIT <version> header line."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_list_recipes(monkeypatch, [])
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *a, **kw: "")
    _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    out = capsys.readouterr().out
    assert "AUTOSKILLIT" in out
    assert "Fleet dispatcher" in out or "fleet dispatcher" in out.lower()


# ---------------------------------------------------------------------------
# T_GREETING: Greeting injection
# ---------------------------------------------------------------------------


def test_fleet_dispatch_passes_initial_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet_dispatch passes a non-None initial_message to _run_interactive_session."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_list_recipes(
        monkeypatch,
        [_fake_recipe("smoke-test", "BUILTIN", "Run smoke tests")],
    )
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *a, **kw: "")
    captured_kwargs: dict = {}

    def mock_run_session(system_prompt: str, **kwargs: object) -> None:
        captured_kwargs.update(kwargs)
        return None

    monkeypatch.setattr(
        "autoskillit.cli.session._session_launch._run_interactive_session",
        mock_run_session,
    )
    _fleet_dispatch()
    assert captured_kwargs.get("initial_message") is not None
    assert "smoke-test" in captured_kwargs["initial_message"]


def test_fleet_dispatch_greeting_contains_recipe_descriptions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The initial_message greeting includes recipe names and descriptions."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_list_recipes(
        monkeypatch,
        [
            _fake_recipe("review-pr", "BUILTIN", "Review a pull request"),
            _fake_recipe("implement", "BUILTIN", "Implement a feature"),
        ],
    )
    monkeypatch.setattr("autoskillit.cli.ui._timed_input.timed_prompt", lambda *a, **kw: "")
    captured_kwargs: dict = {}

    def mock_run_session(system_prompt: str, **kwargs: object) -> None:
        captured_kwargs.update(kwargs)
        return None

    monkeypatch.setattr(
        "autoskillit.cli.session._session_launch._run_interactive_session",
        mock_run_session,
    )
    _fleet_dispatch()
    greeting = captured_kwargs["initial_message"]
    assert "review-pr" in greeting
    assert "implement" in greeting


def test_fleet_dispatch_greetings_have_recipe_table_placeholder() -> None:
    """All _FLEET_DISPATCH_GREETINGS entries must contain {recipe_table}."""
    from autoskillit.cli.fleet._fleet_preview import _FLEET_DISPATCH_GREETINGS

    assert len(_FLEET_DISPATCH_GREETINGS) >= 3
    for greeting in _FLEET_DISPATCH_GREETINGS:
        assert "{recipe_table}" in greeting


# ---------------------------------------------------------------------------
# T_PROMPT: recipe_table injection into system prompt
# ---------------------------------------------------------------------------


def test_build_fleet_dispatch_prompt_embeds_recipe_table() -> None:
    """_build_fleet_dispatch_prompt embeds recipe_table under AVAILABLE FOOD TRUCKS section."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    table = "smoke-test — Run smoke tests\nreview-pr — Review a PR"
    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX, recipe_table=table)
    assert "AVAILABLE FOOD TRUCKS" in prompt
    assert "smoke-test" in prompt
    assert "review-pr" in prompt


def test_build_fleet_dispatch_prompt_no_recipe_table_section_when_none() -> None:
    """_build_fleet_dispatch_prompt omits AVAILABLE FOOD TRUCKS when recipe_table is None."""
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_dispatch_prompt

    prompt = _build_fleet_dispatch_prompt(DIRECT_PREFIX)
    assert "AVAILABLE FOOD TRUCKS" not in prompt


# ---------------------------------------------------------------------------
# T_SESSION: _launch_fleet_session initial_message forwarding
# ---------------------------------------------------------------------------


def test_launch_fleet_session_forwards_initial_message_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_launch_fleet_session passes initial_message to _run_interactive_session."""
    monkeypatch.chdir(tmp_path)
    captured_kwargs: dict = {}

    def mock_run(system_prompt: str, **kwargs: object) -> None:
        captured_kwargs.update(kwargs)
        return None

    monkeypatch.setattr(
        "autoskillit.cli.session._session_launch._run_interactive_session",
        mock_run,
    )
    from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

    _launch_fleet_session(
        None,
        None,
        None,
        None,
        fleet_mode="dispatch",
        initial_message="Hello, dispatcher!",
    )
    assert captured_kwargs.get("initial_message") == "Hello, dispatcher!"


def test_launch_fleet_session_clears_initial_message_on_reload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """On reload, initial_message must be None (only injected on first launch)."""
    monkeypatch.chdir(tmp_path)
    call_count = 0
    captured_messages: list = []

    def mock_run(system_prompt: str, **kwargs: object) -> object:
        nonlocal call_count
        captured_messages.append(kwargs.get("initial_message"))
        call_count += 1
        return "reload-session-abc" if call_count == 1 else None

    monkeypatch.setattr(
        "autoskillit.cli.session._session_launch._run_interactive_session",
        mock_run,
    )
    from autoskillit.cli.fleet._fleet_session import _launch_fleet_session

    _launch_fleet_session(
        None,
        None,
        None,
        None,
        fleet_mode="dispatch",
        initial_message="Hello!",
    )
    assert len(captured_messages) >= 2, (
        f"expected reload to fire but got only {len(captured_messages)} call(s)"
    )
    assert captured_messages[0] == "Hello!"
    assert captured_messages[1] is None
