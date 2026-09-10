"""Generator installation freshness preflight."""

from __future__ import annotations

from autoskillit.core import (
    StaleGeneratorError,
    install_binding_matches_current_state,
    pkg_root,
    resolve_install_binding,
)


def assert_generator_process_fresh() -> None:
    """Verify that the running process's on-disk installation still exists.

    Called at the top of :func:`acquire_launch_binding`, this probe refuses a
    deleted or replaced installation with a typed, actionable error.

    Under Phase 3's immutable, version-addressed install roots (issue #4597),
    an AutoSkillit-initiated upgrade never mutates or deletes a root any live
    process is reading from — it always publishes a fresh generation instead,
    and the retirement engine refuses to reclaim a superseded root until both
    a grace window has elapsed and its lease is uncontended. This probe is
    therefore not "restart after every upgrade" — that hazard is gone — it is
    a backstop against a scenario the transaction guarantees not to cause on
    its own: external tampering (something other than AutoSkillit removed or
    replaced the tree), disk corruption, or an install shaped by a version
    older than #4597's immutable-root scheme.

    Checks:
    1. ``pkg_root()`` must still be a directory and contain ``hooks/_dispatch.py``.
    2. The sealed install root (``InstallBinding``, captured once at this
       process's first access) must still own its path — verified by
       ``device``/``inode`` via ``install_binding_matches_current_state()``,
       never by comparing a live-re-read version string against the frozen
       in-process one. A version-string comparison reads the same fact at two
       different times and is exactly the shape ARCH-012 forbids.
    """
    source = pkg_root()
    dispatcher = source / "hooks" / "_dispatch.py"
    if not source.is_dir() or not dispatcher.is_file():
        raise StaleGeneratorError(
            f"Generator installation deleted: {source} no longer exists or is "
            "missing hooks/_dispatch.py. This should not happen under "
            "AutoSkillit's own immutable install-root lifecycle; if it does, "
            "the tree was altered outside that lifecycle."
        )
    binding = resolve_install_binding()
    if not install_binding_matches_current_state(binding):
        raise StaleGeneratorError(
            f"Generator installation replaced under this process: {binding.root} "
            f"no longer matches the identity sealed at this process's first access "
            f"(device={binding.device}, inode={binding.inode}). This should not "
            "happen under AutoSkillit's own immutable install-root lifecycle; if "
            "it does, the tree was altered outside that lifecycle."
        )
