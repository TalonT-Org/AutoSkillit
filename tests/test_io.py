"""Tests for _io module utilities."""

from __future__ import annotations

import pytest


# IU1
def test_io_module_has_docstring():
    import autoskillit._io as _io_mod

    assert _io_mod.__doc__ is not None and len(_io_mod.__doc__.strip()) > 0


# IU2
def test_load_yaml_from_path_returns_dict(tmp_path):
    from autoskillit._io import _load_yaml

    p = tmp_path / "x.yaml"
    p.write_text("key: value\n", encoding="utf-8")
    result = _load_yaml(p)
    assert result == {"key": "value"}


# IU3
def test_load_yaml_from_string_parses_yaml():
    from autoskillit._io import _load_yaml

    result = _load_yaml("a: 1\nb: 2\n")
    assert result == {"a": 1, "b": 2}


# IU4
def test_load_yaml_invalid_yaml_raises_yaml_error():
    import yaml

    from autoskillit._io import _load_yaml

    with pytest.raises(yaml.YAMLError):
        _load_yaml("{unclosed: [bracket")


# IU5
def test_load_yaml_path_reads_utf8(tmp_path):
    from autoskillit._io import _load_yaml

    p = tmp_path / "u.yaml"
    p.write_bytes("emoji: \xc3\xa9\n")  # UTF-8 é
    result = _load_yaml(p)
    assert result["emoji"] == "é"


# IU6
def test_load_yaml_returns_any_for_list():
    from autoskillit._io import _load_yaml

    result = _load_yaml("- a\n- b\n")
    assert result == ["a", "b"]
