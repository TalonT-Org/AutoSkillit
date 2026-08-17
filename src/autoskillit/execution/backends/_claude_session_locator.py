"""Claude Code session locator.

Extracted from `claude.py` to keep the backend file focused on cmd/cmd-spec
grammar and parser concerns. This module owns the file-system walk that
maps a Claude session id to its persisted JSONL path and the index reader
that turns Claude's sessions-index.json into SessionSummary tuples.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    SessionLocator,
    SessionSummary,
    claude_code_log_path,
    claude_code_project_dir,
    read_registry,
)

_ORDER_GREETING_PREFIXES = (
    "Today's special:",
    "Order up! Today's special:",
    "Order up! The kitchen",
    "Kitchen's open!",
    "Table for one!",
    "Fresh off the menu",
    "Welcome to Good Burger, home of the Good Burger, can I take your order?",
)


@dataclass(frozen=True, slots=True)
class ClaudeSessionLocator(SessionLocator):
    def locate_session(self, session_id: str) -> Path | None:
        if not session_id or session_id.startswith(("no_session_", "crashed_")):
            return None
        base = Path.home() / ".claude" / "projects"
        if not base.exists():
            return None
        for project_dir in base.iterdir():
            if not project_dir.is_dir():
                continue
            candidate = project_dir / f"{session_id}.jsonl"
            if candidate.exists():
                return candidate
        return None

    def project_log_dir(self, cwd: str) -> Path:
        return claude_code_project_dir(cwd)

    def session_log_path(self, cwd: str, session_id: str) -> Path | None:
        return claude_code_log_path(cwd, session_id)

    def list_sessions(self, cwd: str) -> tuple[SessionSummary, ...]:
        normalized_cwd = str(Path(cwd).expanduser().resolve(strict=False))
        index_path = self.project_log_dir(normalized_cwd) / "sessions-index.json"
        try:
            entries = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        if not isinstance(entries, list):
            return ()

        launch_ids_by_session_id = {
            claude_session_id: launch_id
            for launch_id, registry_entry in read_registry(Path(normalized_cwd)).items()
            if isinstance(registry_entry, Mapping)
            and isinstance(
                claude_session_id := registry_entry.get("claude_session_id"),
                str,
            )
        }
        summaries: list[SessionSummary] = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("isSidechain"):
                continue
            entry_cwd = entry.get("cwd")
            if not isinstance(entry_cwd, str):
                continue
            resolved_entry_cwd = str(Path(entry_cwd).expanduser().resolve(strict=False))
            if resolved_entry_cwd != normalized_cwd:
                continue

            session_id = entry.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                continue
            first_prompt = entry.get("firstPrompt")
            normalized_prompt = first_prompt if isinstance(first_prompt, str) else ""
            summary = entry.get("summary")
            git_branch = entry.get("gitBranch")
            modified = entry.get("modified")
            summaries.append(
                SessionSummary(
                    backend_name=AGENT_BACKEND_CLAUDE_CODE,
                    session_id=session_id,
                    launch_id=launch_ids_by_session_id.get(session_id),
                    cwd=resolved_entry_cwd,
                    first_prompt=normalized_prompt,
                    summary=summary if isinstance(summary, str) else "",
                    git_branch=git_branch if isinstance(git_branch, str) else None,
                    modified=modified if isinstance(modified, str) else None,
                    is_sidechain=False,
                    session_type_hint=(
                        "order"
                        if normalized_prompt.startswith(_ORDER_GREETING_PREFIXES)
                        else "cook"
                    ),
                )
            )
        return tuple(summaries)


__all__ = ["ClaudeSessionLocator"]
