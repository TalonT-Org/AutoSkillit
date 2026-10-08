"""Keep a headless orchestrator food truck on one kitchen identity."""

from __future__ import annotations

import sys
from pathlib import Path

import anyio
import pytest
from fastmcp.client import Client
from fastmcp.client.transports import StdioTransport

from autoskillit.core import (
    CAMPAIGN_ID_ENV_VAR,
    DISPATCH_ID_ENV_VAR,
    FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
    HEADLESS_ENV_VAR,
    SESSION_TYPE_ENV_VAR,
    SESSION_TYPE_ORCHESTRATOR,
    load_yaml,
)
from tests.conftest import production_interpreter_env
from tests.integration.test_codex_mcp_tracker_dispatch_identity import (
    _tool_json,
    _write_project_config,
)

pytestmark = [
    pytest.mark.layer("integration"),
    pytest.mark.integration,
    pytest.mark.medium,
    pytest.mark.anyio,
    pytest.mark.skipif(sys.platform != "linux", reason="Linux-only: stdio MCP subprocess"),
]


@pytest.mark.parametrize(
    "campaign_id",
    ["", "campaign-5250"],
    ids=["empty-campaign", "campaign-id"],
)
async def test_headless_food_truck_reuses_its_kitchen_identity(
    tmp_path: Path,
    campaign_id: str,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _write_project_config(project_dir)
    recipe_dir = project_dir / ".autoskillit" / "recipes"
    recipe_dir.mkdir()
    (recipe_dir / "kitchen-identity-probe.yaml").write_text(
        """\
name: kitchen-identity-probe
description: Expose the session kitchen identity in rendered recipe content.
recipe_version: "1.0.0"
kitchen_rules:
  - Use MCP tools only.
ingredients:
  source_dir:
    description: Project directory
    required: true
  kitchen_id:
    description: Session kitchen identity
    default: ""
    hidden: true
steps:
  observe_identity:
    tool: run_cmd
    with:
      cmd: echo ${{ inputs.kitchen_id }}
      cwd: ${{ inputs.source_dir }}
      step_name: observe_identity
    on_success: done
    on_failure: done
  done:
    action: stop
    message: 'Emit the L3 result sentinel JSON block with success=true: {"success": true}'
""",
        encoding="utf-8",
    )

    env = {
        **production_interpreter_env(),
        HEADLESS_ENV_VAR: "1",
        SESSION_TYPE_ENV_VAR: SESSION_TYPE_ORCHESTRATOR,
        FOOD_TRUCK_TOOL_TAGS_ENV_VAR: "kitchen-core",
        DISPATCH_ID_ENV_VAR: "disp-5250",
        CAMPAIGN_ID_ENV_VAR: campaign_id,
        "AUTOSKILLIT_PROJECT_DIR": str(project_dir),
    }
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "autoskillit"],
        env=env,
        cwd=str(project_dir),
        keep_alive=False,
    )

    with anyio.fail_after(45):
        async with Client(transport) as client:
            open_payload = _tool_json(
                await client.call_tool(
                    "open_kitchen",
                    {
                        "name": "kitchen-identity-probe",
                        "overrides": {"source_dir": str(project_dir)},
                    },
                )
            )
            assert open_payload["success"] is True, open_payload
            assert open_payload["valid"] is True, open_payload
            assert open_payload["phase"] == "committed", open_payload
            opened_recipe = load_yaml(str(open_payload["content"]))
            opened_kitchen_id = opened_recipe["steps"]["observe_identity"]["with"][
                "cmd"
            ].removeprefix("echo ")
            assert opened_kitchen_id
            assert "${{" not in opened_kitchen_id
            if campaign_id:
                assert opened_kitchen_id == campaign_id

            load_payload = _tool_json(
                await client.call_tool(
                    "load_recipe",
                    {
                        "name": "kitchen-identity-probe",
                        "overrides": {"source_dir": str(project_dir)},
                    },
                )
            )
            assert load_payload["success"] is True, load_payload
            loaded_recipe = load_yaml(str(load_payload["content"]))
            assert (
                loaded_recipe["steps"]["observe_identity"]["with"]["cmd"]
                == f"echo {opened_kitchen_id}"
            )
