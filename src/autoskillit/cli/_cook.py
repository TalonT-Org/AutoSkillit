"""cook command: interactive skill session launcher."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from collections.abc import Mapping
from pathlib import Path

from autoskillit.cli._terminal import terminal_guard


def _print_recipes_list() -> None:
    """Print available recipes grouped by category to stdout."""
    from autoskillit.recipe import list_recipes
    from autoskillit.recipe.io import _group_rank

    _GROUP_LABELS = {
        0: "Bundled Recipes",
        1: "Bundled Add-ons",
        2: "Family Recipes",
        3: "Experimental",
    }

    recipes = list_recipes(Path.cwd()).items
    if not recipes:
        print("No recipes found.")
        return

    name_w = max(len(r.name) for r in recipes)
    src_w = max(len(r.source) for r in recipes)
    current_rank = -1
    for r in recipes:
        rank = _group_rank(r)
        if rank != current_rank:
            current_rank = rank
            print(f"\n{_GROUP_LABELS[rank]}")
            print(f"{'NAME':<{name_w}}  {'SOURCE':<{src_w}}  DESCRIPTION")
            print(f"{'-' * name_w}  {'-' * src_w}  {'-' * 11}")
        print(f"{r.name:<{name_w}}  {r.source:<{src_w}}  {r.description}")


def _run_cook_session(
    *,
    cmd: list[str],
    env: Mapping[str, str],
    _first_run: bool,
    initial_prompt: str | None,
    project_dir: Path,
) -> str | None:
    """Run the cook subprocess; return session_id if a reload sentinel was written."""
    from autoskillit.cli._reload import consume_reload_sentinel

    with terminal_guard():
        result = subprocess.run(cmd, env=env)
    reload_session_id = consume_reload_sentinel(project_dir)
    if reload_session_id is not None:
        return reload_session_id
    if result.returncode == 0:
        if _first_run and initial_prompt is not None:
            from autoskillit.cli._onboarding import mark_onboarded

            mark_onboarded(project_dir)
    else:
        raise SystemExit(result.returncode)
    return None


def cook(*, resume: bool = False, session_id: str | None = None) -> None:
    """Launch Claude with all bundled AutoSkillit skills as slash commands."""
    from autoskillit.workspace import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
        resolve_ephemeral_root,
    )

    if not shutil.which("claude"):
        print("'claude' not found on PATH. Install Claude Code to use cook.")
        raise SystemExit(1)

    from autoskillit import __version__
    from autoskillit.cli._ansi import supports_color

    color = supports_color()
    _B = "\x1b[1m" if color else ""
    _C = "\x1b[96m" if color else ""
    _D = "\x1b[2m" if color else ""
    _G = "\x1b[32m" if color else ""
    _Y = "\x1b[33m" if color else ""
    _R = "\x1b[0m" if color else ""

    from autoskillit.config import iter_display_categories, load_config  # noqa: PLC0415

    config = load_config()

    print(f"{_B}{_C}AUTOSKILLIT {__version__}{_R} {_D}Kitchen open. All tools active.{_R}")
    skip = {"Telemetry & Diagnostics", "Kitchen"}
    for name, tools in iter_display_categories(
        config.features, experimental_enabled=config.experimental_enabled
    ):
        if name in skip:
            continue
        tool_list = f"{_D}, {_R}".join(f"{_G}{t}{_R}" for t in tools)
        print(f"  {_Y}{name:>20}{_R}  {tool_list}")
    print()

    from autoskillit.cli._ansi import permissions_warning
    from autoskillit.cli._timed_input import timed_prompt

    print(permissions_warning())
    confirm = timed_prompt(
        "\nLaunch session? [Enter/n]", default="", timeout=120, label="autoskillit cook"
    )
    if confirm.lower() in ("n", "no"):
        return

    from autoskillit.cli._init_helpers import _is_plugin_installed
    from autoskillit.cli._onboarding import is_first_run, run_onboarding_menu
    from autoskillit.core import (
        LAUNCH_ID_ENV_VAR,
        SESSION_TYPE_COOK,
        SESSION_TYPE_ENV_VAR,
        BareResume,
        NamedResume,
        NoResume,
        configure_logging,
        pkg_root,
        resume_spec_from_cli,
        write_registry_entry,
    )
    from autoskillit.execution import build_interactive_cmd

    configure_logging()

    resume_spec = resume_spec_from_cli(resume=resume, session_id=session_id)

    project_dir = Path.cwd()
    initial_prompt: str | None = None
    _first_run = is_first_run(project_dir)
    if _first_run:
        initial_prompt = run_onboarding_menu(project_dir, color=color)

    session_id_local = uuid.uuid4().hex[:16]
    write_registry_entry(project_dir, session_id_local, SESSION_TYPE_COOK, None)
    ephemeral_root = resolve_ephemeral_root()
    session_mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), ephemeral_root)
    session_mgr.cleanup_stale()
    skills_dir = session_mgr.init_session(
        session_id_local, cook_session=True, config=config, project_dir=project_dir
    )

    plugin_dir = None if _is_plugin_installed() else pkg_root()

    if isinstance(resume_spec, BareResume):
        from autoskillit.cli._session_picker import pick_session

        selected_id = pick_session(SESSION_TYPE_COOK, project_dir)
        if selected_id is not None:
            resume_spec = NamedResume(session_id=selected_id)
        else:
            resume_spec = NoResume()

    _cook_env_extras: dict[str, str] = {
        SESSION_TYPE_ENV_VAR: SESSION_TYPE_COOK,
        LAUNCH_ID_ENV_VAR: session_id_local,
    }

    current_resume_spec = resume_spec
    _current_first_run = _first_run
    _current_initial_prompt = initial_prompt

    _max_reloads = 10
    seen_reload_ids: set[str] = set()
    while True:
        spec = build_interactive_cmd(
            plugin_dir=plugin_dir,
            add_dirs=[skills_dir],
            initial_prompt=_current_initial_prompt,
            resume_spec=current_resume_spec,
            env_extras=_cook_env_extras,
        )
        reload_session_id = _run_cook_session(
            cmd=spec.cmd,
            env=spec.env,
            _first_run=_current_first_run,
            initial_prompt=_current_initial_prompt,
            project_dir=project_dir,
        )
        if reload_session_id is None:
            break
        if len(seen_reload_ids) >= _max_reloads:
            raise SystemExit(f"Too many reloads ({_max_reloads} max). Check for infinite loop.")
        if reload_session_id in seen_reload_ids:
            raise SystemExit(f"Repeated reload_id {reload_session_id!r} — aborting.")
        seen_reload_ids.add(reload_session_id)
        current_resume_spec = NamedResume(session_id=reload_session_id)
        _current_first_run = False
        _current_initial_prompt = None
