"""Marketplace and plugin management commands: install, upgrade."""

from __future__ import annotations

import importlib.metadata
import json
import os
from collections.abc import Mapping
from pathlib import Path

import regex as re

import autoskillit.cli._hooks as _hooks_mod
from autoskillit.cli._init_helpers import (
    _user_claude_json_path,
    evict_direct_mcp_entry,
    validate_public_plugin_projection,
)
from autoskillit.cli._install_contract import (
    InstallFailureKind,
    InstallMode,
    InstallOutcome,
    InstallRequest,
    InstallResult,
)
from autoskillit.cli._install_snapshot import _fetch_cache_path
from autoskillit.core import (
    MARKETPLACE_PREFIX,
    SkillExecutionRole,
    SkillSource,
    atomic_write,
    get_logger,
    is_git_worktree,
    pkg_root,
)
from autoskillit.workspace import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    SkillProjectionContext,
    materialize_sanitized_plugin_root,
    write_generated_hooks_json,
)

logger = get_logger(__name__)

_VALID_SCOPES = {"user", "project", "local"}
_MARKETPLACE_NAME = "autoskillit-local"


class _InstallFailed(Exception):
    """An operational install step failed; the caller must compensate."""

    def __init__(self, kind: InstallFailureKind, message: str) -> None:
        self.kind = kind
        super().__init__(message)


def _assert_not_worktree() -> None:
    """Refuse to install from a git worktree.

    Hoisted out of ``_ensure_marketplace`` into ``install()``'s preflight so it
    runs ahead of every persistent mutation *and* ahead of the ``CLAUDECODE``
    check. Order matters: a worktree install from inside a Claude Code session
    must report the worktree, not print "run these commands in a regular
    terminal" and return — the generic deferral text names the wrong problem.
    """
    pkg_dir = pkg_root()
    if is_git_worktree(pkg_dir):
        raise RuntimeError(
            "ERROR: 'autoskillit install' cannot be run when the package\n"
            "is installed from a git worktree.\n\n"
            f"  Detected worktree path: {pkg_dir}\n\n"
            "The marketplace projection would be sourced from this transient path.\n\n"
            "Fix: run 'autoskillit install' from the main project checkout:\n"
            "  cd /path/to/main/repo && autoskillit install"
        )


def _ensure_marketplace(
    *,
    cwd: Path | None = None,
    version: str | None = None,
) -> Path:
    """Create or update the local marketplace directory."""
    if version is None:
        from autoskillit import __version__

        version = __version__
    projection_cwd = Path.cwd().resolve() if cwd is None else Path(cwd)

    pkg_dir = pkg_root()
    marketplace_dir = Path.home() / ".autoskillit" / "marketplace"
    plugin_dir = marketplace_dir / ".claude-plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    # Write marketplace manifest
    manifest = {
        "name": _MARKETPLACE_NAME,
        "owner": {"name": "autoskillit"},
        "plugins": [
            {
                "name": "autoskillit",
                "source": "./plugins/autoskillit",
                "description": "Orchestrated skill-driven workflows"
                " using Claude Code headless sessions",
                "version": version,
            }
        ],
    }
    atomic_write(
        plugin_dir / "marketplace.json",
        json.JSONEncoder(indent=2).encode(manifest) + "\n",
    )

    public_plugin_root = marketplace_dir / "plugins" / "autoskillit"
    source_infos = tuple(
        skill for skill in DefaultSkillResolver().list_all() if skill.source is SkillSource.BUNDLED
    )
    catalog = EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(skill) for skill in source_infos),
        execution_role=SkillExecutionRole.SESSION,
    )
    private_manifest = materialize_sanitized_plugin_root(
        pkg_dir,
        public_plugin_root,
        catalog,
        SkillProjectionContext(
            cwd=projection_cwd,
            catalog=catalog,
        ),
        # Marketplace registration — Claude Code resolves these tools under the
        # marketplace prefix; never detect_autoskillit_mcp_prefix(), which answers
        # a different question (host-level registry presence, not load mechanism).
        mcp_tool_prefix=MARKETPLACE_PREFIX,
    )
    write_generated_hooks_json(public_plugin_root)
    validate_public_plugin_projection(
        pkg_dir,
        public_plugin_root,
        private_manifest,
        source_infos,
    )

    return marketplace_dir


def _ensure_workspace_ready(*, cwd: Path | None = None) -> None:
    """Repair project workspace state that install() is responsible for.

    Called after the CLAUDECODE guard — only when the actual install proceeds.
    Idempotent: safe to call on any project state.
    """
    from autoskillit.core import ensure_project_temp

    project_dir = Path.cwd() if cwd is None else Path(cwd)
    # Repair .autoskillit/.gitignore and ensure temp/ exists
    if (project_dir / ".autoskillit").is_dir():
        ensure_project_temp(project_dir)

    # Migrate legacy .autoskillit/scripts/ to .autoskillit/recipes/ if present
    if (project_dir / ".autoskillit" / "scripts").exists():
        try:
            upgrade(project_dir=project_dir)
        except OSError as exc:
            print(f"Warning: migration upgrade() failed (non-fatal): {exc}")


