"""CLI package for autoskillit.

Re-exports all public symbols so that
``from autoskillit import cli; cli.serve()`` and
``patch.object(cli, 'serve', ...)`` continue to work.
"""

import shutil  # noqa: F401 — tests patch autoskillit.cli.shutil.which
import subprocess  # noqa: F401 — tests patch autoskillit.cli.subprocess.run
from pathlib import Path  # noqa: F401 — tests patch autoskillit.cli.Path.home

from autoskillit.cli._hooks import _claude_settings_path
from autoskillit.cli._init_helpers import _prompt_recipe_choice
from autoskillit.cli._mcp_names import detect_autoskillit_mcp_prefix
from autoskillit.cli._prompts import (
    _build_open_kitchen_prompt,
    _build_orchestrator_prompt,
)
from autoskillit.cli.app import (
    _generate_config_yaml,
    _prompt_test_command,
    app,
    config_app,
    config_show,
    fleet_app,
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
    update,
    upgrade,
    workspace_app,
    workspace_clean,
    workspace_init,
)
from autoskillit.cli.app import (
    doctor as _doctor_cmd,
)
from autoskillit.cli.doctor import DoctorResult
from autoskillit.cli.fleet import fleet_campaign, fleet_dispatch, fleet_list, fleet_status
from autoskillit.cli.session._cook import cook
from autoskillit.hook_registry import HookDriftResult

doctor_cmd = _doctor_cmd

__all__ = [
    "_build_open_kitchen_prompt",
    "_build_orchestrator_prompt",
    "DoctorResult",
    "HookDriftResult",
    "_claude_settings_path",
    "_generate_config_yaml",
    "_prompt_recipe_choice",
    "_prompt_test_command",
    "app",
    "config_app",
    "config_show",
    "cook",
    "detect_autoskillit_mcp_prefix",
    "doctor_cmd",
    "fleet_app",
    "fleet_campaign",
    "fleet_dispatch",
    "fleet_list",
    "fleet_status",
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
    "update",
    "upgrade",
    "workspace_app",
    "workspace_clean",
    "workspace_init",
]
