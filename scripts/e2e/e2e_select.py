#!/usr/bin/env python3
"""Decide which live E2E tests a GitHub Actions event runs, and judge the required gate."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import e2e_catalog

E2E_LABEL = "e2e"
MIN_CHANGED_FILES = 10
MIN_CHANGED_LINES = 300
SAMPLE_PROBABILITY = 0.5
MAX_TESTS = 2
MAX_PIPELINE_TESTS = 1


@dataclass(frozen=True, slots=True)
class PullRequestFacts:
    """The fields of a ``pull_request`` event payload that drive selection."""

    number: int
    head_sha: str
    head_repository: str | None
    changed_files: int
    additions: int
    deletions: int
    labels: frozenset[str]

    @classmethod
    def from_payload(cls, pr: Mapping[str, Any]) -> PullRequestFacts:
        try:
            head = pr["head"]
            head_repo = head.get("repo")
            return cls(
                number=int(pr["number"]),
                head_sha=str(head["sha"]),
                head_repository=(
                    head_repo.get("full_name") if isinstance(head_repo, dict) else None
                ),
                changed_files=int(pr["changed_files"]),
                additions=int(pr["additions"]),
                deletions=int(pr["deletions"]),
                labels=frozenset(str(label["name"]) for label in pr.get("labels") or ()),
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"malformed pull_request payload: {exc!r}") from exc


@dataclass(frozen=True, slots=True)
class Selection:
    """The tests one event runs, with the reason the selector reached that decision."""

    tests: tuple[str, ...]
    reason: str

    @property
    def selected(self) -> bool:
        return bool(self.tests)


def is_eligible(pr: PullRequestFacts) -> bool:
    return E2E_LABEL in pr.labels or _is_large_change(pr)


def _is_large_change(pr: PullRequestFacts) -> bool:
    return (
        pr.changed_files >= MIN_CHANGED_FILES or pr.additions + pr.deletions >= MIN_CHANGED_LINES
    )


def sample_score(number: int, head_sha: str) -> float:
    digest = hashlib.sha256(f"{number}:{head_sha}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def is_sampled(pr: PullRequestFacts, probability: float) -> bool:
    return sample_score(pr.number, pr.head_sha) < probability


def _rank(seed: str, name: str) -> str:
    return hashlib.sha256(f"{seed}:{name}".encode()).hexdigest()


def _touches(test: e2e_catalog.CatalogTest, changed_paths: Sequence[str]) -> bool:
    return any(
        fnmatch.fnmatchcase(path, pattern)
        for path in changed_paths
        for pattern in test.trigger_paths
    )


def pick_tests(
    catalog: e2e_catalog.Catalog,
    changed_paths: Sequence[str],
    seed: str,
    *,
    large_change: bool = False,
) -> tuple[str, ...]:
    eligible = [
        test
        for test in catalog.tests
        if not test.pipeline or (large_change and _touches(test, changed_paths))
    ]
    ranked = sorted(eligible, key=lambda test: _rank(seed, test.name))
    touched = [test for test in ranked if _touches(test, changed_paths)]
    if touched:
        picked: list[str] = []
        pipeline_count = 0
        for test in touched:
            if test.pipeline:
                if pipeline_count >= MAX_PIPELINE_TESTS:
                    continue
                pipeline_count += 1
            picked.append(test.name)
            if len(picked) == MAX_TESTS:
                break
        return tuple(picked)
    return (ranked[0].name,) if ranked else ()


def select_for_pull_request(
    pr: PullRequestFacts,
    changed_paths: Sequence[str],
    catalog: e2e_catalog.Catalog,
    repository: str,
) -> Selection:
    if pr.head_repository != repository:
        return Selection((), f"head repository {pr.head_repository} is not {repository}")
    if not is_eligible(pr):
        lines = pr.additions + pr.deletions
        return Selection(
            (),
            f"below thresholds ({pr.changed_files} files, {lines} lines) and no {E2E_LABEL} label",
        )
    seed = f"{pr.number}:{pr.head_sha}"
    if E2E_LABEL in pr.labels:
        return Selection(
            pick_tests(catalog, changed_paths, seed, large_change=_is_large_change(pr)),
            f"{E2E_LABEL} label",
        )
    if not is_sampled(pr, SAMPLE_PROBABILITY):
        return Selection((), "eligible but not sampled for this head commit")
    return Selection(
        pick_tests(catalog, changed_paths, seed, large_change=_is_large_change(pr)),
        "eligible and sampled",
    )


def select_for_dispatch(requested: str, catalog: e2e_catalog.Catalog) -> Selection:
    names = tuple(dict.fromkeys(part.strip() for part in requested.split(",") if part.strip()))
    if not 1 <= len(names) <= MAX_TESTS:
        raise ValueError(f"workflow_dispatch must name 1 to {MAX_TESTS} tests, got {len(names)}")
    for name in names:
        catalog.get(name)
    return Selection(names, "workflow_dispatch")


def _dispatch_request(payload: Mapping[str, Any]) -> str:
    inputs = payload.get("inputs")
    requested = inputs.get("tests") if isinstance(inputs, dict) else None
    if not isinstance(requested, str):
        raise ValueError("workflow_dispatch payload has no string inputs.tests")
    return requested


def select_for_event(
    event_name: str,
    payload: Mapping[str, Any],
    changed_paths: Sequence[str],
    catalog: e2e_catalog.Catalog,
    repository: str,
) -> Selection:
    if event_name == "pull_request":
        pr = payload.get("pull_request")
        if not isinstance(pr, dict):
            raise ValueError("pull_request payload has no pull_request object")
        facts = PullRequestFacts.from_payload(pr)
        return select_for_pull_request(facts, changed_paths, catalog, repository)
    if event_name == "workflow_dispatch":
        return select_for_dispatch(_dispatch_request(payload), catalog)
    if event_name == "merge_group":
        return Selection((), "merge queue: E2E is decided on the pull request")
    raise ValueError(f"unsupported event: {event_name}")


def matrix_json(selection: Selection, catalog: e2e_catalog.Catalog) -> str:
    include = [
        {
            "test": test.name,
            "kind": test.kind,
            "timeout_minutes": e2e_catalog.job_timeout_minutes(test),
        }
        for test in (catalog.get(name) for name in selection.tests)
    ]
    return json.dumps({"include": include}, separators=(",", ":"))


def gate_verdict(select_result: str, selected: str, run_result: str) -> tuple[bool, str]:
    if select_result == "success" and selected == "true" and run_result == "success":
        return True, "selected E2E tests passed"
    if select_result == "success" and selected == "false" and run_result == "skipped":
        return True, "no E2E test selected"
    return False, (
        f"E2E gate failed: select={select_result or '<empty>'} "
        f"selected={selected or '<empty>'} run={run_result or '<empty>'}"
    )


def _read_event() -> tuple[str, Mapping[str, Any], str]:
    event_name = os.environ.get("GITHUB_EVENT_NAME")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not event_name or not event_path or not repository:
        raise ValueError("GITHUB_EVENT_NAME, GITHUB_EVENT_PATH and GITHUB_REPOSITORY are required")
    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub event payload must be an object")
    return event_name, payload, repository


def _read_changed_paths(path: Path) -> tuple[str, ...]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return tuple(line.strip() for line in lines if line.strip())


def _select(catalog_path: Path, changed_paths_file: Path) -> int:
    try:
        catalog = e2e_catalog.load_catalog(catalog_path)
        event_name, payload, repository = _read_event()
        changed_paths = (
            _read_changed_paths(changed_paths_file) if event_name == "pull_request" else ()
        )
        selection = select_for_event(event_name, payload, changed_paths, catalog, repository)
        matrix = matrix_json(selection, catalog)
    except (OSError, ValueError) as exc:
        print(f"e2e_select: {exc}", file=sys.stderr)
        return 2
    print(f"e2e_select: {selection.reason}", file=sys.stderr)
    sys.stdout.write(
        f"selected={'true' if selection.selected else 'false'}\n"
        f"matrix={matrix}\n"
        f"reason={selection.reason}\n"
    )
    return 0


def main(argv: Sequence[str]) -> int:
    """Print a GitHub Actions output record for ``select``; exit 0/1 on the ``gate`` verdict."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select", help="Choose the tests this event runs.")
    select.add_argument("--catalog", default=str(e2e_catalog.CATALOG_PATH))
    select.add_argument(
        "--changed-paths",
        required=True,
        help="File listing the pull request's changed paths, one per line.",
    )
    gate = commands.add_parser("gate", help="Judge the required e2e-gate check.")
    gate.add_argument("--select-result", required=True)
    gate.add_argument("--selected", required=True)
    gate.add_argument("--run-result", required=True)
    args = parser.parse_args(argv)
    if args.command == "select":
        return _select(Path(args.catalog), Path(args.changed_paths))
    passed, reason = gate_verdict(args.select_result, args.selected, args.run_result)
    print(reason)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
