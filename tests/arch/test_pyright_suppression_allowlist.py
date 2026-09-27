"""
Pyright suppression allowlist — REQ-PYRIGHT-001.

Every pyright suppression comment in production code must appear in an
explicit allowlist. Unlisted suppressions fail CI. Every ``# type: ignore`` must
be one that mypy (``task typecheck``) validates, and only those are counted.
"""

import ast
import io
import re
import tokenize
import tomllib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC = REPO_ROOT / "src" / "autoskillit"
TESTS = Path(__file__).resolve().parent.parent
_SUPPRESSION_SCAN_ROOTS = (REPO_ROOT / "src", REPO_ROOT / "scripts", TESTS)

_PYRIGHT_RE = re.compile(r"#\s*pyright:\s*ignore|#.*--\s*pyright:\s*ignore")
_MYPY_VISIBLE_RE = re.compile(r"^#\s*type:\s*ignore\b")
_PYRIGHT_HONORED_TYPE_IGNORE_RE = re.compile(r"(^|#)\s*type:\s*ignore\b")
_ANY_SUPPRESSION_RE = re.compile(r"(^|#)\s*(type|pyright):\s*ignore\b")
_STALENESS_SWITCH_RE = re.compile(r"unused-ignore|ignore-without-code|warn[-_]unused[-_]ignores")

PRODUCTION_ALLOWLIST: dict[tuple[str, int], str] = {
    (
        "recipe/__init__.py",
        374,
    ): "lazy-registry: _reg._finalize_registry() attribute access on dynamically-built registry",
    ("recipe/api_orchestration/_api_orchestration_cache.py", 144): (
        "lazy-registry: RULE_REGISTRY_HASH set by _finalize_registry()"
    ),
}

TEST_ALLOWLIST: dict[tuple[str, int], str] = {
    (
        "arch/test_recipe_rule_registration.py",
        74,
    ): "global-mutated variable Pyright cannot resolve",
    ("recipe/test_research_campaign_rules.py", 7): "side-effect import for rule registration",
    ("recipe/test_research_sub_recipe_rules.py", 9): "side-effect import for rule registration",
}

# Counts only suppressions mypy validates (see `[tool.mypy]`); `warn_unused_ignores`
# makes each one proven necessary.
TYPE_IGNORE_BUDGET = 11


def _scan_pyright_ignores(root: Path) -> set[tuple[str, int]]:
    found: set[tuple[str, int]] = set()
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _PYRIGHT_RE.search(line):
                found.add((str(path.relative_to(root)), i))
    return found


