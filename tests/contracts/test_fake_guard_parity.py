"""Meta-check: every Protocol-declared fake in tests/fakes.py is enrolled (T-B5b).

Mirrors the mechanism of ``tests/contracts/test_fetch_issue_mock_contracts.py``:
AST-scan for a structural signal, accumulate failures as strings, one terminal
``assert not all_failures``. Its stated rationale there — *"state-blind or
body-blind mocks... caused the food-truck re-dispatch bug to go undetected
across 7 test files"* — is the same failure shape this guard exists to catch
one level up: a fake that silently skips a precondition the real
implementation enforces.

Asserting *enrolment* is the right job for a static guard — it can see that a
fake class exists and is Protocol-declared, and whether its name is
referenced anywhere under ``tests/contracts/``. Asserting *behaviour* is
T-B5a's (``test_plugin_authority_contract.py``) job: a static scan can see
that a method calls a precondition, not that calling it changes any outcome.
Splitting the two is what makes this pair close the class of bug rather than
the one instance.

**Completeness claim, stated honestly.** "Protocol-declared" here means a
``tests/fakes.py`` class whose name — with a leading ``Fake``/``InMemory``/
``Mock`` prefix stripped — exactly matches a ``Protocol`` class name found
anywhere under ``src/autoskillit/core/`` (19 files define at least one
``Protocol`` subclass there; they are not confined to the ``_type_protocols_*``
shard naming that ``tests/fakes.py``'s own module docstring suggests — e.g.
``SubprocessRunner`` lives in ``_type_subprocess.py`` and
``ManagedHeadlessSessionLineageStore`` lives in ``_type_native_shell_capture.py``).
This catches every fake in the file at the time of writing, including both
this guard exists because of (``FakePluginArtifactAuthority`` ->
``PluginArtifactAuthority``, ``FakeManagedHeadlessSessionLineageStore`` ->
``ManagedHeadlessSessionLineageStore``). It does **not** catch a fake of
something that is a concrete class rather than a ``Protocol``, a fake using a
naming convention other than those three prefixes, or one that doubles a
Protocol under a completely unrelated name.

The "enrolled" scan itself excludes this file — otherwise every allowlisted
name would trivially self-enroll by appearing in ``_UNENROLLED_ALLOWLIST``
below, which asserts the opposite of enrolment.
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

#: Protocol-declared fakes not enrolled in a shared contracts/ suite, each
#: with a one-line rationale. Pre-existing doubles unrelated to issue #4597
#: (mid-session upgrade immunity) — a full contract-suite migration for them
#: is separate work, not a gap this plan addresses. Widening this set to
#: cover a *new* Protocol-declared fake without either enrolling it or
#: writing a real rationale defeats the guard.
_UNENROLLED_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # isinstance-checked in test_fakes_conformance.py's T1 section, but
        # that is protocol *conformance* (structural shape), not a shared
        # behavioural contract suite (T-B5a's kind of coverage).
        ("InMemoryHeadlessExecutor", "isinstance-only; see test_fakes_conformance.py T1"),
        ("InMemoryTestRunner", "isinstance-only; see test_fakes_conformance.py T1"),
        ("InMemoryRecipeRepository", "isinstance-only; see test_fakes_conformance.py T1"),
        ("InMemoryCIWatcher", "isinstance-only; see test_fakes_conformance.py T1"),
        ("InMemoryMergeQueueWatcher", "isinstance-only; see test_fakes_conformance.py T1"),
        ("InMemoryDatabaseReader", "isinstance-only; see test_fakes_conformance.py T1"),
        ("MockSubprocessRunner", "isinstance-only; see test_fakes_conformance.py T1"),
        # No isinstance conformance check exists for these either.
        ("FakeLaunchResolver", "no conformance or contract coverage; pre-existing"),
        ("FakeSkillSessionContractStore", "no conformance or contract coverage; pre-existing"),
        ("InMemoryGitHubApiLog", "no conformance or contract coverage; pre-existing"),
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


def _enrolled_in_a_contract_suite(fake_name: str, contracts_dir: Path) -> bool:
    for py_file in contracts_dir.rglob("test_*.py"):
        if py_file.resolve() == Path(__file__).resolve():
            continue  # this file's own allowlist would otherwise self-enroll every entry
        tree = ast.parse(py_file.read_text())
        if any(
            (isinstance(node, ast.Name) and node.id == fake_name)
            or (isinstance(node, ast.Attribute) and node.attr == fake_name)
            for node in ast.walk(tree)
        ):
            return True
    return False


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

    allowlisted_names = {name for name, _rationale in _UNENROLLED_ALLOWLIST}
    stale_allowlist_entries = allowlisted_names - declared_fakes
    assert not stale_allowlist_entries, (
        "Allowlist entries no longer match any Protocol-declared fake (class renamed or "
        f"removed?) — remove them: {sorted(stale_allowlist_entries)}"
    )

    failures: list[str] = []
    for fake_name in sorted(declared_fakes):
        if fake_name in allowlisted_names:
            continue
        if not _enrolled_in_a_contract_suite(fake_name, _CONTRACTS_DIR):
            failures.append(
                f"{fake_name}: Protocol-declared but not referenced under tests/contracts/ "
                f"and not in _UNENROLLED_ALLOWLIST — enroll it in a shared contract suite "
                f"or allowlist it with a written rationale"
            )

    assert not failures, "Unenrolled Protocol-declared fakes:\n" + "\n".join(failures)
