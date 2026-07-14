"""InputSpec type system tests — validate closed type vocabulary."""

from __future__ import annotations

import typing

import pytest

from autoskillit.core import VALID_INPUT_SPEC_TYPES
from autoskillit.core.types._type_results import InputSpec

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_input_spec_rejects_unknown_type():
    """Unknown type strings must raise ValueError at construction time."""
    with pytest.raises(ValueError, match="InputSpec.type must be one of"):
        InputSpec(name="x", type="file_path_list_v2", required=False, position=0)


def test_input_spec_accepts_file_path_list():
    """file_path_list is a valid member of the closed type set."""
    spec = InputSpec(name="x", type="file_path_list", required=False, position=0)
    assert spec.type == "file_path_list"


def test_valid_input_spec_types_matches_literal():
    """VALID_INPUT_SPEC_TYPES must match the Literal members declared on InputSpec.type."""
    hints = typing.get_type_hints(InputSpec)
    literal_args = frozenset(typing.get_args(hints["type"]))
    assert literal_args == VALID_INPUT_SPEC_TYPES
