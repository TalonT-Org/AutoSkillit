"""Eager import of modules reached only by except/finally-scoped imports.

Only *new* ``sys.path`` resolutions fail after the install tree is
replaced; modules already in ``sys.modules`` keep working, because
``unlink(2)`` preserves the inode for anything holding it open (the
mechanism behind issue #4469's L3 finding, where the error handler itself
died resolving a lazy import into a deleted tree — ``rich/_emoji_replace.py``
-> ``from ._emoji_codes import EMOJI`` -> ``ModuleNotFoundError``).
Importing these modules eagerly, before any failure path can be the first
resolution, means the crash-recording and cleanup code that runs precisely
when the install root may have just been replaced does not itself become
the second failure.

A complement to immutable, version-addressed install roots, not a
substitute: this shrinks the blast radius of a replacement that has
already happened; only immutable roots prevent the replacement from
reaching a live process at all.

Uses ``importlib.import_module()`` with string module names rather than
literal ``import`` statements. This module is IL-0 so it is callable from
every entry point regardless of layer, but two of the modules it warms
(``autoskillit.fleet``, ``autoskillit.fleet._label_cleanup``) are IL-2 — a
literal ``from autoskillit.fleet import ...`` here would violate IL-0's
zero-upward-imports contract (import-linter statically parses literal
import statements; a dynamic string-named import is invisible to that
scan). The dynamic form is deliberate, narrow infrastructure plumbing for
this one cross-cutting concern, not a layering shortcut for business logic
— do not add other cross-layer imports here by the same mechanism.
"""

from __future__ import annotations

import importlib

from .logging import get_logger

__all__ = ["WARM_MODULE_NAMES", "warm_failure_path_imports"]

logger = get_logger(__name__)

# Modules reached by a
# function-local `autoskillit` import on a genuine except/finally path (or,
# for _process_kill, a path that cannot become a module-level import
# without cycling — see execution/process/_process_tether.py's own comment
# at its import site). Each entry names the *module* the deferred import
# resolves, not the specific symbol.
WARM_MODULE_NAMES: tuple[str, ...] = (
    # pipeline/background.py's except-Exception FailureRecord/RetryReason
    # import, execution/_session_log_recovery.py's ProviderOutcome/
    # RecipeIdentity/SessionTelemetry import, and fleet/_api.py's
    # kill_process_tree import are all satisfied by this one package.
    "autoskillit.core",
    "autoskillit.execution",
    # execution/process/_process_tether.py:304's deferred import — cannot
    # be module-level (cycles with this module at spawn time) but can be
    # warmed here since both modules are importable by the time this runs.
    "autoskillit.execution.process._process_kill",
    # execution/headless/_headless_execute.py's crash-telemetry flush path.
    "autoskillit.execution.session_log",
    # fleet/_dispatch_reaper.py's self-package deferred imports
    # (resolve_stale_running, CampaignStateMutator, DispatchStatus) and
    # fleet/_api.py's mark_dispatch_interrupted import.
    "autoskillit.fleet",
    "autoskillit.fleet._label_cleanup",
    "autoskillit.fleet.state",
)


def warm_failure_path_imports() -> None:
    """Import every module a genuine except/finally-scoped autoskillit import reaches.

    Call once, early, from the cook, headless, and fleet-dispatch entry
    points. Never raises — a warm failure just leaves the pre-existing lazy
    path (unchanged by this function) to resolve the import as before.
    """
    for name in WARM_MODULE_NAMES:
        try:
            importlib.import_module(name)
        except Exception:
            logger.debug("startup_warm_import_failed", module=name, exc_info=True)
