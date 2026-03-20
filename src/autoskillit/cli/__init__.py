"""CLI package for autoskillit.

Re-exports all public symbols so that
``from autoskillit import cli; cli.serve()`` and
``patch.object(cli, 'serve', ...)`` continue to work.
"""

import shutil  # noqa: F401 — tests patch autoskillit.cli.shutil.which
import subprocess  # noqa: F401 — tests patch autoskillit.cli.subprocess.run
from pathlib import Path  # noqa: F401 — tests patch autoskillit.cli.Path.home

from autoskillit.cli._cook import cook
from autoskillit.cli._hooks import _claude_settings_path
from autoskillit.cli._init_helpers import _prompt_recipe_choice
from autoskillit.cli._prompts import (
    _OPEN_KITCHEN_CHOICE,
    _build_open_kitchen_prompt,
    _build_orchestrator_prompt,
    _resolve_recipe_input,
)
from autoskillit.cli.app import (
    _generate_config_yaml,
    _prompt_test_command,
    app,
    config_app,
    config_show,
    doctor,
    init,
    install,
    main,
    migrate,
    order,
    quota_status,
    recipes_app,
    recipes_list,
    recipes_render,
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
    "_OPEN_KITCHEN_CHOICE",
    "_build_open_kitchen_prompt",
    "_build_orchestrator_prompt",
    "_claude_settings_path",
    "_generate_config_yaml",
    "_prompt_recipe_choice",
    "_prompt_test_command",
    "_resolve_recipe_input",
    "app",
    "config_app",
    "config_show",
    "cook",
    "doctor",
    "init",
    "install",
    "main",
    "migrate",
    "order",
    "quota_status",
    "recipes_app",
    "recipes_list",
    "recipes_render",
    "recipes_show",
    "serve",
    "skills_app",
    "skills_list",
    "upgrade",
    "workspace_app",
    "workspace_clean",
    "workspace_init",
]
