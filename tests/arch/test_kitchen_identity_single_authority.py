"""Count the production references that define kitchen identity authority.

Import-layer checks establish which modules may depend on one another; they do
not establish how many mint, activation, or state-construction sites exist.
These exact occurrence counts also reject duplicate references inside an
otherwise allowed scope.
"""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

SRC_ROOT = Path(__file__).parents[2] / "src" / "autoskillit"
Location = tuple[str, str]

_REFERENCE_TARGETS = frozenset({"resolve_kitchen_id"})
_CALL_TARGETS = frozenset({"new_kitchen_open_state", "activate_kitchen", "KitchenOpenState"})


class _KitchenIdentityOccurrenceCollector(ast.NodeVisitor):
    """Count selected references and calls under their enclosing source scope."""

    def __init__(self, relative_path: str) -> None:
        self._relative_path = relative_path
        self._scope_stack: list[str] = []
        self.references: defaultdict[str, Counter[Location]] = defaultdict(Counter)
        self.calls: defaultdict[str, Counter[Location]] = defaultdict(Counter)
        self.replace_kitchen_id_keywords: Counter[Location] = Counter()

    def _location(self) -> Location:
        return (
            self._relative_path,
            ".".join(self._scope_stack) or "<module>",
        )

    def _record(self, inventory: defaultdict[str, Counter[Location]], symbol: str) -> None:
        inventory[symbol][self._location()] += 1

    def _enter_scope(self, name: str, node: ast.AST) -> None:
        self._scope_stack.append(name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter_scope(node.name, node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter_scope(node.name, node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter_scope(node.name, node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _REFERENCE_TARGETS:
            self._record(self.references, node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _REFERENCE_TARGETS:
            self._record(self.references, node.attr)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in _REFERENCE_TARGETS or alias.asname in _REFERENCE_TARGETS:
                self._record(self.references, "resolve_kitchen_id")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        symbol = _call_name(node.func)
        # Its public wrapper delegates to the store method; count callers of
        # the API, not that implementation detail as a second authority site.
        delegated_activation = (
            symbol == "activate_kitchen"
            and self._relative_path == "server/recipe/_recipe_generation.py"
            and self._scope_stack == ["activate_kitchen"]
            and isinstance(node.func, ast.Attribute)
        )
        if symbol in _CALL_TARGETS and not delegated_activation:
            self._record(self.calls, symbol)
        if symbol == "replace" and any(keyword.arg == "kitchen_id" for keyword in node.keywords):
            self.replace_kitchen_id_keywords[self._location()] += 1
        self.generic_visit(node)


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _collect_occurrences(
    sources: list[tuple[str, str]],
) -> tuple[
    dict[str, Counter[Location]],
    dict[str, Counter[Location]],
    Counter[Location],
]:
    assert sources, "kitchen identity collector scanned no source files"
    references: defaultdict[str, Counter[Location]] = defaultdict(Counter)
    calls: defaultdict[str, Counter[Location]] = defaultdict(Counter)
    replace_kitchen_id_keywords: Counter[Location] = Counter()

    for relative_path, source in sources:
        collector = _KitchenIdentityOccurrenceCollector(relative_path)
        collector.visit(ast.parse(source, filename=relative_path))
        for symbol, counts in collector.references.items():
            references[symbol].update(counts)
        for symbol, counts in collector.calls.items():
            calls[symbol].update(counts)
        replace_kitchen_id_keywords.update(collector.replace_kitchen_id_keywords)

    return dict(references), dict(calls), replace_kitchen_id_keywords


def _collect_source_tree() -> tuple[
    dict[str, Counter[Location]],
    dict[str, Counter[Location]],
    Counter[Location],
]:
    sources = [
        (path.relative_to(SRC_ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted(SRC_ROOT.rglob("*.py"))
    ]
    return _collect_occurrences(sources)


def _assert_exact_counter(
    actual: Counter[Location], expected: Counter[Location], description: str
) -> None:
    assert actual, f"{description} inventory is empty; guard is vacuous"
    duplicates = {location: count for location, count in actual.items() if count != 1}
    assert not duplicates, f"{description} has duplicate occurrences: {duplicates}"
    assert actual == expected, (
        f"{description} occurrences changed.\nExpected: {expected}\nFound:    {actual}"
    )


def test_kitchen_identity_authority_matches_exact_occurrence_counts() -> None:
    references, calls, replace_kitchen_id_keywords = _collect_source_tree()

    _assert_exact_counter(
        references.get("resolve_kitchen_id", Counter()),
        Counter(
            {
                ("core/runtime/__init__.py", "<module>"): 1,
                ("server/lifecycle/_kitchen_identity.py", "<module>"): 1,
                ("server/lifecycle/_kitchen_identity.py", "establish_kitchen_identity"): 1,
            }
        ),
        "resolve_kitchen_id references",
    )
    _assert_exact_counter(
        calls.get("new_kitchen_open_state", Counter()),
        Counter(
            {
                ("server/lifecycle/_kitchen_identity.py", "establish_kitchen_identity"): 1,
                (
                    "server/tools/tools_kitchen/_open_kitchen_transition.py",
                    "_bind_open_kitchen_transition.wrapper",
                ): 1,
            }
        ),
        "new_kitchen_open_state calls",
    )
    _assert_exact_counter(
        calls.get("activate_kitchen", Counter()),
        Counter({("server/lifecycle/_kitchen_identity.py", "establish_kitchen_identity"): 1}),
        "activate_kitchen calls",
    )
    _assert_exact_counter(
        calls.get("KitchenOpenState", Counter()),
        Counter(
            {
                ("pipeline/kitchen_transition.py", "closed_kitchen_open_state"): 1,
                ("pipeline/kitchen_transition.py", "new_kitchen_open_state"): 1,
            }
        ),
        "KitchenOpenState constructions",
    )
    assert not replace_kitchen_id_keywords, (
        f"dataclasses.replace must not change kitchen identity: {replace_kitchen_id_keywords}"
    )


def test_committed_reseed_passes_the_existing_kitchen_id_attribute() -> None:
    source_path = SRC_ROOT / "server/tools/tools_kitchen/_open_kitchen_transition.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    binder = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_bind_open_kitchen_transition"
    )
    wrapper = next(
        node
        for node in ast.walk(binder)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "wrapper"
    )
    reseeds = [
        node
        for node in ast.walk(wrapper)
        if isinstance(node, ast.Call) and _call_name(node.func) == "new_kitchen_open_state"
    ]
    assert len(reseeds) == 1, "the committed re-seed call must remain in the wrapper"
    kitchen_id_values = [
        keyword.value for keyword in reseeds[0].keywords if keyword.arg == "kitchen_id"
    ]
    assert len(kitchen_id_values) == 1, "the re-seed must pass one kitchen_id keyword"
    assert isinstance(kitchen_id_values[0], ast.Attribute)
    assert kitchen_id_values[0].attr == "kitchen_id"


_FORGED_SOURCE = """
from autoskillit.core.runtime import resolve_kitchen_id


def establish_kitchen_identity():
    resolve_kitchen_id()
    new_kitchen_open_state(kitchen_id="first", context_id="ctx")
    new_kitchen_open_state(kitchen_id="second", context_id="ctx")
    activate_kitchen("first")
    KitchenOpenState(phase=None, kitchen_id="first", operation_id="", context_id="ctx")
    replace(state, kitchen_id="forged")


def unregistered_boot():
    resolve_kitchen_id()
    new_kitchen_open_state(kitchen_id="rogue", context_id="ctx")
    activate_kitchen("rogue")
    KitchenOpenState(phase=None, kitchen_id="rogue", operation_id="", context_id="ctx")
    replace(state, kitchen_id="forged")
"""


def test_forged_source_canary_trips_counting_guards() -> None:
    references, calls, replace_kitchen_id_keywords = _collect_occurrences(
        [("synthetic/boot.py", _FORGED_SOURCE)]
    )

    with pytest.raises(AssertionError, match="duplicate occurrences|occurrences changed"):
        _assert_exact_counter(
            references["resolve_kitchen_id"],
            Counter(
                {
                    ("synthetic/boot.py", "<module>"): 1,
                    ("synthetic/boot.py", "establish_kitchen_identity"): 1,
                }
            ),
            "synthetic resolve_kitchen_id references",
        )

    # This duplicate is inside the otherwise allowed establishment scope. The
    # Counter preserves it, where a set-based collector would collapse it.
    assert (
        calls["new_kitchen_open_state"][("synthetic/boot.py", "establish_kitchen_identity")] == 2
    )
    for symbol in ("new_kitchen_open_state", "activate_kitchen", "KitchenOpenState"):
        with pytest.raises(AssertionError, match="duplicate occurrences|occurrences changed"):
            _assert_exact_counter(
                calls[symbol],
                Counter({("synthetic/boot.py", "establish_kitchen_identity"): 1}),
                f"synthetic {symbol} calls",
            )

    with pytest.raises(AssertionError, match="replace must not change kitchen identity"):
        assert not replace_kitchen_id_keywords, (
            f"dataclasses.replace must not change kitchen identity: {replace_kitchen_id_keywords}"
        )
