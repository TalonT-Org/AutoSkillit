"""Operator-facing diagnostic/maintenance CLI subcommands.

Each module exposes a single entry point consumed only by ``app.py``: report by
default, mutate only behind an explicit ``--reap`` (or ``--reclaim``) flag.
"""

from autoskillit.cli.ops._capture_store import run_capture_store
from autoskillit.cli.ops._codex_attempts import run_codex_attempts
from autoskillit.cli.ops._codex_orphans import run_codex_orphans
from autoskillit.cli.ops._daemon_orphans import run_daemon_orphans
from autoskillit.cli.ops._process_orphans import run_process_orphans
from autoskillit.cli.ops._sessions import sessions_app

__all__ = [
    "run_capture_store",
    "run_codex_attempts",
    "run_codex_orphans",
    "run_daemon_orphans",
    "run_process_orphans",
    "sessions_app",
]
