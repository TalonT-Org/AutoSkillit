"""Contract tests for the paired runtime-import helper in tests/arch/_helpers.py."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import _runtime_import_froms, _runtime_imports, _runtime_plain_imports

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_TRAVERSAL_SAMPLE = """\
from __future__ import annotations
import os
from typing import TYPE_CHECKING
import typing

if TYPE_CHECKING:
    import guarded_plain
    from guarded import name
else:
    import guarded_else

if typing.TYPE_CHECKING:
    from guarded_attr import name

if os.name == "posix":
    import in_if
else:
    from in_else import name

def function():
    import in_function

async def coroutine():
    from in_async import name

class Holder:
    import in_class

try:
    import in_try
except ImportError:
    from in_except import name
else:
    import in_try_else
finally:
    from in_finally import name

with open(__file__) as handle:
    import in_with

for _ in ():
    from in_for import name

while False:
    import in_while
"""


def _install_parse_counter(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    original_parse = ast.parse
    counter = [0]

    def counting_parse(*args, **kwargs):
        counter[0] += 1
        return original_parse(*args, **kwargs)

    monkeypatch.setattr(ast, "parse", counting_parse)
    return counter


def test_runtime_imports_parses_each_path_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "module.py"
    path.write_text("import os\nfrom pathlib import Path\n")
    counter = _install_parse_counter(monkeypatch)

    import_froms, plain_imports = _runtime_imports(path)

    assert counter[0] == 1
    assert [node.module for node in import_froms] == ["pathlib"]
    assert [alias.name for node in plain_imports for alias in node.names] == ["os"]


def test_runtime_imports_pins_selective_traversal(tmp_path: Path) -> None:
    """The walker's descent rules are a contract.

    A broader traversal must fail here: ``ast.walk``, or an ``ast.NodeVisitor`` whose
    ``generic_visit`` descends into ``with``/``for``/``while`` bodies, would collect
    ``in_with``, ``in_for``, and ``in_while``.
    """
    path = tmp_path / "module.py"
    path.write_text(_TRAVERSAL_SAMPLE)

    import_froms, plain_imports = _runtime_imports(path)

    assert [node.module for node in import_froms] == [
        "__future__",
        "typing",
        "in_else",
        "in_async",
        "in_except",
        "in_finally",
    ]
    assert [alias.name for node in plain_imports for alias in node.names] == [
        "os",
        "typing",
        "in_if",
        "in_function",
        "in_class",
        "in_try",
        "in_try_else",
    ]


def test_single_list_helpers_match_paired_result(tmp_path: Path) -> None:
    path = tmp_path / "module.py"
    path.write_text(_TRAVERSAL_SAMPLE)

    import_froms, plain_imports = _runtime_imports(path)

    def dumps(nodes: list[ast.stmt]) -> list[str]:
        return [ast.dump(node, include_attributes=True) for node in nodes]

    assert dumps(_runtime_import_froms(path)) == dumps(import_froms)
    assert dumps(_runtime_plain_imports(path)) == dumps(plain_imports)


def test_runtime_import_helpers_propagate_syntax_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.py"
    path.write_text("def broken(:\n")
    for helper in (_runtime_imports, _runtime_import_froms, _runtime_plain_imports):
        with pytest.raises(SyntaxError):
            helper(path)
