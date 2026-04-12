"""Shared fixtures and helpers for tests/execution/."""

from __future__ import annotations

import textwrap

# Simulates Claude CLI process that writes a result line then hangs.
# Used by test_process_channel_b.py and test_process_monitor.py.
WRITE_RESULT_THEN_HANG_SCRIPT = textwrap.dedent("""\
    import sys, time, json
    result = {"type": "result", "subtype": "success", "is_error": False,
              "result": "done", "session_id": "s1"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    time.sleep(3600)
""")
