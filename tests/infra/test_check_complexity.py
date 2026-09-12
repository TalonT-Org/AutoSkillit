"""Behavioral tests for scripts/check_complexity.py: counter, ratchet, and CLI.

Loads the script fresh via importlib so tests exercise the real module, following the
dataclass-aware pattern in tests/arch/test_acceptance_policy_relaxation_gate.py.
"""

from __future__ import annotations

import ast
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.infra.conftest import (
    _CONSTRUCT_CASES,
    _MINIMAL_LIMITS,
    _git,
    _source_with_function,
    load_check_script,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_complexity.py"
_CHECK_MODULE_NAME = "_autoskillit_check_complexity"

check = load_check_script(_CHECK_MODULE_NAME, _CHECK_SCRIPT)


# --- shared helpers --------------------------------------------------------------------


def _reader(sources: dict[str, str]):
    return sources.get


def _policy(exemptions=None, max_complexity=10, min_rationale_chars=60):
    return check.ComplexityPolicy(max_complexity, min_rationale_chars, exemptions or {})


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: bytes) -> None:
        self.returncode = returncode
        self.stdout = stdout


def _seed_repo(tmp_path: Path, function_source: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests" / "arch").mkdir(parents=True)
    (repo / "src").mkdir()
    _git(repo.parent, "init", "-q", "repo")
    _git(repo, "config", "user.email", "gate@example.invalid")
    _git(repo, "config", "user.name", "gate")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "tests" / "arch" / "_complexity_limits.py").write_text(
        _MINIMAL_LIMITS, encoding="utf-8"
    )
    (repo / "src" / "a.py").write_text(function_source, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    return repo


def _normalize(text: str) -> str:
    return " ".join(text.split())


# --- counter: exactly ruff's C901 semantics ---------------------------------------------


def _complexity_of(source: str) -> int:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return check.cyclomatic_complexity(node)
    raise AssertionError("no top-level function in snippet")


@pytest.mark.parametrize(
    "label,source,expected", _CONSTRUCT_CASES, ids=[c[0] for c in _CONSTRUCT_CASES]
)
def test_counter_matches_ruff_rules(label, source, expected):
    assert _complexity_of(source) == expected, label


def test_function_metrics_qualnames():
    source = textwrap.dedent(
        """
        from typing import TYPE_CHECKING


        def top_level():
            pass


        class Outer:
            def method(self):
                pass

            class Inner:
                def amethod(self):
                    pass


        def outer():
            def inner():
                pass
            return inner


        def f():
            class K:
                def m(self):
                    pass
            return K


        if TYPE_CHECKING:
            def guarded():
                pass


        @staticmethod
        def decorated():
            pass
        """
    )
    tree = ast.parse(source)
    metrics = check.function_metrics(tree)
    assert set(metrics) == {
        "top_level",
        "Outer.method",
        "Outer.Inner.amethod",
        "outer",
        "outer.<locals>.inner",
        "f",
        "f.<locals>.K.m",
        "guarded",
        "decorated",
    }
    decorated_lineno = next(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "decorated"
    )
    assert metrics["decorated"].lineno == decorated_lineno
    assert metrics["decorated"].end_lineno >= decorated_lineno


def test_duplicate_qualname_keeps_higher_complexity():
    source = textwrap.dedent(
        """
        import sys

        if sys.platform == "win32":
            def f():
                if a:
                    pass
                elif b:
                    pass
        else:
            def f():
                if a:
                    pass
                elif b:
                    pass
                elif c:
                    pass
                elif d:
                    pass
        """
    )
    metrics = check.function_metrics(ast.parse(source))
    assert metrics["f"].complexity == 5


# --- allowed_complexity ------------------------------------------------------------------


def test_allowed_complexity():
    assert check.allowed_complexity(None, None, 10) == 10
    assert check.allowed_complexity(22, None, 10) == 22
    assert check.allowed_complexity(8, None, 10) == 10
    assert check.allowed_complexity(5, (14, "rationale"), 10) == 14
    assert check.allowed_complexity(None, (14, "rationale"), 10) == 14


# --- evaluate: one test per row ----------------------------------------------------------


def test_evaluate_new_function_violation():
    changes = [check.ChangedFile(path="src/x.py", base_path=None)]
    heads = {"src/x.py": _source_with_function("f", 11)}
    violations = check.evaluate(changes, _reader(heads), _reader({}), _policy())
    assert len(violations) == 1
    v = violations[0]
    assert v.qualname == "f" and v.complexity == 11 and v.allowed == 10
    assert v.base_complexity is None
    assert "new function" in check._reason(v, 10)


def test_evaluate_new_function_at_threshold_is_clean():
    changes = [check.ChangedFile(path="src/x.py", base_path=None)]
    heads = {"src/x.py": _source_with_function("f", 10)}
    assert check.evaluate(changes, _reader(heads), _reader({}), _policy()) == []


def test_evaluate_growth_above_base_is_violation():
    changes = [check.ChangedFile(path="src/x.py", base_path="src/x.py")]
    heads = {"src/x.py": _source_with_function("f", 23)}
    bases = {"src/x.py": _source_with_function("f", 22)}
    violations = check.evaluate(changes, _reader(heads), _reader(bases), _policy())
    assert len(violations) == 1
    assert violations[0].base_complexity == 22
    assert "was 22 at the base revision" in check._reason(violations[0], 10)


def test_evaluate_unchanged_complexity_is_clean():
    changes = [check.ChangedFile(path="src/x.py", base_path="src/x.py")]
    source = _source_with_function("f", 22)
    violations = check.evaluate(
        changes, _reader({"src/x.py": source}), _reader({"src/x.py": source}), _policy()
    )
    assert violations == []


def test_evaluate_shrink_is_clean():
    changes = [check.ChangedFile(path="src/x.py", base_path="src/x.py")]
    heads = {"src/x.py": _source_with_function("f", 25)}
    bases = {"src/x.py": _source_with_function("f", 30)}
    assert check.evaluate(changes, _reader(heads), _reader(bases), _policy()) == []


def test_evaluate_exempted_at_limit_is_clean():
    changes = [check.ChangedFile(path="src/x.py", base_path=None)]
    heads = {"src/x.py": _source_with_function("f", 14)}
    policy = _policy(exemptions={"src/x.py::f": (14, "rationale")})
    assert check.evaluate(changes, _reader(heads), _reader({}), policy) == []


def test_evaluate_exempted_above_limit_is_violation():
    changes = [check.ChangedFile(path="src/x.py", base_path=None)]
    heads = {"src/x.py": _source_with_function("f", 15)}
    policy = _policy(exemptions={"src/x.py::f": (14, "rationale")})
    violations = check.evaluate(changes, _reader(heads), _reader({}), policy)
    assert len(violations) == 1
    assert violations[0].exemption == (14, "rationale")
    assert "exempted up to 14" in check._reason(violations[0], 10)


def test_evaluate_vanished_function_inherits_ceiling():
    changes = [
        check.ChangedFile(path="src/old.py", base_path="src/old.py", deleted=True),
        check.ChangedFile(path="src/new.py", base_path=None),
    ]
    heads = {"src/new.py": _source_with_function("f", 18)}
    bases = {"src/old.py": _source_with_function("f", 18)}
    assert check.evaluate(changes, _reader(heads), _reader(bases), _policy()) == []


def test_evaluate_vanished_function_growth_names_old_path():
    changes = [
        check.ChangedFile(path="old/path.py", base_path="old/path.py", deleted=True),
        check.ChangedFile(path="src/new.py", base_path=None),
    ]
    heads = {"src/new.py": _source_with_function("f", 19)}
    bases = {"old/path.py": _source_with_function("f", 18)}
    violations = check.evaluate(changes, _reader(heads), _reader(bases), _policy())
    assert len(violations) == 1
    reason = check._reason(violations[0], 10)
    assert "was 18 at the base revision" in reason
    assert "(in old/path.py)" in reason


def test_evaluate_function_moved_between_modified_files_is_clean():
    changes = [
        check.ChangedFile(path="src/a.py", base_path="src/a.py"),
        check.ChangedFile(path="src/b.py", base_path="src/b.py"),
    ]
    heads = {
        "src/a.py": "def other():\n    pass\n",
        "src/b.py": _source_with_function("f", 20) + "\n\ndef existing():\n    pass\n",
    }
    bases = {
        "src/a.py": _source_with_function("f", 20) + "\n\ndef other():\n    pass\n",
        "src/b.py": "def existing():\n    pass\n",
    }
    assert check.evaluate(changes, _reader(heads), _reader(bases), _policy()) == []


def test_evaluate_surviving_function_is_not_an_inheritance_source():
    changes = [
        check.ChangedFile(path="src/a.py", base_path="src/a.py"),
        check.ChangedFile(path="src/new.py", base_path=None),
    ]
    heads = {
        "src/a.py": _source_with_function("f", 20),
        "src/new.py": _source_with_function("f", 25),
    }
    bases = {"src/a.py": _source_with_function("f", 20)}
    violations = check.evaluate(changes, _reader(heads), _reader(bases), _policy())
    assert len(violations) == 1
    assert violations[0].path == "src/new.py"
    assert violations[0].base_complexity is None
    assert "new function" in check._reason(violations[0], 10)


def test_evaluate_rename_uses_same_file_base_lookup():
    changes = [check.ChangedFile(path="src/new.py", base_path="src/old.py")]
    heads = {"src/new.py": _source_with_function("f", 12)}
    bases = {"src/old.py": _source_with_function("f", 11)}
    violations = check.evaluate(changes, _reader(heads), _reader(bases), _policy())
    assert len(violations) == 1
    assert violations[0].base_complexity == 11
    assert violations[0].base_path == "src/old.py"


def test_evaluate_rename_into_scope_inherits_ceiling():
    changes = [check.ChangedFile(path="src/new.py", base_path="docs/old.py")]
    heads = {"src/new.py": _source_with_function("f", 20)}
    bases = {"docs/old.py": _source_with_function("f", 20)}
    assert check.evaluate(changes, _reader(heads), _reader(bases), _policy()) == []


def test_evaluate_rename_out_of_scope_still_records_vanished():
    changes = [
        check.ChangedFile(path="docs/old.py", base_path="src/old.py"),
        check.ChangedFile(path="src/new.py", base_path=None),
    ]
    heads = {"src/new.py": _source_with_function("f", 20)}
    bases = {"src/old.py": _source_with_function("f", 20)}
    assert check.evaluate(changes, _reader(heads), _reader(bases), _policy()) == []


def test_evaluate_deleted_out_of_scope_and_added_in_scope_inherits():
    changes = [
        check.ChangedFile(path="docs/old.py", base_path="docs/old.py", deleted=True),
        check.ChangedFile(path="src/new.py", base_path=None),
    ]
    heads = {"src/new.py": _source_with_function("f", 18)}
    bases = {"docs/old.py": _source_with_function("f", 18)}
    assert check.evaluate(changes, _reader(heads), _reader(bases), _policy()) == []


def test_evaluate_head_syntax_error_is_skipped(capsys):
    changes = [check.ChangedFile(path="src/x.py", base_path=None)]
    heads = {"src/x.py": "def f(:\n    pass\n"}
    violations = check.evaluate(changes, _reader(heads), _reader({}), _policy())
    assert violations == []
    assert "does not parse" in capsys.readouterr().err


def test_evaluate_unparseable_base_yields_new_function_violation(capsys):
    changes = [check.ChangedFile(path="src/x.py", base_path="src/x.py")]
    heads = {"src/x.py": _source_with_function("f", 11)}
    bases = {"src/x.py": "def f(:\n    pass\n"}
    violations = check.evaluate(changes, _reader(heads), _reader(bases), _policy())
    assert len(violations) == 1
    assert violations[0].base_complexity is None
    assert "does not parse" in capsys.readouterr().err


def test_evaluate_missing_required_head_source_raises_git_failure():
    changes = [check.ChangedFile(path="src/x.py", base_path=None)]
    with pytest.raises(check.GitFailure):
        check.evaluate(changes, _reader({}), _reader({}), _policy())


def test_evaluate_missing_required_base_source_raises_git_failure():
    changes = [check.ChangedFile(path="src/x.py", base_path="src/x.py")]
    heads = {"src/x.py": _source_with_function("f", 5)}
    with pytest.raises(check.GitFailure):
        check.evaluate(changes, _reader(heads), _reader({}), _policy())


# --- rendering ----------------------------------------------------------------------------


def test_report_is_self_instructing_in_warn_mode():
    violation = check.Violation(
        key="src/x.py::Foo.bar",
        path="src/x.py",
        qualname="Foo.bar",
        lineno=12,
        end_lineno=40,
        complexity=14,
        allowed=10,
        base_complexity=None,
        base_path=None,
        exemption=None,
    )
    report = check.render_report([violation], "warn", _policy())
    normalized = _normalize(report)
    ordered_fragments = [
        "WARNING ONLY",
        "will be promoted to a hard failure",
        "src/x.py:12-40  Foo.bar",
        "complexity 14 > allowed 10",
        "1. Refactor first",
        "Do not split a function into trivially named fragments",
        "2. Exemption -- last resort",
        "tests/arch/_complexity_limits.py",
        '"src/x.py::Foo.bar": ComplexityExemption(',
        "limit=14",
        "at least 60 characters",
        "PolicyRelaxationApproval",
        "an automated session must not add the approval",
    ]
    positions = [normalized.index(_normalize(fragment)) for fragment in ordered_fragments]
    assert positions == sorted(positions)


def test_report_fail_mode_banner():
    report = check.render_report([], "fail", _policy())
    assert report.startswith("Cyclomatic complexity check FAILED.")


def _sample_violation():
    return check.Violation(
        key="src/x.py::f",
        path="src/x.py",
        qualname="f",
        lineno=12,
        end_lineno=40,
        complexity=14,
        allowed=10,
        base_complexity=None,
        base_path=None,
        exemption=None,
    )


def test_annotations_emitted_only_under_github_actions(monkeypatch):
    violation = _sample_violation()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    warn_lines = check.render_annotations([violation], "warn", 10)
    assert len(warn_lines) == 1
    assert warn_lines[0].startswith(
        "::warning file=src/x.py,line=12,endLine=40,title=Cyclomatic complexity::"
    )
    fail_lines = check.render_annotations([violation], "fail", 10)
    assert fail_lines[0].startswith(
        "::error file=src/x.py,line=12,endLine=40,title=Cyclomatic complexity::"
    )
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert check.render_annotations([violation], "warn", 10) == []
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    assert check.render_annotations([violation], "warn", 10) == []


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("100% done", "100%25 done"),
        ("line1\rline2", "line1%0Dline2"),
        ("line1\nline2", "line1%0Aline2"),
    ],
)
def test_annotation_escaping_message(raw, expected):
    assert check._escape_message(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("path:with:colons", "path%3Awith%3Acolons"),
        ("path,with,commas", "path%2Cwith%2Ccommas"),
        ("100%", "100%25"),
    ],
)
def test_annotation_escaping_property(raw, expected):
    assert check._escape_property(raw) == expected


