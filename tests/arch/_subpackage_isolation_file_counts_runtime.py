from __future__ import annotations

RUNTIME_FILE_COUNT_LIMITS: dict[str, int] = {
    "server": 28,
    "execution": 23,
    "cli": 9,
    "cli/session": 11,
    "cli/doctor": 13,
    "pipeline": 19,
    "fleet": 28,
    "server/tools": 39,
    "execution/process": 11,
    "execution/backends": 30,
    "execution/github_review": 15,
    "execution/headless": 15,
    "execution/session": 20,
}
