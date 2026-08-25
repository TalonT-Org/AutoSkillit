"""Doctor command implementation — project setup checks."""

from __future__ import annotations

import functools
from pathlib import Path

from autoskillit.cli._hooks import _claude_settings_path
from autoskillit.core import Severity, get_logger, is_feature_enabled
from autoskillit.execution import get_backend

from ._doctor_capture_store import _check_capture_store_stats
from ._doctor_config import (
    _check_config_layers_for_secrets,
    _check_gitignore_completeness,
    _check_local_recipe_validity,
    _check_project_config,
    _check_secret_scanning_hook,
    _check_standing_backend_pins_feasibility,
    _load_config_guarded,
)
from ._doctor_env import (
    _check_ambient_campaign_id,
    _check_ambient_session_type_fleet,
    _check_ambient_session_type_orchestrator,
    _check_ambient_session_type_skill,
)
from ._doctor_features import (
    _check_feature_dependencies,
    _check_feature_registry_consistency,
)
from ._doctor_fleet import (
    _check_campaign_manifest_clone_dests,
    _check_campaign_onboarding_hint,
    _check_fleet_dispatch_guard_registered,
    _check_fleet_state_schema,
    _check_script_version_health,
    _check_sous_chef_bundled,
    _check_stale_fleet_state,
)
from ._doctor_hooks import (
    _check_dual_registration,
    _check_hook_health_all_scopes,
    _check_hook_registration,
    _check_hook_registry_drift_all_scopes,
)
from ._doctor_install import (
    _check_autoskillit_on_path,
    _check_editable_install_source_exists,
    _check_install_classification,
    _check_publication_obligation,
    _check_source_version_drift,
    _check_stale_entry_points,
    _check_update_dismissal_state,
)
from ._doctor_mcp import (
    _check_claude_mcp_timeouts,
    _check_codex_mcp_timeouts,
    _check_dual_mcp_registration,
    _check_install_state_consistency,
    _check_mcp_server_registered,
    _check_plugin_cache_exists,
    _check_plugin_cache_integrity,
    _check_stale_mcp_servers,
)
from ._doctor_repair import collect_retiring_cache_repair_results
from ._doctor_runtime import (
    _check_backend_version,
    _check_claude_binary,
    _check_claude_process_state_breakdown,
    _check_cli_conformance_probes,
    _check_codex_graduation,
    _check_codex_limits_verified,
    _check_codex_model_alias_staleness,
    _check_codex_ndjson_drift,
    _check_orphaned_autoskillit_daemons,
    _check_orphaned_codex_processes,
    _check_orphaned_process_tethers,
    _check_quota_cache_schema,
    _check_script_binary,
    _check_session_index_projection,
)
from ._doctor_skills import (
    _check_project_local_skill_contracts,
    _check_skill_capability_authenticity,
)
from ._doctor_types import _NON_PROBLEM as _NON_PROBLEM
from ._doctor_types import DoctorResult, _print_doctor_results, _run_check

logger = get_logger(__name__)

__all__ = ["DoctorResult", "Severity", "run_doctor", "run_doctor_repairs"]