# --- main: exit codes ----------------------------------------------------------------------


def test_main_exit_codes_no_violations(tmp_path, capsys):
    repo = _seed_repo(tmp_path, "def f():\n    pass\n")
    assert check.main(["--base", "HEAD", "--repo-root", str(repo)]) == 0
    assert capsys.readouterr().out == ""


def test_main_exit_codes_warn_and_fail(tmp_path, monkeypatch, capsys):
    repo = _seed_repo(tmp_path, _source_with_function("f", 10))
    (repo / "src" / "a.py").write_text(_source_with_function("f", 11), encoding="utf-8")
    _git(repo, "add", "-A")
    assert check.main(["--staged", "--repo-root", str(repo)]) == 0
    capsys.readouterr()
    monkeypatch.setattr(check, "ENFORCEMENT", "fail")
    assert check.main(["--staged", "--repo-root", str(repo)]) == 1


def test_main_missing_mode_exits_2():
    assert check.main([]) == 2


def test_main_conflicting_modes_exits_2():
    assert check.main(["--staged", "--base", "HEAD"]) == 2


def test_main_unavailable_limits_exits_2(tmp_path):
    empty_repo = tmp_path / "empty"
    empty_repo.mkdir()
    _git(empty_repo.parent, "init", "-q", "empty")
    _git(empty_repo, "config", "user.email", "gate@example.invalid")
    _git(empty_repo, "config", "user.name", "gate")
    _git(empty_repo, "config", "commit.gpgsign", "false")
    (empty_repo / "README.md").write_text("x", encoding="utf-8")
    _git(empty_repo, "add", "-A")
    _git(empty_repo, "commit", "-qm", "seed")
    assert check.main(["--base", "HEAD", "--repo-root", str(empty_repo)]) == 2


