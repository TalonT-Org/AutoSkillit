"""Extended YAML I/O tests for core/io.py consolidation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core.io import read_versioned_json, write_versioned_json

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


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

    @pytest.mark.parametrize(
        "input_kind",
        ["string", "path"],
        ids=["str-input", "path-input"],
    )
    def test_load_yaml_uses_c_loader_when_available(self, tmp_path, monkeypatch, input_kind):
        import yaml as _yaml

        from autoskillit.core.io import load_yaml

        if not getattr(_yaml, "__with_libyaml__", False):
            pytest.skip("LibYAML not available")
        captured: dict[str, object] = {}
        original_load = _yaml.load

        def spy(data, *, Loader=None, **kw):
            assert not kw, f"unexpected kwargs passed to yaml.load: {kw}"
            captured["Loader"] = Loader
            return original_load(data, Loader=Loader, **kw)

        monkeypatch.setattr("autoskillit.core.io.yaml.load", spy)

        if input_kind == "path":
            p = tmp_path / "t.yaml"
            p.write_text("a: 1\n")
            result = load_yaml(p)
            assert result == {"a": 1}
        else:
            load_yaml("key: val")

        assert captured["Loader"] is _yaml.CSafeLoader

    def test_loader_is_csafe_or_safe(self):
        import yaml as _yaml

        from autoskillit.core.io import _Loader

        if getattr(_yaml, "__with_libyaml__", False):
            assert _Loader is _yaml.CSafeLoader
        else:
            assert _Loader is _yaml.SafeLoader


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

    def test_dumper_is_cdumper_or_dumper(self):
        import yaml as _yaml

        from autoskillit.core.io import _Dumper

        if getattr(_yaml, "__with_libyaml__", False):
            assert _Dumper is _yaml.CDumper
        else:
            assert _Dumper is _yaml.Dumper

    def test_dump_yaml_str_uses_c_dumper_when_available(self, monkeypatch):
        import yaml as _yaml

        from autoskillit.core.io import dump_yaml_str

        if not getattr(_yaml, "__with_libyaml__", False):
            pytest.skip("LibYAML not available")
        captured: dict[str, object] = {}
        original_dump = _yaml.dump

        def spy(data, **kw):
            captured["Dumper"] = kw.get("Dumper")
            return original_dump(data, **kw)

        monkeypatch.setattr("autoskillit.core.io.yaml.dump", spy)
        dump_yaml_str({"x": 1})
        assert captured["Dumper"] is _yaml.CDumper


class TestYamlConsolidationArchitecture:
    def test_only_yaml_imports_yaml_directly(self):
        """Only core/io.py may contain 'import yaml' at any scope."""
        import ast
        from pathlib import Path

        from autoskillit.core.paths import pkg_root

        src_dir = pkg_root()
        allowed_rel = str(Path("core") / "io.py")
        violations = []
        for py_file in sorted(src_dir.rglob("*.py")):
            rel = str(py_file.relative_to(src_dir))
            if rel == allowed_rel:
                continue
            tree = ast.parse(py_file.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "yaml" or alias.name.startswith("yaml."):
                            violations.append(f"{rel}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "").startswith("yaml"):
                        violations.append(f"{rel}: from {node.module} import ...")
        assert not violations, f"Direct yaml imports found outside core/io.py: {violations}"


def test_atomic_write_is_canonical_public_name():
    """_atomic_write must not appear in core.io.__all__; atomic_write must."""
    import autoskillit.core.io as io_mod

    assert "atomic_write" in io_mod.__all__
    assert "_atomic_write" not in io_mod.__all__


def test_atomic_write_importable_via_core_gateway():
    from autoskillit.core import atomic_write

    assert callable(atomic_write)


def test_atomic_write_private_alias_removed():
    """_atomic_write must not be importable as a module attribute."""
    import autoskillit.core.io as io_mod

    assert not hasattr(io_mod, "_atomic_write")


# ---------------------------------------------------------------------------
# write_versioned_json — schema version envelope helper
# ---------------------------------------------------------------------------


def test_write_versioned_json_enriches_payload_with_schema_version(tmp_path):

    from autoskillit.core.io import write_versioned_json

    target = tmp_path / "f.json"
    write_versioned_json(target, {"a": 1}, schema_version=2)
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "schema_version": 2}


def test_write_versioned_json_preserves_existing_keys_atomically(tmp_path, monkeypatch):
    """Asserts the helper routes through ``atomic_write`` (no partial-file
    fallout on a simulated mid-write crash)."""

    from autoskillit.core import io as io_mod
    from autoskillit.core.io import write_versioned_json

    calls: list[tuple[str, str]] = []
    real_atomic_write = io_mod.atomic_write

    def spy(path, content):
        calls.append((str(path), content))
        return real_atomic_write(path, content)

    monkeypatch.setattr(io_mod, "atomic_write", spy)

    target = tmp_path / "nested.json"
    payload = {"outer": {"inner": [1, 2, 3]}, "name": "demo"}
    write_versioned_json(target, payload, schema_version=7)

    assert len(calls) == 1
    assert calls[0][0] == str(target)
    decoded = json.loads(target.read_text(encoding="utf-8"))
    assert decoded == {"outer": {"inner": [1, 2, 3]}, "name": "demo", "schema_version": 7}


def test_write_versioned_json_rejects_non_dict_payload(tmp_path):
    import pytest

    target = tmp_path / "bad.json"
    with pytest.raises(TypeError, match="dict payload"):
        write_versioned_json(target, [1, 2, 3], schema_version=1)  # type: ignore[arg-type]
    assert not target.exists()


def test_write_versioned_json_produces_indented_output(tmp_path):
    target = tmp_path / "f.json"
    write_versioned_json(target, {"a": 1, "b": [2, 3]}, schema_version=1)
    raw = target.read_text(encoding="utf-8")
    lines = raw.strip().splitlines()
    assert len(lines) > 1, "Output must be multi-line (indented)"
    assert json.loads(raw) == {"a": 1, "b": [2, 3], "schema_version": 1}


# read_versioned_json — schema version validation helper


class TestReadVersionedJson:
    def setup_method(self):
        from autoskillit.core.io import _reset_schema_drift_logged_for_tests

        _reset_schema_drift_logged_for_tests()

    def test_read_versioned_json_returns_payload_on_match(self, tmp_path: Path) -> None:
        from autoskillit.core.io import write_versioned_json

        target = tmp_path / "v3.json"
        write_versioned_json(target, {"a": 1}, schema_version=3)
        result = read_versioned_json(target, expected_version=3)
        assert result == {"a": 1, "schema_version": 3}

    def test_read_versioned_json_returns_none_on_version_mismatch(self, tmp_path: Path) -> None:
        from autoskillit.core.io import write_versioned_json

        target = tmp_path / "v1.json"
        write_versioned_json(target, {"a": 1}, schema_version=1)
        result = read_versioned_json(target, expected_version=3)
        assert result is None

    def test_read_versioned_json_logs_drift_warning_on_mismatch(self, tmp_path: Path) -> None:
        import warnings as _warnings

        from autoskillit.core.io import write_versioned_json

        target = tmp_path / "v1.json"
        write_versioned_json(target, {"a": 1}, schema_version=1)
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            result = read_versioned_json(target, expected_version=3)
        assert result is None
        assert len(w) == 1
        assert "schema_drift" in str(w[0].message)

    def test_read_versioned_json_deduplicates_drift_warnings(self, tmp_path: Path) -> None:
        import warnings as _warnings

        from autoskillit.core.io import write_versioned_json

        target = tmp_path / "v1.json"
        write_versioned_json(target, {"a": 1}, schema_version=1)
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            read_versioned_json(target, expected_version=3)
            read_versioned_json(target, expected_version=3)
        assert len(w) == 1

    def test_read_versioned_json_deduplicates_per_path_not_globally(self, tmp_path: Path) -> None:
        import warnings as _warnings

        from autoskillit.core.io import write_versioned_json

        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        write_versioned_json(a, {"a": 1}, schema_version=1)
        write_versioned_json(b, {"b": 2}, schema_version=1)
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            read_versioned_json(a, expected_version=3)
            read_versioned_json(b, expected_version=3)
        assert len(w) == 2

    def test_read_versioned_json_returns_none_on_missing_file(self, tmp_path: Path) -> None:

        result = read_versioned_json(tmp_path / "nonexistent.json", expected_version=1)
        assert result is None

    def test_read_versioned_json_returns_none_on_corrupt_json(self, tmp_path: Path) -> None:

        target = tmp_path / "bad.json"
        target.write_text("not valid json {{{", encoding="utf-8")
        result = read_versioned_json(target, expected_version=1)
        assert result is None

    def test_read_versioned_json_returns_none_on_non_dict(self, tmp_path: Path) -> None:

        target = tmp_path / "array.json"
        target.write_text("[1,2,3]", encoding="utf-8")
        result = read_versioned_json(target, expected_version=1)
        assert result is None

    def test_read_versioned_json_returns_none_on_missing_schema_version_key(
        self, tmp_path: Path
    ) -> None:

        target = tmp_path / "no_ver.json"
        target.write_text('{"a": 1}', encoding="utf-8")
        result = read_versioned_json(target, expected_version=1)
        assert result is None

    def test_read_versioned_json_reset_drift_set_clears_dedup(self, tmp_path: Path) -> None:
        import warnings as _warnings

        from autoskillit.core.io import (
            _reset_schema_drift_logged_for_tests,
            write_versioned_json,
        )

        target = tmp_path / "v1.json"
        write_versioned_json(target, {"a": 1}, schema_version=1)
        with _warnings.catch_warnings(record=True) as w1:
            _warnings.simplefilter("always")
            read_versioned_json(target, expected_version=3)
        assert len(w1) == 1

        _reset_schema_drift_logged_for_tests()

        with _warnings.catch_warnings(record=True) as w2:
            _warnings.simplefilter("always")
            read_versioned_json(target, expected_version=3)
        assert len(w2) == 1

    def test_read_versioned_json_deduplicates_per_path_and_version(self, tmp_path: Path) -> None:
        import warnings as _warnings

        from autoskillit.core.io import write_versioned_json

        target = tmp_path / "v1.json"
        write_versioned_json(target, {"a": 1}, schema_version=1)
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            read_versioned_json(target, expected_version=3)
            read_versioned_json(target, expected_version=5)
        assert len(w) == 2


class TestReadResult:
    def test_missing_is_not_corrupt(self):
        from autoskillit.core.io import ReadResult

        r = ReadResult.missing({})
        assert r.is_corrupt is False
        assert r.data == {}

    def test_corrupt_carries_raw_bytes(self):
        from autoskillit.core.io import ReadResult

        r = ReadResult.corrupt(b"content")
        assert r.raw_bytes == b"content"
        assert r.is_corrupt is True
        assert r.data == {}

    def test_ok_data_accessible(self):
        from autoskillit.core.io import ReadResult

        r = ReadResult.ok({"key": "val"})
        assert r.data == {"key": "val"}
        assert r.is_corrupt is False


class TestSafeUpsertSection:
    def test_appends_section_to_file_without_it(self, tmp_path):
        from autoskillit.core.io import safe_upsert_section

        p = tmp_path / "config.toml"
        p.write_text("[other]\nkey = 1\n", encoding="utf-8")
        section_text = '[mcp_servers.autoskillit]\ncommand = "autoskillit"\n'
        safe_upsert_section(p, "[mcp_servers.autoskillit]", section_text)
        result = p.read_text(encoding="utf-8")
        assert "[other]" in result
        assert "key = 1" in result
        assert "[mcp_servers.autoskillit]" in result
        assert 'command = "autoskillit"' in result

    def test_replaces_existing_section(self, tmp_path):
        from autoskillit.core.io import safe_upsert_section

        p = tmp_path / "config.toml"
        p.write_text(
            "[preamble]\nfoo = 1\n\n"
            '[mcp_servers.autoskillit]\ncommand = "old"\n\n'
            "[other]\nbar = 2\n",
            encoding="utf-8",
        )
        section_text = '[mcp_servers.autoskillit]\ncommand = "new"\n'
        safe_upsert_section(p, "[mcp_servers.autoskillit]", section_text)
        result = p.read_text(encoding="utf-8")
        assert "[preamble]" in result
        assert "foo = 1" in result
        assert "[other]" in result
        assert "bar = 2" in result
        assert 'command = "new"' in result
        assert 'command = "old"' not in result

    def test_handles_section_at_end_of_file(self, tmp_path):
        from autoskillit.core.io import safe_upsert_section

        p = tmp_path / "config.toml"
        p.write_text(
            '[preamble]\nfoo = 1\n\n[mcp_servers.autoskillit]\ncommand = "old"\n',
            encoding="utf-8",
        )
        section_text = '[mcp_servers.autoskillit]\ncommand = "new"\n'
        safe_upsert_section(p, "[mcp_servers.autoskillit]", section_text)
        result = p.read_text(encoding="utf-8")
        assert "[preamble]" in result
        assert "foo = 1" in result
        assert 'command = "new"' in result
        assert 'command = "old"' not in result
