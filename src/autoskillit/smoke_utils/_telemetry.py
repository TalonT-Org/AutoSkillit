"""Telemetry and PR-patching helpers for smoke_utils sub-modules."""

from __future__ import annotations

import regex as _regex

from autoskillit.core import DISPATCH_ID_ENV_VAR, PR_TELEMETRY_SECTIONS

assert len(PR_TELEMETRY_SECTIONS) == 3, (  # noqa: S101
    f"_PR_SECTION_RE assumes exactly 3 sections; got {len(PR_TELEMETRY_SECTIONS)}"
)
_PR_SECTION_RE = _regex.compile(
    r"\n*"
    + _regex.escape(PR_TELEMETRY_SECTIONS[0])
    + r"\n.*?"
    + "".join(f"(?:\\n{_regex.escape(s)}\\n.*?)?" for s in PR_TELEMETRY_SECTIONS[1:])
    + r"(?=\n## |\Z)",
    _regex.DOTALL,
)


def patch_pr_token_summary(
    pr_url: str,
    cwd: str = "",
    order_id: str = "",
    log_dir: str = "",
    timeout: int = 60,
) -> dict[str, str]:
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import time  # noqa: PLC0415

    from autoskillit.execution import resolve_log_dir  # noqa: PLC0415
    from autoskillit.pipeline import DefaultTokenLog, TelemetryFormatter  # noqa: PLC0415

    m = _regex.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
    if not m:
        return {"success": "false", "error": f"Invalid PR URL: {pr_url}"}

    owner, repo, pr_number = m.group(1), m.group(2), m.group(3)

    effective_order_id = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

    log_root = resolve_log_dir(log_dir)
    token_log = DefaultTokenLog()
    if effective_order_id:
        count = token_log.load_from_log_dir(log_root, order_id_filter=effective_order_id)
    else:
        count = token_log.load_from_log_dir(log_root, cwd_filter=cwd)

    if count == 0:
        return {"success": "false", "error": "No sessions found", "sessions_loaded": "0"}

    scope_kwargs: dict[str, str] = {"order_id": effective_order_id} if effective_order_id else {}
    steps = token_log.get_report(**scope_kwargs)
    total = token_log.compute_total(**scope_kwargs)
    model_totals = token_log.compute_model_totals(**scope_kwargs)
    combined = TelemetryFormatter.format_pr_telemetry_block(steps, total, model_totals)

    try:
        read_result = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/pulls/{pr_number}", "--jq", ".body"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"success": "false", "error": f"Failed to read PR body: {exc}"}

    if read_result.returncode != 0:
        return {"success": "false", "error": f"Failed to read PR: {read_result.stderr.strip()}"}

    current_body = read_result.stdout.strip()
    if not current_body or current_body == "null":
        return {"success": "false", "error": "PR body is empty"}

    if _PR_SECTION_RE.search(current_body):
        new_body = _PR_SECTION_RE.sub("\n\n" + combined, current_body, count=1)
    else:
        new_body = current_body + "\n\n" + combined

    time.sleep(1)

    try:
        patch_result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{owner}/{repo}/pulls/{pr_number}",
                "--method",
                "PATCH",
                "--raw-field",
                f"body={new_body}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"success": "false", "error": f"Failed to patch PR: {exc}"}

    if patch_result.returncode != 0:
        detail = patch_result.stderr.strip() or patch_result.stdout.strip()
        return {
            "success": "false",
            "error": f"Failed to patch PR: {detail}",
        }

    return {"success": "true", "sessions_loaded": str(count)}