# --- git plumbing: name-status parsing, scope filtering, and exact commands ----------------


@pytest.mark.parametrize(
    "output,expected",
    [
        ("A\0p\0", [("p", None, False)]),
        ("M\0p\0", [("p", "p", False)]),
        ("D\0p\0", [("p", "p", True)]),
        ("R100\0a\0b\0", [("b", "a", False)]),
    ],
)
def test_changed_files_parses_name_status_tokens(output, expected):
    changes = check._parse_name_status(output)
    assert [(c.path, c.base_path, c.deleted) for c in changes] == expected


def test_in_scope_change_filters_correctly():
    assert check._in_scope_change(check.ChangedFile(path="src/a.py", base_path=None)) is True
    assert (
        check._in_scope_change(
            check.ChangedFile(path="docs/a.py", base_path="docs/a.py", deleted=True)
        )
        is True
    )
    assert check._in_scope_change(check.ChangedFile(path="docs/a.py", base_path=None)) is False
    assert (
        check._in_scope_change(
            check.ChangedFile(path="docs/a.txt", base_path="docs/a.txt", deleted=True)
        )
        is False
    )


def test_staged_mode_git_commands(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_git(_repo_root, *args):
        calls.append(args)
        if args[0] == "diff":
            return _FakeCompleted(0, b"")
        if args[0] == "show":
            return _FakeCompleted(0, b"def f():\n    pass\n")
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(check, "_git", fake_git)
    check.changed_files(Path("/repo"), staged=True, base_rev="HEAD")
    assert ("diff", "--cached", "--name-status", "-M", "-z", "--diff-filter=AMRD", "HEAD") in calls


def test_base_mode_git_commands(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_git(_repo_root, *args):
        calls.append(args)
        if args[0] in ("diff", "ls-files"):
            return _FakeCompleted(0, b"")
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(check, "_git", fake_git)
    check.changed_files(Path("/repo"), staged=False, base_rev="abc123")
    assert ("diff", "--name-status", "-M", "-z", "--diff-filter=AMRD", "abc123") in calls
    assert ("ls-files", "--others", "--exclude-standard", "-z") in calls


def test_readers_use_index_and_named_revision(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_git(_repo_root, *args):
        calls.append(args)
        return _FakeCompleted(0, b"def f():\n    pass\n")

    monkeypatch.setattr(check, "_git", fake_git)
    check._index_reader(Path("/repo"))("src/a.py")
    assert ("show", ":src/a.py") in calls
    check.git_show(Path("/repo"), "HEAD", "src/a.py")
    assert ("show", "HEAD:src/a.py") in calls


def test_working_tree_reader_reads_real_files(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    reader = check._working_tree_reader(tmp_path)
    assert reader("a.py") == "def f():\n    pass\n"
    assert reader("missing.py") is None


def test_git_failure_exits_2(tmp_path, capsys):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    exit_code = check.main(["--staged", "--repo-root", str(not_a_repo)])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_git_timeout_exits_2(monkeypatch, capsys):
    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr(check.subprocess, "run", fake_run)
    exit_code = check.main(["--staged", "--repo-root", "/tmp"])
    assert exit_code == 2
    assert capsys.readouterr().err != ""


def test_decode_source_honors_encoding_cookie():
    data = "# -*- coding: latin-1 -*-\ndef f():\n    pass  # cafe: \xe9\n".encode("latin-1")
    text = check._decode_source(data)
    assert "\xe9" in text


# --- load_policy: literal-only extraction ---------------------------------------------------


_RATIONALE_60 = "a" * 60  # a real 60-char literal, embedded below via repr() -- not an
# expression in the fixture source, since load_policy rejects non-literal bindings.

_VALID_LIMITS = (
    "MAX_COMPLEXITY = 10\n"
    "MIN_RATIONALE_CHARS = 60\n"
    "COMPLEXITY_EXEMPTIONS = {\n"
    f'    "src/x.py::f": ComplexityExemption(limit=14, rationale={_RATIONALE_60!r}),\n'
    "}\n"
)


def test_load_policy_reads_literal_snapshot():
    policy = check.load_policy(_reader({check.LIMITS_PATH.as_posix(): _VALID_LIMITS}))
    assert policy.max_complexity == 10
    assert policy.min_rationale_chars == 60
    assert policy.exemptions == {"src/x.py::f": (14, _RATIONALE_60)}


@pytest.mark.parametrize(
    "source",
    [
        "MIN_RATIONALE_CHARS = 60\nCOMPLEXITY_EXEMPTIONS = {}\n",
        (
            "MAX_COMPLEXITY = 10\nMAX_COMPLEXITY = 11\n"
            "MIN_RATIONALE_CHARS = 60\nCOMPLEXITY_EXEMPTIONS = {}\n"
        ),
        'MAX_COMPLEXITY = "ten"\nMIN_RATIONALE_CHARS = 60\nCOMPLEXITY_EXEMPTIONS = {}\n',
        "MAX_COMPLEXITY = True\nMIN_RATIONALE_CHARS = 60\nCOMPLEXITY_EXEMPTIONS = {}\n",
        (
            "MAX_COMPLEXITY = 10\nMIN_RATIONALE_CHARS = 60\n"
            'COMPLEXITY_EXEMPTIONS = {"k": ComplexityExemption(14, "x", extra=1)}\n'
        ),
    ],
    ids=["missing", "duplicate", "nonliteral", "bool_as_int", "unsupported_ctor_arg"],
)
def test_load_policy_rejects_invalid_shapes(source):
    with pytest.raises(check.PolicyUnavailable):
        check.load_policy(_reader({check.LIMITS_PATH.as_posix(): source}))


def test_load_policy_never_executes_module_body():
    source = (
        "MAX_COMPLEXITY = 10\nMIN_RATIONALE_CHARS = 60\nCOMPLEXITY_EXEMPTIONS = {}\n"
        "1 / 0  # would raise ZeroDivisionError if this module were executed\n"
    )
    policy = check.load_policy(_reader({check.LIMITS_PATH.as_posix(): source}))
    assert policy.max_complexity == 10


def test_staged_policy_uses_index_not_working_tree(tmp_path):
    repo = _seed_repo(tmp_path, _source_with_function("f", 15))
    exempt_limits = (
        "MAX_COMPLEXITY = 10\nMIN_RATIONALE_CHARS = 60\n"
        'COMPLEXITY_EXEMPTIONS = {"src/a.py::f": ComplexityExemption(\n'
        f"    limit=15, rationale={_RATIONALE_60!r},\n"
        ")}\n"
    )
    (repo / "tests" / "arch" / "_complexity_limits.py").write_text(exempt_limits, encoding="utf-8")
    _git(repo, "add", "-A")
    # Dirty the working tree afterward with content that drops the exemption -- staged
    # mode must still see the staged (indexed) version, not this unstaged one.
    (repo / "tests" / "arch" / "_complexity_limits.py").write_text(
        _MINIMAL_LIMITS, encoding="utf-8"
    )
    assert check.main(["--staged", "--repo-root", str(repo)]) == 0


def test_staged_policy_unstaged_exemption_does_not_authorize(tmp_path, monkeypatch, capsys):
    repo = _seed_repo(tmp_path, _source_with_function("f", 10))
    (repo / "src" / "a.py").write_text(_source_with_function("f", 15), encoding="utf-8")
    _git(repo, "add", "src/a.py")
    exempt_limits = (
        "MAX_COMPLEXITY = 10\nMIN_RATIONALE_CHARS = 60\n"
        'COMPLEXITY_EXEMPTIONS = {"src/a.py::f": ComplexityExemption(\n'
        f"    limit=15, rationale={_RATIONALE_60!r},\n"
        ")}\n"
    )
    (repo / "tests" / "arch" / "_complexity_limits.py").write_text(exempt_limits, encoding="utf-8")
    monkeypatch.setattr(check, "ENFORCEMENT", "fail")
    assert check.main(["--staged", "--repo-root", str(repo)]) == 1
    assert "src/a.py::f" in capsys.readouterr().out


# --- validate_exemptions: self-invalidation -------------------------------------------------


def test_validate_exemptions_malformed_key_no_separator():
    policy = _policy(exemptions={"src/x.py.f": (14, "x" * 60)})
    problems = check.validate_exemptions(policy, _reader({}))
    assert len(problems) == 1 and "malformed key" in problems[0]


def test_validate_exemptions_malformed_key_empty_qualname():
    policy = _policy(exemptions={"src/x.py::": (14, "x" * 60)})
    assert "malformed key" in check.validate_exemptions(policy, _reader({}))[0]


def test_validate_exemptions_malformed_key_outside_scan_roots():
    policy = _policy(exemptions={"docs/x.py::f": (14, "x" * 60)})
    assert "malformed key" in check.validate_exemptions(policy, _reader({}))[0]


def test_validate_exemptions_malformed_key_dotdot_component():
    policy = _policy(exemptions={"src/../x.py::f": (14, "x" * 60)})
    assert "malformed key" in check.validate_exemptions(policy, _reader({}))[0]


def test_validate_exemptions_rationale_too_short():
    policy = _policy(exemptions={"src/x.py::f": (14, "x" * 59)})
    source_for = _reader({"src/x.py": _source_with_function("f", 14)})
    assert "at least" in check.validate_exemptions(policy, source_for)[0]


def test_validate_exemptions_limit_not_above_max():
    policy = _policy(exemptions={"src/x.py::f": (10, "x" * 60)})
    source_for = _reader({"src/x.py": _source_with_function("f", 10)})
    assert "must exceed MAX_COMPLEXITY" in check.validate_exemptions(policy, source_for)[0]


def test_validate_exemptions_file_missing():
    policy = _policy(exemptions={"src/x.py::f": (14, "x" * 60)})
    assert "file not found" in check.validate_exemptions(policy, _reader({}))[0]


def test_validate_exemptions_qualname_missing():
    policy = _policy(exemptions={"src/x.py::f": (14, "x" * 60)})
    source_for = _reader({"src/x.py": "def g():\n    pass\n"})
    problem = check.validate_exemptions(policy, source_for)[0]
    assert "stale" in problem and "function not found" in problem


def test_validate_exemptions_no_longer_exceeds():
    policy = _policy(exemptions={"src/x.py::f": (14, "x" * 60)})
    source_for = _reader({"src/x.py": _source_with_function("f", 10)})
    assert "no longer exceeds" in check.validate_exemptions(policy, source_for)[0]


def test_validate_exemptions_below_current_complexity():
    policy = _policy(exemptions={"src/x.py::f": (14, "x" * 60)})
    source_for = _reader({"src/x.py": _source_with_function("f", 16)})
    assert "below the current complexity" in check.validate_exemptions(policy, source_for)[0]


def test_validate_exemptions_valid_entry_is_clean():
    policy = _policy(exemptions={"src/x.py::f": (14, "x" * 60)})
    source_for = _reader({"src/x.py": _source_with_function("f", 14)})
    assert check.validate_exemptions(policy, source_for) == []


def test_invalid_registry_blocks_run_in_both_modes(tmp_path, monkeypatch, capsys):
    repo = _seed_repo(tmp_path, "def f():\n    pass\n")
    stale_limits = (
        "MAX_COMPLEXITY = 10\nMIN_RATIONALE_CHARS = 60\n"
        'COMPLEXITY_EXEMPTIONS = {"src/a.py::missing": ComplexityExemption(\n'
        f"    limit=14, rationale={_RATIONALE_60!r},\n"
        ")}\n"
    )
    (repo / "tests" / "arch" / "_complexity_limits.py").write_text(stale_limits, encoding="utf-8")
    _git(repo, "add", "-A")
    for enforcement in ("warn", "fail"):
        monkeypatch.setattr(check, "ENFORCEMENT", enforcement)
        exit_code = check.main(["--staged", "--repo-root", str(repo)])
        assert exit_code == 2
        assert "stale" in capsys.readouterr().err