def _marketplace_manifest_path() -> Path:
    return Path.home() / ".autoskillit" / "marketplace" / ".claude-plugin" / "marketplace.json"


def _typed_result(
    outcome: InstallOutcome,
    *,
    failure_kind: InstallFailureKind | None = None,
    verified_identity: str | None = None,
    findings: tuple[str, ...] = (),
) -> InstallResult:
    return InstallResult(
        outcome=outcome,
        failure_kind=failure_kind,
        verified_identity=verified_identity,
        findings=findings,
    )


def _read_json_object(path: Path, *, purpose: str) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise _InstallFailed(
            InstallFailureKind.POSTCONDITION,
            f"Could not verify {purpose} at {path}: {exc}",
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _InstallFailed(
            InstallFailureKind.POSTCONDITION,
            f"Could not verify {purpose} at {path}: invalid JSON ({exc})",
        ) from exc
    if not isinstance(data, dict):
        raise _InstallFailed(
            InstallFailureKind.POSTCONDITION,
            f"Could not verify {purpose} at {path}: expected a JSON object",
        )
    return data


def _verify_cleanup(settings_path: Path, fetch_cache_path: Path) -> None:
    direct = _read_json_object(
        _user_claude_json_path(),
        purpose="direct MCP registration eviction",
    )
    servers = direct.get("mcpServers", {})
    if isinstance(servers, dict) and "autoskillit" in servers:
        raise _InstallFailed(
            InstallFailureKind.POSTCONDITION,
            "Stale direct MCP registration remains after eviction",
        )

    settings = _read_json_object(settings_path, purpose="Claude hook eviction")
    if _hooks_mod._find_autoskillit_hook_commands(settings):
        raise _InstallFailed(
            InstallFailureKind.POSTCONDITION,
            f"Stale AutoSkillit hook remains in {settings_path}",
        )
    try:
        fetch_cache_path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _InstallFailed(
            InstallFailureKind.POSTCONDITION,
            f"Could not verify fetch-cache invalidation: {exc}",
        ) from exc
    raise _InstallFailed(
        InstallFailureKind.POSTCONDITION,
        f"Fetch cache remains after invalidation: {fetch_cache_path}",
    )


def install(
    *,
    request: InstallRequest,
    child_env: Mapping[str, str] | None = None,
    child_cwd: Path | None = None,
) -> InstallResult:
    """Publish a generation-keyed plugin artifact as one typed transaction.

    The transaction stages a fresh generation from source, flips the
    atomic selector, reconciles retired shapes, and cleans up stale
    registrations. Publication never contends with readers (fresh-path
    staging), so ``InstallOutcome.DEFERRED`` for lease contention is no
    longer possible.
    """
    ambient_env = dict(os.environ)
    ambient_cwd = Path.cwd().resolve()
    from autoskillit import __version__

    install_request = request
    effective_scope = install_request.scope
    if (
        install_request.mode is InstallMode.MAINTENANCE_UPDATE
        and not install_request.require_registered_plugin
    ):
        return _typed_result(
            InstallOutcome.NOT_REQUIRED,
            findings=("Claude plugin publication is not required for this maintenance update",),
        )

    try:
        if effective_scope not in _VALID_SCOPES:
            raise RuntimeError(
                f"Invalid scope: {effective_scope!r}. Must be one of: "
                f"{', '.join(sorted(_VALID_SCOPES))}"
            )
        if install_request.mode is InstallMode.MAINTENANCE_UPDATE:
            if child_env is None or child_cwd is None:
                raise RuntimeError("Maintenance install requires a sealed child_env and child_cwd")
            operation_env = dict(child_env)
            operation_cwd = Path(child_cwd)
            if not operation_cwd.is_absolute():
                raise RuntimeError("Maintenance child_cwd must be absolute")
            expected_version = install_request.expected_version
            if expected_version is None:
                raise RuntimeError("Maintenance install requires expected_version")
            distribution_version = importlib.metadata.version("autoskillit")
            if distribution_version != expected_version:
                raise RuntimeError(
                    "Maintenance install expected distribution version "
                    f"{expected_version}, observed {distribution_version}"
                )
        else:
            operation_env = dict(ambient_env if child_env is None else child_env)
            operation_cwd = Path(ambient_cwd if child_cwd is None else child_cwd).resolve()
            expected_version = install_request.expected_version or __version__

        if not operation_cwd.is_dir():
            raise RuntimeError(f"Install child_cwd is not a directory: {operation_cwd}")
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in operation_env.items()
        ):
            raise RuntimeError("Install child_env must contain only string keys and values")

        _assert_not_worktree()
        if install_request.mode is InstallMode.DIRECT:
            from autoskillit.config import load_config
            from autoskillit.execution import get_backend

            cfg = load_config(operation_cwd)
            backend = get_backend(cfg.agent_backend.backend)
            if not backend.capabilities.plugin_install_capable:
                return _typed_result(
                    InstallOutcome.DECLINED,
                    findings=(
                        "Plugin install requires a plugin_install_capable backend; "
                        f"current backend is {cfg.agent_backend.backend!r}",
                    ),
                )

        plugin_ref = f"autoskillit@{_MARKETPLACE_NAME}"
        if operation_env.get("CLAUDECODE"):
            return _typed_result(
                InstallOutcome.DEFERRED,
                findings=("Run the plugin generation publication in a regular terminal",),
            )
    except (OSError, RuntimeError, ValueError, importlib.metadata.PackageNotFoundError) as exc:
        return _typed_result(
            InstallOutcome.FAILED,
            failure_kind=InstallFailureKind.PREFLIGHT,
            findings=(f"preflight failure: {exc}",),
        )

    from autoskillit.cli._plugin_artifact import installed_plugin_semantic_key
    from autoskillit.core import _InstallLock
    from autoskillit.workspace import publish_generation, reconcile_install_artifacts

    settings_path = _hooks_mod._claude_settings_path(
        effective_scope,
        cwd=operation_cwd,
    )
    try:
        with _InstallLock():
            try:
                for repaired in reconcile_install_artifacts():
                    print(f"Repaired legacy install artifact: ~/{repaired}")

                # Stage and publish the marketplace projection for metadata
                _ensure_marketplace(
                    cwd=operation_cwd,
                    version=expected_version,
                )
                if install_request.mode is InstallMode.DIRECT:
                    _ensure_workspace_ready(cwd=operation_cwd)

                # Build the source root for the generation
                marketplace_plugin_root = (
                    Path.home() / ".autoskillit" / "marketplace" / "plugins" / "autoskillit"
                )

                semantic_key = installed_plugin_semantic_key(
                    plugin_ref,
                    expected_version,
                )

                try:
                    identity = publish_generation(
                        home=Path.home(),
                        plugin_ref=plugin_ref,
                        version=expected_version,
                        semantic_key=semantic_key,
                        source_root=marketplace_plugin_root,
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    raise _InstallFailed(
                        InstallFailureKind.POSTCONDITION,
                        f"Failed to publish plugin generation: {exc}",
                    ) from exc

                verified_identity = identity.semantic_key

                # Clean up stale registrations
                if evict_direct_mcp_entry(_user_claude_json_path()):
                    print("Removed stale direct MCP entry from ~/.claude.json")
                _hooks_mod._evict_stale_autoskillit_hooks(settings_path)
                from autoskillit.cli.update._update_checks import invalidate_fetch_cache

                invalidate_fetch_cache(Path.home())
                _verify_cleanup(settings_path, _fetch_cache_path(Path.home()))

            except _InstallFailed as exc:
                return _typed_result(
                    InstallOutcome.FAILED,
                    failure_kind=exc.kind,
                    findings=(f"{exc.kind.value} failure: {exc}",),
                )
            except BaseException as exc:
                logger.warning(
                    "install_transaction_unexpected_failure",
                    failure=str(exc),
                    exc_info=True,
                )
                if not isinstance(exc, Exception):
                    raise
                return _typed_result(
                    InstallOutcome.FAILED,
                    failure_kind=InstallFailureKind.POSTCONDITION,
                    findings=(f"Install transaction failed: {exc}",),
                )
    except (OSError, RuntimeError, ValueError) as exc:
        return _typed_result(
            InstallOutcome.FAILED,
            failure_kind=InstallFailureKind.PREFLIGHT,
            findings=(f"install lock failure: {exc}",),
        )

    success_message = f"Plugin published: {plugin_ref} (scope: {effective_scope})"
    return _typed_result(
        InstallOutcome.COMPLETED,
        verified_identity=verified_identity,
        findings=(success_message,),
    )


def upgrade(*, project_dir: Path | None = None):
    """Migrate a project from .autoskillit/scripts/ to .autoskillit/recipes/.

    Renames the directory and rewrites YAML top-level keys:
      inputs: -> ingredients:
      constraints: -> kitchen_rules:

    Idempotent: safe to run multiple times.
    """
    project_dir = Path.cwd() if project_dir is None else Path(project_dir)
    scripts_dir = project_dir / ".autoskillit" / "scripts"
    recipes_dir = project_dir / ".autoskillit" / "recipes"

    if not scripts_dir.exists():
        print("Nothing to do — .autoskillit/scripts/ not found.")
        return

    if recipes_dir.exists():
        print("Nothing to do — .autoskillit/recipes/ already present.")
        return

    scripts_dir.rename(recipes_dir)

    changed = 0
    for yaml_file in sorted(recipes_dir.rglob("*.yaml")):
        text = yaml_file.read_text()
        new_text = re.sub(r"^inputs:", "ingredients:", text, flags=re.MULTILINE)
        new_text = re.sub(r"^constraints:", "kitchen_rules:", new_text, flags=re.MULTILINE)
        if new_text != text:
            atomic_write(yaml_file, new_text)
            changed += 1

    print(f"Upgraded: directory renamed, {changed} file(s) updated.")
