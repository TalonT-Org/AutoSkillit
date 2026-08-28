"""Provider wire fields may be read only by their declared parser authority."""

from __future__ import annotations

import ast

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.medium]

_WIRE_FIELDS = frozenset(
    {
        "api_error_status",
        "error",
        "isApiErrorMessage",
        "is_api_error_message",
        "rateLimitType",
        "rate_limit_info",
        "rate_limit_type",
        "resetsAt",
        "resets_at",
        "terminalReason",
        "terminal_reason",
    }
)
_EXPECTED_READERS = frozenset(
    ("execution/session/_session_parser.py", field_name) for field_name in _WIRE_FIELDS
)


class _RawProviderFieldReaderVisitor(ast.NodeVisitor):
    """Inventory only raw-provider dictionary reads, not internal schema fields."""

    def __init__(self) -> None:
        self.raw_names: set[str] = set()
        self.fields: set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
            if node.value.func.attr == "loads":
                self.raw_names.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )
        elif isinstance(node.value, ast.BoolOp) or isinstance(node.value, ast.Call):
            names = {child.id for child in ast.walk(node.value) if isinstance(child, ast.Name)}
            if names & self.raw_names:
                self.raw_names.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if isinstance(node.func.value, ast.Name) and node.func.value.id in self.raw_names:
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    if node.args[0].value in _WIRE_FIELDS:
                        self.fields.add(node.args[0].value)
        elif isinstance(node.func, ast.Name) and node.func.id == "_provider_field":
            if (
                node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in self.raw_names
            ):
                for argument in node.args[1:]:
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        if argument.value in _WIRE_FIELDS:
                            self.fields.add(argument.value)
        self.generic_visit(node)


def _read_fields(source: str) -> set[str]:
    visitor = _RawProviderFieldReaderVisitor()
    visitor.visit(ast.parse(source))
    return visitor.fields


def test_declared_provider_wire_fields_have_one_reader_authority() -> None:
    source_path = SRC_ROOT / "execution/session/_session_parser.py"

    actual = frozenset(
        ("execution/session/_session_parser.py", field_name)
        for field_name in _read_fields(source_path.read_text())
    )

    assert actual == _EXPECTED_READERS


def test_raw_provider_record_reader_outside_authority_is_detected() -> None:
    source = "import json\nobj = json.loads(line)\nobj.get('rateLimitType')\n"

    assert _read_fields(source) == {"rateLimitType"}


def test_internal_schema_reads_do_not_count_as_provider_wire_reads() -> None:
    source = "record = {'resets_at': 1, 'subagent_type': 'child'}\nrecord.get('resets_at')\n"

    assert _read_fields(source) == set()
