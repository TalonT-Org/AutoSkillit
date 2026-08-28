"""Durable-artifact writer registry — forces every function that writes an
artifact with a lifetime exceeding the writing process under a relocatability
or machine-local-detection obligation.

Issue #4735: extracted from ``_type_constants.py`` to keep the facade under
the enforced 750-line budget (``test_warning_zone_files_under_750_lines``).
The ``repair_corrupt_retiring_cache`` entry (added in #4723) is preserved at
the head of ``DURABLE_ARTIFACT_WRITERS`` — dropping it would re-introduce the
brick-after-leak failure mode fixed by that PR.

Import-time validation rejects duplicate writers and machine-local entries
that omit their required staleness detector. See
``tests/contracts/test_durable_artifact_relocatability.py`` for the per-writer
contracts.

``Detection`` is ``str | None`` (``module:qualname``), not ``Callable`` — that
is verified by the type annotations here and the import-side contract test
``tests/contracts/test_durable_artifact_relocatability.py`` (which imports
the validator directly from this module).
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = ["DurableArtifactWriterDef", "DURABLE_ARTIFACT_WRITERS"]


class DurableArtifactWriterDef(NamedTuple):
    """Static definition of a function that writes durable artifacts.

    ``writer``: ``module:qualname`` of the writing function — specifically the
        function whose body contains the actual persistence call (``atomic_write``,
        ``write_versioned_json``, …), not an outer public wrapper that merely
        delegates to it.  This is what lets the AST-based completeness guard
        (``tests/arch/test_durable_artifact_writers_guard.py``) match registry
        entries against real call sites without call-graph resolution.
    ``artifact``: human-readable description of the destination, including the
        public entry point a reader would recognize (e.g. "via sync_hooks_to_settings()").
    ``machine_local``: True when the artifact legitimately bakes absolute host
        paths (e.g. settings.json / config.toml — neither Claude Code's settings
        file nor Codex's config.toml expands a relocatable ``${CLAUDE_PLUGIN_ROOT}``-
        style token, unlike hooks.json).  Machine-local writers must declare a
        ``detection`` callable that detects staleness at startup.
    ``detection``: ``module:qualname`` of the staleness-detection callable;
        required when ``machine_local=True``, enforced by the import-time assertion.

    See ``tests/contracts/test_durable_artifact_relocatability.py`` (T-C2) for
    per-writer resolvability/relocatability contracts.
    """

    writer: str
    artifact: str
    machine_local: bool
    detection: str | None


def _validate_durable_artifact_writer_defs(
    writers: tuple[DurableArtifactWriterDef, ...],
) -> None:
    missing_detection = [
        writer.writer for writer in writers if writer.machine_local and not writer.detection
    ]
    if missing_detection:
        raise AssertionError(
            "Every machine_local DurableArtifactWriterDef must have a detection callable. "
            f"Missing: {missing_detection}"
        )
    writer_names = [writer.writer for writer in writers]
    if len(writer_names) != len(set(writer_names)):
        raise AssertionError("DURABLE_ARTIFACT_WRITERS contains duplicate writer strings")


DURABLE_ARTIFACT_WRITERS: tuple[DurableArtifactWriterDef, ...] = (
    DurableArtifactWriterDef(
        writer="autoskillit.core._retiring_cache:_write_retiring_cache_unlocked",
        artifact=(
            "retiring_cache.json — v2 retiring-cache state under the retiring-cache "
            "lock; serialized by every retirement-v2 mutation (cancel, append, "
            "promote-legacy-evidence, repair-rebuild)"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.core._retiring_cache:repair_corrupt_retiring_cache",
        artifact=(
            "immutable retiring_cache.corrupt-<timestamp>.json forensic sidecar; "
            "the original machine-local bytes are preserved for diagnosis and are "
            "never consumed as relocated configuration"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.core._active_kitchens:register_active_kitchen",
        artifact=(
            "active_kitchens.json — append-only kitchen registry under the "
            "active-kitchens lock; one writer per kitchen open/transition"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.core._active_kitchens:unregister_active_kitchen",
        artifact=(
            "active_kitchens.json — kitchen registry survivor-list rewrite under "
            "the active-kitchens lock; one writer per kitchen close/transition"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.workspace._projected_artifact._publication:write_generated_hooks_json",
        artifact=(
            "hooks/hooks.json in plugin/projection roots — relocatable "
            "${CLAUDE_PLUGIN_ROOT}-form commands; written during projection staging, "
            "marketplace publication, and self-heal republish"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.server._lifespan:run_startup_drift_check",
        artifact=(
            "dev-checkout pkg_root()/hooks/hooks.json — self-healed at MCP server "
            "startup when on-disk bytes drift from render_hooks_json_text()"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.hooks._session_binding:write_binding",
        artifact=(
            "skill_guard_<session_id>.flag — the session-binding channel between "
            "the skill-load hook and the join consumers"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.hooks._join_ledger:write_join_ledger",
        artifact=(
            "join_ledger.json — immutable declared-batch records and the "
            "declaration-key index, serialized under the sibling flock"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer=(
            "autoskillit.workspace._projected_artifact._hook_repair:"
            "repair_broken_plugin_cache_hooks"
        ),
        artifact="repaired hooks/hooks.json in an installed plugin-cache incarnation",
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer=(
            "autoskillit.workspace._projected_artifact._hook_repair:repair_broken_projection_hooks"
        ),
        artifact="repaired hooks/hooks.json + manifest digest in a plugin-projections incarnation",
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.workspace._projected_artifact._hook_repair:_rollback_repair",
        artifact=(
            "rollback restoration of original hooks.json/manifest.json bytes after a "
            "failed hook-repair transaction"
        ),
        machine_local=True,
        detection="autoskillit.hook_registry:find_broken_hook_scripts",
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.cli._hooks:_write_settings_data",
        artifact=(
            "~/.claude/settings.json hook entries (absolute host paths) — via "
            "sync_hooks_to_settings() / _evict_stale_autoskillit_hooks()"
        ),
        machine_local=True,
        detection="autoskillit.hook_registry:find_broken_hook_scripts",
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.execution.backends._codex_hooks:_upsert_hooks_text",
        artifact=(
            "~/.codex/config.toml [[hooks]] blocks (absolute host paths) — the "
            "foreign-block-preserving text rewrite used by sync_hooks_to_codex_config()"
        ),
        machine_local=True,
        detection="autoskillit.execution.backends._codex_hooks:find_broken_codex_hook_commands",
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.execution.backends._codex_config:_write_codex_config",
        artifact=(
            "~/.codex/config.toml — generic TOML writer; persists "
            "sync_hooks_to_codex_config()'s merged hook entries (absolute host paths) "
            "as well as MCP server registration"
        ),
        machine_local=True,
        detection="autoskillit.execution.backends._codex_hooks:find_broken_codex_hook_commands",
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.execution.backends._codex_fs_atomic:_write_reconciliation_audit",
        artifact=(
            "immutable operator authorization records for explicit Codex attempt-view "
            "reconciliation"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.execution.session_log:_append_session_archive_rows",
        artifact=(
            "sessions-archive.jsonl machine-local historical telemetry written via "
            "flush_session_log()"
        ),
        machine_local=True,
        detection=("autoskillit.execution.session_index:find_stale_session_archive_references"),
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.execution.process._process_tether:write_tether",
        artifact=(
            "process-tethers/*.json under default_log_dir() — per-spawn spawner/child "
            "identity and ceiling records; ephemeral host-and-boot-tied state, never "
            "relocated, removed by settle() or the sweep — not a configuration artifact"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.core.runtime.artifact_lease:ArtifactLease._acquire",
        artifact=(
            "persistent *.lock sidecars created with mode 0600 for POSIX flock "
            "coordination; file contents carry no host-specific state"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer=("autoskillit.core.runtime.worktree_gate_lease:_write_gate_holder_manifest"),
        artifact=(
            "gate-leases/*.json under default_log_dir() — diagnostic acquisition "
            "identity for worktree test-gate contention; the adjacent flock descriptor "
            "is the sole admission authority"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.execution.otlp_sink:LocalOtlpSink._persist_line",
        artifact=(
            "otlp.jsonl and otlp.jsonl.1 host-local diagnostic stream under the "
            "configured log root; never consumed as relocated configuration"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.workspace.session_skill_catalog:write_skill_unavailability_metadata",
        artifact="add-dir/skill-unavailability.json",
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.pipeline.exploration_context:OwnerBoundExplorationContextStore.bind_launch",
        artifact=(
            ".autoskillit-exploration-authority.json (0600, HMAC-signed) for the "
            "launch-environment explorer binding path — recoverable across a server "
            "restart within the lease TTL"
        ),
        machine_local=False,
        detection=None,
    ),
    DurableArtifactWriterDef(
        writer="autoskillit.pipeline.exploration_context_durable:bind_session_scoped_durable",
        artifact=(
            ".autoskillit-exploration-authority.json (0600, HMAC-signed) for the "
            "session-scoped Claude-native explorer binding path — symmetric to "
            "bind_launch, so this path also survives a server restart within the "
            "lease TTL"
        ),
        machine_local=False,
        detection=None,
    ),
)

_validate_durable_artifact_writer_defs(DURABLE_ARTIFACT_WRITERS)
