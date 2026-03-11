"""chefs-hat command: ephemeral skill session launcher."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid


def chefs_hat() -> None:
    """Launch Claude with all bundled AutoSkillit skills as slash commands."""
    from autoskillit.workspace import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
        resolve_ephemeral_root,
    )

    if not shutil.which("claude"):
        print("'claude' not found on PATH. Install Claude Code to use chefs-hat.")
        raise SystemExit(1)

    from autoskillit import __version__
    from autoskillit.server import _build_tool_listing

    print(f"AutoSkillit {__version__} — Kitchen open. All tools active.")
    print(_build_tool_listing())
    print()

    session_id = uuid.uuid4().hex[:16]
    ephemeral_root = resolve_ephemeral_root()
    session_mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), ephemeral_root)
    skills_dir = session_mgr.init_session(session_id, cook_session=True)

    env = {**os.environ}
    try:
        result = subprocess.run(["claude", "--add-dir", str(skills_dir)], env=env)
        if result.returncode != 0:
            raise SystemExit(result.returncode)
    finally:
        shutil.rmtree(skills_dir, ignore_errors=True)
