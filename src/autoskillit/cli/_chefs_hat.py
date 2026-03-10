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

    session_id = uuid.uuid4().hex[:16]
    ephemeral_root = resolve_ephemeral_root()
    session_mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), ephemeral_root)
    skills_dir = session_mgr.init_session(session_id, cook_session=True)

    env = {**os.environ, "AUTOSKILLIT_KITCHEN_OPEN": "1"}
    subprocess.run(["claude", "--add-dir", str(skills_dir)], env=env)
