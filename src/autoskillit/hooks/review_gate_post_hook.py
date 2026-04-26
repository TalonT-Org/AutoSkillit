"""PostToolUse hook: capture review gate tags from run_skill and track check_review_loop calls."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_TAG_LOOP_REQUIRED = "%%REVIEW_GATE::LOOP_REQUIRED%%"
_TAG_CLEAR = "%%REVIEW_GATE::CLEAR%%"
_STATE_FILE_RELPATH = (".autoskillit", "temp", "review_gate_state.json")
_PR_NUMBER_RE = re.compile(r"\b(\d{1,6})\b")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _extract_run_skill_result(tool_response: str | dict) -> str:  # type: ignore[return]
    """Unwrap double-wrapped run_skill JSON to get the inner result string."""
    try:
        outer = json.loads(tool_response) if isinstance(tool_response, str) else tool_response
        inner_str = outer["result"]
        inner = json.loads(inner_str)
        return inner.get("result", "")
    except Exception:
        return ""


def _extract_pr_number(skill_command: str) -> str:
    """Best-effort PR number extraction from a skill_command string."""
    m = _PR_NUMBER_RE.search(skill_command)
    return m.group(1) if m else ""


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_name: str = data.get("tool_name", "")
    tool_input: dict = data.get("tool_input", {}) or {}
    tool_response = data.get("tool_response", "")

    state_file = Path.cwd().joinpath(*_STATE_FILE_RELPATH)

    # Branch 1 & 2: run_skill output containing a gate tag
    if "run_skill" in tool_name:
        result_text = _extract_run_skill_result(tool_response)
        if _TAG_LOOP_REQUIRED in result_text:
            skill_command = tool_input.get("skill_command", "") or tool_input.get("cmd", "")
            pr_number = _extract_pr_number(str(skill_command))
            state = {
                "gate": "LOOP_REQUIRED",
                "review_verdict": "changes_requested",
                "check_review_loop_called": False,
                "pr_number": pr_number,
                "set_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                _atomic_write(state_file, json.dumps(state))
            except Exception:
                pass
            sys.exit(0)

        if _TAG_CLEAR in result_text:
            try:
                state_file.unlink(missing_ok=True)
            except OSError:
                pass
            sys.exit(0)

    # Branch 3: run_python with check_review_loop callable
    if "run_python" in tool_name:
        callable_name: str = tool_input.get("callable", "") or ""
        if "check_review_loop" in callable_name:
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                    if state.get("gate") == "LOOP_REQUIRED":
                        state["check_review_loop_called"] = True
                        # Update pr_number from args if available
                        args = tool_input.get("args") or {}
                        if isinstance(args, dict) and args.get("pr_number"):
                            state["pr_number"] = str(args["pr_number"])
                        _atomic_write(state_file, json.dumps(state))
                except Exception:
                    pass
            sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
