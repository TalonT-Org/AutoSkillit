"""Every skill declares a reviewed write scope that covers what its body writes."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from autoskillit.core import ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS
from autoskillit.hooks._write_scope import WriteScope, WriteScopeKind
from autoskillit.recipe._skill_placeholder_parser import extract_write_path_declarations
from autoskillit.workspace.skills import bundled_skills_dir, bundled_skills_extended_dir
from autoskillit.workspace.skills._format import parse_frontmatter_content
from tests.contracts._skill_prose_write_cues import iter_write_cue_targets

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]

REVIEWED_UNRESTRICTED_WRITE_SCOPES: dict[str, str] = {
    "build-execution-map": "worktree output follows a dynamic caller path",
    "bundle-local-report": "the report lands in the caller's research_dir",
    "download-data": "downloads into the caller's research worktree",
    "dry-walkthrough": "updates the caller's plan path, which may be outside temp",
    "file-audit-issues": "writes into an operator- or caller-supplied audit run dir",
    "generate-report": "commits the report inside the research worktree",
    "implement-experiment": "edits a newly created research worktree",
    "implement-worktree": "creates and edits a worktree outside temp",
    "implement-worktree-no-merge": "creates and edits a worktree outside temp",
    "judge-eval": "writes verdict.json into the caller-supplied eval run directory",
    "make-campaign": "publishes a recipe under .autoskillit/recipes/campaigns",
    "merge-pr": "resolves conflicts in a caller-owned integration worktree",
    "mermaid": "edits arbitrary markdown",
    "render-recipe": "publishes the live recipe diagram under recipes/diagrams",
    "resolve-claims-review": "edits and commits caller-worktree files",
    "resolve-failures": "edits and commits in a caller worktree",
    "resolve-merge-conflicts": "edits and commits in a caller worktree",
    "resolve-research-review": "edits and commits caller-worktree files",
    "resolve-review": "edits and commits caller-worktree files",
    "retry-worktree": "resumes edits in a caller-owned worktree",
    "run-experiment": "executes experiment code in the research worktree",
    "setup-environment": "installs into the caller's research worktree",
    "setup-project": "writes .autoskillit/config.yaml",
    "smoke-task": "executes an arbitrary prose task",
    "stage-data": "creates directories in the caller's research worktree",
    "troubleshoot-experiment": "repairs the research worktree",
    "update-architecture": "writes architecture documents under docs/",
    "update-reqs": "writes requirement documents under docs/",
    "update-specs": "writes specification documents under docs/",
    "validate-audit": "writes to an operator-supplied audit run directory",
    "write-recipe": "writes .autoskillit/recipes/",
}

REVIEWED_INHERIT_WRITE_SCOPES: dict[str, str] = {
    "close-kitchen": "server lifecycle toggle; a slash command must not change write posture",
    "open-kitchen": "server lifecycle toggle; a slash command must not change write posture",
    "reload-session": "MCP call plus /exit only",
    "sous-chef": "injected orchestrator document; children write under their own scope",
}

READ_REFERENCE_EXEMPTIONS: dict[tuple[str, str], str] = {
    ("open-integration-pr", "{{AUTOSKILLIT_TEMP}}/arch-lens-{lens-name}/"): (
        "describes where the arch-lens skills it invokes write, then reads their output"
    ),
}

OVERRIDE_SCOPE_DIVERGENCE: dict[str, str] = {}

_TEMP_TARGET = re.compile(
    r"(?:\{\{AUTOSKILLIT_TEMP\}\}|\.autoskillit/temp)/([^/\s`]+)(?:/[^\s`]*)?"
)
_REPO_ROOT_TEMP_TARGET = re.compile(r"^(?:\./)?temp/[^/\s`]+")


def _normalize(path: str) -> str:
    return path.replace(".autoskillit/temp/", "{{AUTOSKILLIT_TEMP}}/", 1)


def _tracked_repo_local_skills() -> list[Path]:
    listed = subprocess.run(
        [
            "git",
            "ls-files",
            *(f"{search_dir}/*/SKILL.md" for search_dir in ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS),
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [_REPO_ROOT / path for path in listed]


def _bundled_skills() -> list[Path]:
    return [
        path
        for root in (bundled_skills_dir(), bundled_skills_extended_dir())
        for path in sorted(root.glob("*/SKILL.md"))
    ]


_CORPUS: tuple[Path, ...] = (*_bundled_skills(), *_tracked_repo_local_skills())


def _label(path: Path) -> str:
    if any(
        path.is_relative_to(root) for root in (bundled_skills_dir(), bundled_skills_extended_dir())
    ):
        return f"bundled:{path.parent.name}"
    return f"repo:{path.relative_to(_REPO_ROOT).parent}"


def _scope(path: Path) -> WriteScope:
    scope = parse_frontmatter_content(path.read_text(encoding="utf-8")).write_scope
    assert scope is not None, f"{_label(path)} does not declare a valid write scope"
    return scope


def _declared_prefixes(scope: WriteScope) -> tuple[str, ...]:
    return tuple(_normalize(path).rstrip("/") + "/" for path in scope.paths)


def _covered(path: str, prefixes: tuple[str, ...]) -> bool:
    return any((_normalize(path).rstrip("/") + "/").startswith(prefix) for prefix in prefixes)


def _cued_temp_targets(path: Path) -> list[tuple[int, str]]:
    """Return (line, temp path) for every concrete temp directory a write cue names."""
    parsed = parse_frontmatter_content(path.read_text(encoding="utf-8"))
    body_offset = parsed.content.count("\n") - parsed.body.count("\n")
    targets: list[tuple[int, str]] = []
    for candidate in iter_write_cue_targets(parsed.body):
        for match in _TEMP_TARGET.finditer(candidate.target):
            if match.group(1).startswith("<"):
                continue
            entry = (candidate.target_line + body_offset, _normalize(match.group(0)))
            if entry not in targets:
                targets.append(entry)
    return targets


def _kind_members(kind: WriteScopeKind) -> set[str]:
    return {path.parent.name for path in _CORPUS if _scope(path).kind is kind}


@pytest.mark.parametrize("path", _CORPUS, ids=_label)
def test_every_skill_declares_a_valid_write_scope(path: Path) -> None:
    parsed = parse_frontmatter_content(path.read_text(encoding="utf-8"))
    assert parsed.write_scope is not None, (_label(path), parsed.write_scope_issue)


def test_unrestricted_declarers_equal_the_reviewed_allowlist() -> None:
    assert _kind_members(WriteScopeKind.UNRESTRICTED) == set(REVIEWED_UNRESTRICTED_WRITE_SCOPES)


def test_inherit_declarers_equal_the_reviewed_allowlist() -> None:
    assert _kind_members(WriteScopeKind.INHERIT) == set(REVIEWED_INHERIT_WRITE_SCOPES)


_BOUNDED = tuple(path for path in _CORPUS if _scope(path).kind is WriteScopeKind.BOUNDED)
_INHERIT = tuple(path for path in _CORPUS if _scope(path).kind is WriteScopeKind.INHERIT)


@pytest.mark.parametrize("path", _BOUNDED, ids=_label)
def test_bounded_body_write_targets_fall_inside_declared_prefixes(path: Path) -> None:
    prefixes = _declared_prefixes(_scope(path))
    uncovered = [
        (line, target)
        for line, target in _cued_temp_targets(path)
        if not _covered(target, prefixes)
        and (path.parent.name, target) not in READ_REFERENCE_EXEMPTIONS
    ]
    assert not uncovered, (
        f"{_label(path)} writes outside its declared write_paths {list(prefixes)}: {uncovered}. "
        "Add the directory to write_paths (a genuine write) or a reviewed "
        "READ_REFERENCE_EXEMPTIONS entry (a read)."
    )


@pytest.mark.parametrize("path", _BOUNDED, ids=_label)
def test_bounded_never_block_scope_matches_declaration(path: Path) -> None:
    prefixes = _declared_prefixes(_scope(path))
    undeclared = [
        declared
        for declared in extract_write_path_declarations(path.read_text(encoding="utf-8"))
        if not _covered("{{AUTOSKILLIT_TEMP}}/" + declared, prefixes)
    ]
    assert not undeclared, f"{_label(path)} NEVER block names undeclared scope {undeclared}"


@pytest.mark.parametrize("path", _INHERIT, ids=_label)
def test_inherit_skills_declare_no_writes(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    assert not _cued_temp_targets(path), _label(path)
    assert not extract_write_path_declarations(content), _label(path)


def test_read_reference_exemptions_are_not_stale() -> None:
    by_name = {path.parent.name: path for path in _BOUNDED}
    stale = [
        key
        for key in READ_REFERENCE_EXEMPTIONS
        if key[0] not in by_name
        or key[1] not in {target for _, target in _cued_temp_targets(by_name[key[0]])}
        or _covered(key[1], _declared_prefixes(_scope(by_name[key[0]])))
    ]
    assert not stale


def test_repo_local_overrides_match_their_bundled_twins() -> None:
    bundled = {path.parent.name: _scope(path) for path in _bundled_skills()}
    divergent = {
        path.parent.name
        for path in _tracked_repo_local_skills()
        if path.parent.name in bundled and _scope(path) != bundled[path.parent.name]
    }
    assert divergent == set(OVERRIDE_SCOPE_DIVERGENCE)


@pytest.mark.parametrize("path", _tracked_repo_local_skills(), ids=_label)
def test_repo_local_skills_never_write_to_repo_root_temp(path: Path) -> None:
    body = parse_frontmatter_content(path.read_text(encoding="utf-8")).body
    offenders = sorted(
        {
            (candidate.target_line, candidate.target)
            for candidate in iter_write_cue_targets(body)
            if _REPO_ROOT_TEMP_TARGET.match(candidate.target.strip())
        }
    )
    assert not offenders, f"{_label(path)} writes to repo-root temp/: {offenders}"
