"""Require every Protocol-declared test fake to have contract coverage.

The AST guard matches direct Protocol bases or exact names after stripping the
``Fake``/``InMemory``/``Mock`` prefixes. It checks enrollment, not behavior, and
deliberately excludes this file so the allowlist cannot self-enroll its entries.
Concrete classes and unrelated fake names remain outside this static check.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FAKES_PATH = _REPO_ROOT / "tests" / "fakes.py"
_CORE_ROOT = _REPO_ROOT / "src" / "autoskillit" / "core"
_CONTRACTS_DIR = _REPO_ROOT / "tests" / "contracts"

_FAKE_PREFIXES: tuple[str, ...] = ("Fake", "InMemory", "Mock")

#: Pre-existing Protocol-declared fakes without shared contract coverage.
_UNENROLLED_ALLOWLIST: frozenset[str] = frozenset(
    {
        # These have structural conformance checks in test_fakes_conformance.py T1.
        "InMemoryHeadlessExecutor",
        "InMemoryTestRunner",
        "InMemoryRecipeRepository",
        "InMemoryCIWatcher",
        "InMemoryMergeQueueWatcher",
        "InMemoryDatabaseReader",
        "MockSubprocessRunner",
        # No isinstance conformance check exists for these either.
        "FakeLaunchResolver",
        "FakeSkillSessionContractStore",
        "InMemoryGitHubApiLog",
    }
)


def _protocol_names(core_root: Path) -> set[str]:
    names: set[str] = set()
    for py_file in core_root.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if any(
                (isinstance(base, ast.Name) and base.id == "Protocol")
                or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
                for base in node.bases
            ):
                names.add(node.name)
    return names


def _protocol_declared_fake_names(fakes_path: Path, protocol_names: set[str]) -> set[str]:
    tree = ast.parse(fakes_path.read_text())
    declared: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
            continue
        base_names = {base.id for base in node.bases if isinstance(base, ast.Name)}
        if base_names & protocol_names:
            declared.add(node.name)
            continue
        for prefix in _FAKE_PREFIXES:
            if node.name.startswith(prefix) and node.name[len(prefix) :] in protocol_names:
                declared.add(node.name)
                break
    return declared


def _contract_suite_references(contracts_dir: Path) -> set[str]:
    references: set[str] = set()
    for py_file in contracts_dir.rglob("test_*.py"):
        if py_file.resolve() == Path(__file__).resolve():
            continue  # this file's own allowlist would otherwise self-enroll every entry
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                references.add(node.id)
            elif isinstance(node, ast.Attribute):
                references.add(node.attr)
    return references


def test_protocol_scan_accepts_qualified_protocol_bases(tmp_path: Path) -> None:
    (tmp_path / "qualified.py").write_text(
        "import typing\n\nclass QualifiedProtocol(typing.Protocol):\n    pass\n"
    )

    assert _protocol_names(tmp_path) == {"QualifiedProtocol"}


def test_every_fake_is_enrolled_in_a_shared_contract_suite() -> None:
    protocol_names = _protocol_names(_CORE_ROOT)
    assert protocol_names, (
        "No Protocol classes found under src/autoskillit/core/ — check scan path"
    )

    declared_fakes = _protocol_declared_fake_names(_FAKES_PATH, protocol_names)
    assert declared_fakes, "No Protocol-declared fakes found in tests/fakes.py — check scan logic"

    allowlisted_names = _UNENROLLED_ALLOWLIST
    stale_allowlist_entries = allowlisted_names - declared_fakes
    assert not stale_allowlist_entries, (
        "Allowlist entries no longer match any Protocol-declared fake (class renamed or "
        f"removed?) — remove them: {sorted(stale_allowlist_entries)}"
    )

    contract_references = _contract_suite_references(_CONTRACTS_DIR)
    failures: list[str] = []
    for fake_name in sorted(declared_fakes):
        if fake_name in allowlisted_names:
            continue
        if fake_name not in contract_references:
            failures.append(
                f"{fake_name}: Protocol-declared but not referenced under tests/contracts/ "
                f"and not in _UNENROLLED_ALLOWLIST — enroll it in a shared contract suite "
                f"or allowlist it with a written rationale"
            )

    assert not failures, "Unenrolled Protocol-declared fakes:\n" + "\n".join(failures)
