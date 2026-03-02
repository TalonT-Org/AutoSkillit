"""Extended YAML I/O tests for core/io.py consolidation."""

from __future__ import annotations

import pytest


class TestLoadYamlExtended:
    def test_accepts_path(self, tmp_path):
        from autoskillit.core.io import load_yaml

        f = tmp_path / "t.yaml"
        f.write_text("key: value\n", encoding="utf-8")
        assert load_yaml(f) == {"key": "value"}

    def test_accepts_str(self):
        from autoskillit.core.io import load_yaml

        assert load_yaml("key: value") == {"key": "value"}

    def test_str_multiline(self):
        from autoskillit.core.io import load_yaml

        result = load_yaml("name: test\nflag: true")
        assert result == {"name": "test", "flag": True}

    def test_str_nested(self):
        from autoskillit.core.io import load_yaml

        result = load_yaml("outer:\n  inner: 42")
        assert result == {"outer": {"inner": 42}}

    def test_yaml_error_reexport_is_pyyaml_error(self):
        import yaml

        from autoskillit.core.io import YAMLError

        assert YAMLError is yaml.YAMLError

    def test_load_yaml_str_raises_yaml_error_on_invalid(self):
        from autoskillit.core.io import YAMLError, load_yaml

        with pytest.raises(YAMLError):
            load_yaml("{bad yaml: [unclosed")


class TestDumpYamlStr:
    def test_roundtrip_with_load_yaml(self):
        from autoskillit.core.io import dump_yaml_str, load_yaml

        data = {"a": 1, "b": [2, 3]}
        assert load_yaml(dump_yaml_str(data)) == data

    def test_returns_str_not_bytes(self):
        from autoskillit.core.io import dump_yaml_str

        assert isinstance(dump_yaml_str({"x": 1}), str)

    def test_sort_keys_false_honored(self):
        from autoskillit.core.io import dump_yaml_str

        data = {"z": 1, "a": 2}
        result = dump_yaml_str(data, sort_keys=False)
        assert result.index("z:") < result.index("a:")

    def test_default_flow_style_false_honored(self):
        from autoskillit.core.io import dump_yaml_str

        data = {"key": [1, 2, 3]}
        result = dump_yaml_str(data, default_flow_style=False)
        # Block style: items on separate lines, no inline [...] for lists
        assert "[1, 2, 3]" not in result
