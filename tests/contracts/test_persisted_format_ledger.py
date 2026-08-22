"""Contracts for the persisted-format enum compatibility ledger."""

from __future__ import annotations

import importlib
from enum import Enum
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core.types._type_persisted_formats import PERSISTED_FORMAT_LEDGER
from scripts.check_persisted_enum_decoding import (
    PERSISTED_ENUM_DECODERS,
    discover_persisted_enum_references,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"


def _resolve_dotted_object(qualname: str) -> Any:
    module_name, separator, attribute_path = qualname.rpartition(".")
    assert separator and module_name and attribute_path, f"invalid dotted name: {qualname!r}"
    value: Any = importlib.import_module(module_name)
    for attribute in attribute_path.split("."):
        value = getattr(value, attribute)
    return value


def _declared_enum_references() -> set[tuple[str, str]]:
    return {
        (format_def.decoder_module, enum_def.enum_qualname.rsplit(".", 1)[-1])
        for format_def in PERSISTED_FORMAT_LEDGER.values()
        for enum_def in format_def.enums
    }


def test_every_persisted_enum_is_declared() -> None:
    discovered = discover_persisted_enum_references(_SRC_ROOT)
    missing = sorted(discovered - _declared_enum_references())
    assert not missing, (
        "Persisted enums constructed by registered decoders are absent from "
        f"PERSISTED_FORMAT_LEDGER: {missing}"
    )


def test_no_declared_enum_has_vanished() -> None:
    discovered = discover_persisted_enum_references(_SRC_ROOT)
    vanished = sorted(_declared_enum_references() - discovered)
    assert not vanished, (
        "PERSISTED_FORMAT_LEDGER declares enums no longer decoded by their registered "
        f"modules: {vanished}"
    )


def test_checker_registry_matches_persisted_format_ledger() -> None:
    ledger_registry: dict[str, frozenset[str]] = {}
    for format_def in PERSISTED_FORMAT_LEDGER.values():
        enum_names = frozenset(
            enum_def.enum_qualname.rsplit(".", 1)[-1] for enum_def in format_def.enums
        )
        assert format_def.decoder_module not in ledger_registry, (
            "Each persisted decoder module must have exactly one ledger owner: "
            f"{format_def.decoder_module}"
        )
        ledger_registry[format_def.decoder_module] = enum_names

    assert ledger_registry == PERSISTED_ENUM_DECODERS


def test_every_declared_enum_member_is_recorded() -> None:
    mismatches: list[str] = []
    for format_def in PERSISTED_FORMAT_LEDGER.values():
        for enum_def in format_def.enums:
            enum_type = _resolve_dotted_object(enum_def.enum_qualname)
            assert isinstance(enum_type, type) and issubclass(enum_type, Enum), (
                f"{enum_def.enum_qualname} must resolve to an Enum type"
            )
            live_members = frozenset(member.name for member in enum_type)
            recorded_members = frozenset(enum_def.members)
            if live_members != recorded_members:
                mismatches.append(
                    f"{enum_def.enum_qualname}: "
                    f"missing={sorted(live_members - recorded_members)}, "
                    f"stale={sorted(recorded_members - live_members)}"
                )

    assert not mismatches, "Persisted enum member ledger drift:\n  " + "\n  ".join(mismatches)


def test_every_recorded_member_declares_its_introduction_version() -> None:
    invalid: list[str] = []
    for format_def in PERSISTED_FORMAT_LEDGER.values():
        for enum_def in format_def.enums:
            for member_name, introduced_version in enum_def.members.items():
                if type(introduced_version) is not int or introduced_version < 1:
                    invalid.append(
                        f"{enum_def.enum_qualname}.{member_name}={introduced_version!r}"
                    )

    assert not invalid, (
        f"Every persisted enum member needs a positive integer introduction version: {invalid}"
    )


def test_members_introduced_after_the_current_version_are_rejected() -> None:
    future_members: list[str] = []
    for format_def in PERSISTED_FORMAT_LEDGER.values():
        current_version = _resolve_dotted_object(format_def.version_constant)
        assert type(current_version) is int and current_version >= 1, (
            f"{format_def.version_constant} must resolve to a positive integer"
        )
        for enum_def in format_def.enums:
            for member_name, introduced_version in enum_def.members.items():
                if introduced_version > current_version:
                    future_members.append(
                        f"{enum_def.enum_qualname}.{member_name}: introduced in "
                        f"{introduced_version}, current format version is {current_version}"
                    )

    assert not future_members, (
        "Persisted enum members cannot predate their enclosing schema bump:\n  "
        + "\n  ".join(future_members)
    )