def _python_files(*roots: Path) -> list[Path]:
    return [
        path
        for root in roots
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _comment_tokens(path: Path) -> list[tokenize.TokenInfo]:
    source = path.read_text(encoding="utf-8")
    return [
        tok
        for tok in tokenize.generate_tokens(io.StringIO(source).readline)
        if tok.type == tokenize.COMMENT
    ]


def _is_trailing(tok: tokenize.TokenInfo) -> bool:
    return tok.line[: tok.start[1]].strip() != ""


def _mypy_scope_files() -> list[Path]:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    entries = config.get("tool", {}).get("mypy", {}).get("files")
    assert entries, (
        "[tool.mypy].files is missing — the type: ignore budget is scoped to the files "
        "mypy checks, so the scope must come from mypy's own configuration"
    )
    files: list[Path] = []
    for entry in entries:
        path = REPO_ROOT / entry
        files.extend(_python_files(path) if path.is_dir() else [path])
    return files


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def test_production_pyright_suppressions_are_allowlisted() -> None:
    found = _scan_pyright_ignores(SRC)
    allowed = set(PRODUCTION_ALLOWLIST.keys())
    unlisted = found - allowed
    assert not unlisted, (
        "Unregistered pyright suppression(s) in production code:\n"
        + "\n".join(f"  {p}:{ln}" for p, ln in sorted(unlisted))
        + "\nIf legitimate, add to PRODUCTION_ALLOWLIST with justification."
    )
    stale = allowed - found
    assert not stale, "Stale allowlist entry — suppression no longer exists:\n" + "\n".join(
        f"  {p}:{ln}" for p, ln in sorted(stale)
    )


def test_test_pyright_suppressions_are_allowlisted() -> None:
    found = _scan_pyright_ignores(TESTS)
    allowed = set(TEST_ALLOWLIST.keys())
    unlisted = found - allowed
    assert not unlisted, (
        "Unregistered pyright suppression(s) in test code:\n"
        + "\n".join(f"  {p}:{ln}" for p, ln in sorted(unlisted))
        + "\nIf legitimate, add to TEST_ALLOWLIST with justification."
    )
    stale = allowed - found
    assert not stale, "Stale allowlist entry — suppression no longer exists:\n" + "\n".join(
        f"  {p}:{ln}" for p, ln in sorted(stale)
    )


def test_type_ignore_comments_are_mypy_visible() -> None:
    """A trailing ``type: ignore`` that follows another comment is honored only by Pyright."""
    violations = [
        f"  {_rel(path)}:{tok.start[0]}: {tok.string}"
        for path in _python_files(*_SUPPRESSION_SCAN_ROOTS)
        for tok in _comment_tokens(path)
        if _is_trailing(tok)
        and _PYRIGHT_HONORED_TYPE_IGNORE_RE.search(tok.string)
        and not _MYPY_VISIBLE_RE.match(tok.string)
    ]
    assert not violations, (
        "`# type: ignore` must be the first comment on the line (write "
        "`# type: ignore[code]  # noqa: X`); otherwise mypy never validates it.\n"
        + "\n".join(violations)
    )


def _is_assert_never_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "assert_never") or (
        isinstance(func, ast.Attribute) and func.attr == "assert_never"
    )


def test_assert_never_is_never_suppressed() -> None:
    """A suppressed ``assert_never`` voids the exhaustiveness proof it exists to provide."""
    violations: list[str] = []
    for path in _python_files(*_SUPPRESSION_SCAN_ROOTS):
        suppressions_by_line: dict[int, list[str]] = {}
        for tok in _comment_tokens(path):
            if _ANY_SUPPRESSION_RE.search(tok.string):
                suppressions_by_line.setdefault(tok.start[0], []).append(tok.string)
        if not suppressions_by_line:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not _is_assert_never_call(node):
                continue
            assert isinstance(node, ast.Call)
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                violations.extend(
                    f"  {_rel(path)}:{line}: {comment}"
                    for comment in suppressions_by_line.get(line, [])
                )
    assert not violations, (
        "assert_never must never carry a type/pyright suppression — fix the unhandled "
        "member instead:\n" + "\n".join(violations)
    )


def test_no_suppression_disables_the_staleness_check() -> None:
    """In-source switches would let a dead suppression pass the gate and be counted forever."""
    violations = [
        f"  {_rel(path)}:{tok.start[0]}: {tok.string}"
        for path in _mypy_scope_files()
        for tok in _comment_tokens(path)
        if _STALENESS_SWITCH_RE.search(tok.string)
    ]
    assert not violations, (
        "A suppression needed on only one platform or Python version must be restructured "
        "with a `sys.platform` / `sys.version_info` guard (mypy evaluates it per "
        "`--platform`); never silence `unused-ignore`.\n" + "\n".join(violations)
    )


def test_type_ignore_count_budget() -> None:
    """Guard against unbounded growth of type: ignore suppressions."""
    count = sum(
        1
        for path in _mypy_scope_files()
        for tok in _comment_tokens(path)
        if _MYPY_VISIBLE_RE.match(tok.string)
    )
    assert count <= TYPE_IGNORE_BUDGET, (
        f"type: ignore count ({count}) exceeds TYPE_IGNORE_BUDGET ({TYPE_IGNORE_BUDGET}). "
        "Every counted suppression is one mypy (`task typecheck`) proved necessary. Fix the "
        "type error instead. If a suppression is genuinely unavoidable, stop and ask a human: "
        "raising this budget is a policy relaxation that only a human may approve. Never answer "
        "a Pyright/LSP-only diagnostic with `# type: ignore` — the gate rejects it as unused."
    )
