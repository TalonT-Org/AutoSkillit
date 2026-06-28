"""Reusable canary state machine and GitHub issue updater for live probes.

IL-1 module: imports only stdlib and `autoskillit.core`. Provides the
persistence + flake-guard primitives that live probe classes build on.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from enum import StrEnum, unique
from pathlib import Path

from autoskillit.core import atomic_write, get_logger, run_gh

logger = get_logger(__name__)

N_CONSECUTIVE_FLAKE_GUARD: int = 3


@unique
class ErrorKind(StrEnum):
    NETWORK = "network"
    SCHEMA = "schema"


@dataclass
class CanaryState:
    network_streak: int = 0
    schema_streak: int = 0
    last_issue_number: int | None = None

    @classmethod
    def load(cls, path: Path) -> CanaryState:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        return cls(
            network_streak=raw.get("network_streak", 0),
            schema_streak=raw.get("schema_streak", 0),
            last_issue_number=raw.get("last_issue_number"),
        )

    def save(self, path: Path) -> None:
        atomic_write(path, json.dumps(asdict(self), indent=2))

    def record_failure(self, kind: ErrorKind) -> None:
        if kind is ErrorKind.NETWORK:
            self.network_streak += 1
        elif kind is ErrorKind.SCHEMA:
            self.schema_streak += 1
        else:
            raise ValueError(f"Unhandled ErrorKind: {kind!r}")

    def record_success(self) -> None:
        self.network_streak = 0
        self.schema_streak = 0

    def should_report(self, flake_guard: int = N_CONSECUTIVE_FLAKE_GUARD) -> bool:
        return self.network_streak >= flake_guard or self.schema_streak >= flake_guard


def _run_gh_with_body_file(args: list[str], body: str) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(body)
        body_path = f.name
    try:
        return run_gh([*args, "--body-file", body_path])
    finally:
        try:
            os.unlink(body_path)
        except OSError:
            logger.debug("canary_body_file_unlink_failed", path=body_path)


class CanaryIssueUpdater:
    def __init__(self, *, owner: str, repo: str) -> None:
        self._owner = owner
        self._repo = repo

    def ensure_issue(self, state: CanaryState, title: str, body: str) -> int:
        existing = self._find_existing(title)
        if existing is not None:
            result = _run_gh_with_body_file(
                [
                    "issue",
                    "edit",
                    str(existing),
                    "--repo",
                    f"{self._owner}/{self._repo}",
                ],
                body,
            )
            if result.returncode != 0:
                logger.warning(
                    "canary_issue_edit_failed",
                    issue=existing,
                    stderr=result.stderr,
                )
            state.last_issue_number = existing
            return existing
        result = _run_gh_with_body_file(
            [
                "issue",
                "create",
                "--repo",
                f"{self._owner}/{self._repo}",
                "--title",
                title,
                "--json",
                "number",
            ],
            body,
        )
        if result.returncode != 0:
            msg = f"gh issue create failed: {result.stderr}"
            raise RuntimeError(msg)
        try:
            issue_number = json.loads(result.stdout)["number"]
        except (json.JSONDecodeError, KeyError) as exc:
            msg = f"gh issue create returned unexpected output: {result.stdout!r}"
            raise RuntimeError(msg) from exc
        state.last_issue_number = issue_number
        return issue_number

    def _find_existing(self, title: str) -> int | None:
        result = run_gh(
            [
                "issue",
                "list",
                "--repo",
                f"{self._owner}/{self._repo}",
                "--search",
                title,
                "--state",
                "open",
                "--json",
                "number,title",
                "--limit",
                "10",
            ],
        )
        if result.returncode != 0:
            logger.warning(
                "canary_find_existing_failed",
                returncode=result.returncode,
                stderr=result.stderr,
            )
            return None
        try:
            issues = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        if not isinstance(issues, list):
            return None
        for issue in issues:
            if issue.get("title") == title:
                number = issue.get("number")
                if isinstance(number, int):
                    return number
        return None


def _handle_post_failure(
    *,
    state_file: str,
    backend: str,
    cli_version: str,
    failure_type: str,
    workflow_run_url: str,
) -> int:
    repo_slug = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo_slug or "/" not in repo_slug:
        logger.error("post_failure_missing_github_repository")
        return 1

    owner, repo = repo_slug.split("/", 1)
    state_path = Path(state_file)
    state = CanaryState.load(state_path)
    try:
        kind = ErrorKind(failure_type)
    except ValueError:
        logger.error("post_failure_invalid_failure_type", failure_type=failure_type)
        return 1
    state.record_failure(kind)

    if state.should_report():
        title = f"[Canary] {backend} probe failure: {kind.value}"
        body = (
            f"**Backend:** {backend}\n"
            f"**CLI Version:** {cli_version}\n"
            f"**Failure Type:** {kind.value}\n"
            f"**Workflow Run:** {workflow_run_url}\n"
            f"**Network Streak:** {state.network_streak}\n"
            f"**Schema Streak:** {state.schema_streak}\n"
        )
        updater = CanaryIssueUpdater(owner=owner, repo=repo)
        try:
            updater.ensure_issue(state, title, body)
        except Exception as exc:
            logger.error("canary_ensure_issue_failed", error=str(exc))

    state.save(state_path)
    return 0


def _cli_main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="autoskillit._probe_canary")
    sub = parser.add_subparsers(dest="command")

    post = sub.add_parser(
        "post-failure", help="Record a probe failure and optionally create an issue"
    )
    post.add_argument("--state-file", required=True, help="Path to canary state JSON file")
    post.add_argument("--backend", required=True, help="Probe backend identifier")
    post.add_argument("--cli-version", required=True, help="CLI version that ran the probe")
    post.add_argument(
        "--failure-type",
        required=True,
        choices=[e.value for e in ErrorKind],
        help="Error kind: network or schema",
    )
    post.add_argument("--workflow-run-url", required=True, help="GitHub Actions workflow run URL")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "post-failure":
        return _handle_post_failure(
            state_file=args.state_file,
            backend=args.backend,
            cli_version=args.cli_version,
            failure_type=args.failure_type,
            workflow_run_url=args.workflow_run_url,
        )

    return 1


if __name__ == "__main__":
    import sys

    sys.exit(_cli_main())
