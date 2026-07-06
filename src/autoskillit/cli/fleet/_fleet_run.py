"""Headless one-shot fleet dispatch command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, NoReturn

from cyclopts import Parameter

from autoskillit.core import get_logger, is_feature_enabled

logger = get_logger(__name__)


def _fleet_run_error(error: str, message: str, exit_code: int = 1) -> NoReturn:
    envelope = {"success": False, "error": error, "user_visible_message": message}
    print(json.dumps(envelope))
    raise SystemExit(exit_code)


def fleet_run(
    recipe: str,
    *,
    task: Annotated[str, Parameter(name=["--task", "-t"])] = "",
    ingredient: Annotated[tuple[str, ...], Parameter(name=["--ingredient", "-i"])] = (),
    backend: Annotated[str | None, Parameter(name=["--backend"])] = None,
    timeout_sec: Annotated[int | None, Parameter(name=["--timeout-sec"])] = None,
    resume_session_id: Annotated[str | None, Parameter(name=["--resume-session-id"])] = None,
    prior_dispatch_id: Annotated[str | None, Parameter(name=["--prior-dispatch-id"])] = None,
    disable_quota_guard: Annotated[bool, Parameter(name=["--disable-quota-guard"])] = False,
) -> None:
    """One-shot headless recipe dispatch (experimental).

    Dispatches a single recipe run non-interactively. Prints the dispatch
    result envelope as JSON on stdout. Exit 0 on SUCCESS, nonzero otherwise.
    """
    # --- Session-type guard (retained for headless — prevents recursive dispatch) ---
    if os.environ.get("AUTOSKILLIT_SESSION_TYPE") in ("skill", "leaf"):
        _fleet_run_error(
            "FLEET_SESSION_TYPE_BLOCKED",
            "'fleet run' cannot run inside a skill or leaf session.",
        )
    # NOTE: CLAUDECODE guard intentionally NOT applied — REQ-HFD-006

    # --- Config + feature gates ---
    from autoskillit.config import load_config

    try:
        cfg = load_config(Path.cwd())
    except Exception as exc:
        logger.error("fleet run: failed to load config", exc_info=True)
        _fleet_run_error("FLEET_CONFIG_ERROR", f"Failed to load config: {exc}")

    # Fleet base feature check (equivalent to _require_fleet but with JSON output)
    if not is_feature_enabled(
        "fleet", cfg.features, experimental_enabled=cfg.experimental_enabled
    ):
        _fleet_run_error(
            "FLEET_FEATURE_DISABLED",
            "The 'fleet' feature is not enabled.\n"
            "Enable with: features.experimental_enabled: true in your config\n"
            "Or set: AUTOSKILLIT_FEATURES__FLEET=true",
        )

    # Headless run feature check
    if not is_feature_enabled(
        "fleet_headless_run",
        cfg.features,
        experimental_enabled=cfg.experimental_enabled,
    ):
        _fleet_run_error(
            "FLEET_FEATURE_DISABLED",
            "The 'fleet_headless_run' feature is not enabled.\n"
            "Enable with: features.experimental_enabled: true\n"
            "Or: features.fleet_headless_run: true\n"
            "Or: AUTOSKILLIT_FEATURES__FLEET_HEADLESS_RUN=true",
        )

    # --- Part B implements the dispatch body here ---
    _fleet_run_error(
        "FLEET_NOT_IMPLEMENTED",
        "'fleet run' dispatch is not yet implemented. See Part B.",
    )
