"""CLI package for autoskillit.

Re-exports all public symbols from cli/app.py so that
``from autoskillit import cli; cli.serve()`` and
``patch.object(cli, 'serve', ...)`` continue to work.
"""

import shutil  # noqa: F401 — tests patch autoskillit.cli.shutil.which
import subprocess  # noqa: F401 — tests patch autoskillit.cli.subprocess.run
from pathlib import Path  # noqa: F401 — tests patch autoskillit.cli.Path.home

from autoskillit.cli.app import (
    _build_orchestrator_prompt,
    _claude_settings_path,
    _ensure_marketplace,
    _generate_config_yaml,
    _print_next_steps,
    _prompt_recipe_choice,
    _prompt_test_command,
    app,
    config_app,
    config_show,
    cook,
    doctor,
    init,
    install,
    main,
    migrate,
    quota_status,
    recipes_app,
    recipes_list,
    recipes_show,
    serve,
    skills_app,
    skills_list,
    upgrade,
    workspace_app,
    workspace_clean,
    workspace_init,
)

__all__ = [
    "_build_orchestrator_prompt",
    "_claude_settings_path",
    "_ensure_marketplace",
    "_generate_config_yaml",
    "_print_next_steps",
    "_prompt_recipe_choice",
    "_prompt_test_command",
    "app",
    "config_app",
    "config_show",
    "cook",
    "doctor",
    "init",
    "install",
    "main",
    "migrate",
    "quota_status",
    "recipes_app",
    "recipes_list",
    "recipes_show",
    "serve",
    "skills_app",
    "skills_list",
    "upgrade",
    "workspace_app",
    "workspace_clean",
    "workspace_init",
]
