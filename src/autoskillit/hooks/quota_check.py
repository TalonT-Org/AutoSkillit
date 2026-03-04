#!/usr/bin/env python3
"""PreToolUse hook: quota check before run_skill."""

import json
import subprocess
import sys


def main() -> None:
    try:
        raw = sys.stdin.read()
        _ = json.loads(raw)  # validate event is JSON; contents not needed
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # malformed event — approve
    except Exception as e:
        print(
            f"quota_check: unexpected error reading stdin: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        sys.exit(0)  # log but approve — don't block run_skill on hook bugs

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "autoskillit", "quota-status"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            sys.exit(0)  # tool failed — approve
        data = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError, ValueError):
        sys.exit(0)  # expected infrastructure errors — approve
    except Exception as e:
        print(
            f"quota_check: unexpected error running quota-status: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        sys.exit(0)  # log but approve — quota check must never block valid runs

    if data.get("should_sleep"):
        n = int(data.get("sleep_seconds", 60))
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"Quota threshold exceeded. Sleep {n} seconds then retry. "
                            f'Call run_cmd with: python3 -c "import time; time.sleep({n})" '
                            f"timeout={n + 30}"
                        ),
                    }
                }
            )
        )
    sys.exit(0)  # exit 0 so Claude Code parses the JSON decision


if __name__ == "__main__":
    main()
