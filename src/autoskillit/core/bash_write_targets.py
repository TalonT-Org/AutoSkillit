"""Backward-compat shim for bash_write_targets — see core.git.bash_write_targets."""

from autoskillit.core.git.bash_write_targets import (
    _strip_heredoc_bodies,
    contains_test_gate_command,
    extract_bash_write_targets,
)

__all__ = ["_strip_heredoc_bodies", "contains_test_gate_command", "extract_bash_write_targets"]
