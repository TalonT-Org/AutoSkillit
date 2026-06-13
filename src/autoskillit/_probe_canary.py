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
        os.unlink(body_path)


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
            return None
        try:
            issues = json.loads(result.stdout)
        except (json.JSONDecodeError, KeyError):
            return None
        if not isinstance(issues, list):
            return None
        for issue in issues:
            if issue.get("title") == title:
                return issue["number"]
        return None
