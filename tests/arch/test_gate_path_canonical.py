"""Tests for canonical gate path helpers in pipeline/gate.py.

Verifies that gate_file_path, hook_config_path, and GATE_DIR_COMPONENTS
return paths rooted under .autoskillit/temp/ and that the components constant
is consistent with the runtime path computation.
"""

from __future__ import annotations


def test_gate_file_path_returns_autoskillit_temp(tmp_path):
    """gate_file_path must return a path under .autoskillit/temp/, not temp/."""
    from autoskillit.pipeline.gate import gate_file_path

    result = gate_file_path(tmp_path)
    assert result == tmp_path / ".autoskillit" / "temp" / ".kitchen_gate"


def test_hook_config_path_returns_autoskillit_temp(tmp_path):
    """hook_config_path must return a path under .autoskillit/temp/, not temp/."""
    from autoskillit.pipeline.gate import hook_config_path

    result = hook_config_path(tmp_path)
    assert result == tmp_path / ".autoskillit" / "temp" / ".autoskillit_hook_config.json"


def test_gate_dir_components_constant(tmp_path):
    """GATE_DIR_COMPONENTS must be the tuple of directory components for the gate dir."""
    from autoskillit.pipeline.gate import GATE_DIR_COMPONENTS, gate_file_path

    path = gate_file_path(tmp_path)
    assert path.parent.parts[-2:] == GATE_DIR_COMPONENTS
