"""Backward-compat shim for bash_write_targets — see core.git.bash_write_targets."""

from autoskillit.core.git.bash_write_targets import (
    contains_test_gate_command,
    extract_bash_write_targets,
)

__all__ = ["contains_test_gate_command", "extract_bash_write_targets"]