def _collect_doctor_results() -> list[DoctorResult]:
    """Collect diagnostic results without owning output or performing repairs."""
    cfg, results = _load_config_guarded(Path.cwd())
    if cfg.agent_backend.backend:
        try:
            _backend = get_backend(cfg.agent_backend.backend)
        except ValueError:
            logger.warning("unknown_backend_fallback", backend=cfg.agent_backend.backend)
            _backend = None
    else:
        _backend = None
    results.extend(
        _run_check(
            lambda: _check_stale_mcp_servers(Path.home() / ".claude.json", backend=_backend),
            check_name="stale_mcp_servers",
        )
    )
    results.extend(
        _run_check(
            lambda: _check_mcp_server_registered(
                claude_json_path=Path.home() / ".claude.json",
                backend=_backend,
            ),
            check_name="mcp_server_registered",
        )
    )
    results.extend(_run_check(functools.partial(_check_dual_mcp_registration)))
    results.extend(_run_check(functools.partial(_check_plugin_cache_exists)))
    results.extend(_run_check(functools.partial(_check_plugin_cache_integrity)))
    results.extend(_run_check(functools.partial(_check_install_state_consistency)))
    results.extend(_run_check(functools.partial(_check_autoskillit_on_path)))
    results.extend(_run_check(functools.partial(_check_project_config)))
    results.extend(_run_check(functools.partial(_check_config_layers_for_secrets)))
    results.extend(
        _run_check(
            lambda: _check_hook_health_all_scopes(Path.cwd()),
            check_name="hook_health_all_scopes",
        )
    )
    results.extend(
        _run_check(
            lambda: _check_hook_registration(_claude_settings_path("user", cwd=Path.cwd())),
            check_name="hook_registration",
        )
    )
    results.extend(
        _run_check(
            lambda: _check_hook_registry_drift_all_scopes(Path.cwd()),
            check_name="hook_registry_drift_all_scopes",
        )
    )
    results.extend(
        _run_check(
            lambda: _check_dual_registration(_claude_settings_path("user", cwd=Path.cwd())),
            check_name="dual_registration",
        )
    )
    results.extend(_run_check(functools.partial(_check_script_version_health)))
    results.extend(
        _run_check(
            lambda: _check_gitignore_completeness(Path.cwd()),
            check_name="gitignore_completeness",
        )
    )
    results.extend(
        _run_check(
            lambda: _check_secret_scanning_hook(Path.cwd()),
            check_name="secret_scanning_hook",
        )
    )
    results.extend(_run_check(functools.partial(_check_editable_install_source_exists)))
    results.extend(_run_check(functools.partial(_check_stale_entry_points)))
    results.extend(_run_check(functools.partial(_check_source_version_drift)))
    results.extend(_run_check(functools.partial(_check_quota_cache_schema)))
    results.extend(
        _run_check(functools.partial(_check_claude_process_state_breakdown, backend=_backend))
    )
    results.extend(_run_check(functools.partial(_check_install_classification)))
    results.extend(_run_check(functools.partial(_check_update_dismissal_state)))
    results.extend(_run_check(functools.partial(_check_publication_obligation)))
    results.extend(_run_check(functools.partial(_check_ambient_session_type_skill)))
    results.extend(_run_check(functools.partial(_check_ambient_session_type_orchestrator)))
    results.extend(_run_check(functools.partial(_check_ambient_session_type_fleet)))
    results.extend(_run_check(functools.partial(_check_ambient_campaign_id)))
    results.extend(
        _run_check(
            lambda: _check_feature_dependencies(cfg.features),
            check_name="feature_dependencies",
        )
    )
    results.extend(_run_check(functools.partial(_check_feature_registry_consistency)))
    if is_feature_enabled("fleet", cfg.features, experimental_enabled=cfg.experimental_enabled):
        results.extend(_run_check(functools.partial(_check_sous_chef_bundled)))
        results.extend(_run_check(functools.partial(_check_fleet_dispatch_guard_registered)))
        results.extend(_run_check(functools.partial(_check_stale_fleet_state)))
        results.extend(_run_check(functools.partial(_check_campaign_onboarding_hint)))
        results.extend(_run_check(functools.partial(_check_campaign_manifest_clone_dests)))
        results.extend(_run_check(functools.partial(_check_fleet_state_schema)))
    results.extend(_run_check(functools.partial(_check_backend_version, backend=_backend)))
    results.extend(_run_check(functools.partial(_check_script_binary)))
    results.extend(_run_check(functools.partial(_check_claude_binary)))
    results.extend(
        _run_check(
            functools.partial(
                _check_codex_mcp_timeouts,
                backend=_backend,
                run_skill=cfg.run_skill,
                fleet=cfg.fleet,
            )
        )
    )
    results.extend(
        _run_check(
            functools.partial(
                _check_claude_mcp_timeouts,
                backend=_backend,
                run_skill=cfg.run_skill,
                fleet=cfg.fleet,
            )
        )
    )
    results.extend(_run_check(functools.partial(_check_codex_graduation, backend=_backend)))
    results.extend(_run_check(functools.partial(_check_cli_conformance_probes, backend=_backend)))
    results.extend(
        _run_check(
            functools.partial(
                _check_codex_ndjson_drift, log_dir=cfg.linux_tracing.log_dir, backend=_backend
            )
        )
    )
    results.extend(_run_check(functools.partial(_check_codex_model_alias_staleness)))
    results.extend(_run_check(functools.partial(_check_standing_backend_pins_feasibility)))
    results.extend(_run_check(functools.partial(_check_local_recipe_validity)))
    results.extend(_run_check(functools.partial(_check_codex_limits_verified, backend=_backend)))
    results.extend(_run_check(functools.partial(_check_skill_capability_authenticity)))
    results.extend(_run_check(functools.partial(_check_capture_store_stats)))
    results.extend(_run_check(functools.partial(_check_project_local_skill_contracts)))
    results.extend(
        _run_check(
            functools.partial(_check_session_index_projection, log_dir=cfg.linux_tracing.log_dir)
        )
    )
    results.extend(_run_check(functools.partial(_check_orphaned_codex_processes)))
    results.extend(_run_check(functools.partial(_check_orphaned_autoskillit_daemons)))
    results.extend(_run_check(functools.partial(_check_orphaned_process_tethers)))
    return results


def run_doctor(*, output_json: bool = False) -> None:
    """Run the read-only diagnostic entry point."""
    _print_doctor_results(_collect_doctor_results(), output_json=output_json)


def run_doctor_repairs(*, output_json: bool = False) -> None:
    """Run the opt-in safe repair action, then report post-repair diagnostics."""
    results = _collect_doctor_results()
    results.extend(collect_retiring_cache_repair_results())
    _print_doctor_results(
        results,
        output_json=output_json,
        include_info=True,
    )
