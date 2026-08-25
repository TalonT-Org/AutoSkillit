"""Architectural guard for downgrade-safe persisted enum decoding."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_persisted_enum_decoding import (
    PERSISTED_ENUM_DECODERS,
    find_bare_enum_constructions,
    find_missing_registered_modules,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
_MODULE = "core/_retiring_cache.py"


def _write_decoder(src_root: Path, source: str, module: str = _MODULE) -> None:
    path = src_root / module
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_no_bare_enum_construction_in_registered_decoders() -> None:
    assert not find_bare_enum_constructions(_SRC_ROOT)


def test_guard_detects_an_injected_bare_construction(tmp_path: Path) -> None:
    _write_decoder(
        tmp_path,
        """\
def decode(payload):
    return PluginArtifactKind(payload["artifact_kind"])
""",
    )

    violations = find_bare_enum_constructions(tmp_path)

    assert violations == [
        "core/_retiring_cache.py:2: bare dynamic construction of PluginArtifactKind; "
        "use its tolerant constructor or quarantine the containing record"
    ]


@pytest.mark.parametrize(
    "body",
    [
        'PluginArtifactKind(payload["artifact_kind"])',
        'PluginArtifactKind(payload.get("artifact_kind"))',
        'kind_value = payload["artifact_kind"]\n    PluginArtifactKind(kind_value)',
        "[PluginArtifactKind(item) for item in payload]",
    ],
    ids=["subscript", "get", "local-alias", "comprehension"],
)
def test_guard_detects_every_dynamic_payload_shape(tmp_path: Path, body: str) -> None:
    _write_decoder(tmp_path, f"def decode(payload):\n    {body}\n")

    violations = find_bare_enum_constructions(tmp_path)

    assert len(violations) == 1
    assert "bare dynamic construction of PluginArtifactKind" in violations[0]


@pytest.mark.parametrize(
    "import_line, constructor",
    [
        (
            "from autoskillit.core import PluginArtifactKind as ArtifactKind",
            "ArtifactKind",
        ),
        ("import autoskillit.core", "autoskillit.core.PluginArtifactKind"),
        ("import autoskillit.core as core", "core.PluginArtifactKind"),
    ],
    ids=["from-alias", "module-qualified", "module-alias"],
)
def test_guard_resolves_import_aliases(tmp_path: Path, import_line: str, constructor: str) -> None:
    _write_decoder(
        tmp_path,
        f"""\
{import_line}
def decode(payload):
    return {constructor}(payload["artifact_kind"])
""",
    )

    violations = find_bare_enum_constructions(tmp_path)

    assert len(violations) == 1
    assert "bare dynamic construction of PluginArtifactKind" in violations[0]


def test_literal_and_named_tolerant_constructors_are_allowed(tmp_path: Path) -> None:
    _write_decoder(
        tmp_path,
        """\
def decode(payload):
    literal = PluginArtifactKind("projection")
    tolerant = PluginArtifactKind.from_persisted(payload["artifact_kind"])
    quarantined = _persisted_enum(PluginArtifactKind, payload["artifact_kind"])
    return literal, tolerant, quarantined
""",
    )

    assert not find_bare_enum_constructions(tmp_path)


def test_explicit_record_quarantine_constructor_is_allowed(tmp_path: Path) -> None:
    _write_decoder(
        tmp_path,
        """\
def _record_from_json(raw):
    return PluginArtifactKind(raw["artifact_kind"])
""",
    )

    assert not find_bare_enum_constructions(tmp_path)


def test_every_registered_decoder_module_exists() -> None:
    assert not find_missing_registered_modules(_SRC_ROOT)
    assert set(PERSISTED_ENUM_DECODERS) == {
        "core/_retiring_cache.py",
        "execution/session/_skill_session_contract_codec.py",
        "fleet/state_types.py",
        "hooks/_capture/_ledger.py",
    }
