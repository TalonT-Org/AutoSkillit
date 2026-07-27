"""Investigation extraction for the remediation bridge_investigation step."""

from __future__ import annotations

from pathlib import Path

import regex as re

from autoskillit.core import atomic_write, run_gh

_INVESTIGATION_HEADING = "## Investigation"
_RECOMMENDATIONS_HEADING = "## Recommendations"
_INVESTIGATION_HEADING_RE = re.compile(r"^" + re.escape(_INVESTIGATION_HEADING), re.MULTILINE)
_RECOMMENDATIONS_HEADING_RE = re.compile(r"^" + re.escape(_RECOMMENDATIONS_HEADING), re.MULTILINE)


def extract_investigation(
    investigation_path: str = "",
    issue_number: str = "",
    output_dir: str = "",
) -> dict[str, str]:
    """Resolve the effective investigation path for rectify.

    Called by run_python from the bridge_investigation step in remediation.yaml.
    When investigation_path points to a complete report, validates and returns it.
    Otherwise, fetches the issue body and extracts the ``## Investigation`` section
    to end-of-body, validating that the section contains the ``## Recommendations``
    heading (the guaranteed-last section of an investigation report).
    """
    if investigation_path and Path(investigation_path).is_file():
        content = Path(investigation_path).read_text()
        if _RECOMMENDATIONS_HEADING_RE.search(content) is None:
            raise ValueError(
                f"investigation_path file lacks '{_RECOMMENDATIONS_HEADING}' heading: "
                f"{investigation_path}"
            )
        return {"investigation_report": str(investigation_path)}

    if not issue_number:
        raise ValueError("issue_number must be non-empty when investigation_path is absent")
    if not output_dir:
        raise ValueError("output_dir must be non-empty")
    if not Path(output_dir).is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")

    gh_result = run_gh(
        ["issue", "view", issue_number, "--json", "body", "-q", ".body"],
        timeout=60,
    )
    if gh_result.returncode != 0:
        raise ValueError(
            f"gh issue view failed for issue {issue_number}: {gh_result.stderr.strip()}"
        )

    body = gh_result.stdout
    heading_match = _INVESTIGATION_HEADING_RE.search(body)
    if heading_match is None:
        raise ValueError(
            f"no '{_INVESTIGATION_HEADING}' section found in issue {issue_number} body"
        )

    extracted = body[heading_match.end() :]

    if _RECOMMENDATIONS_HEADING_RE.search(extracted) is None:
        raise ValueError(
            f"extracted '{_INVESTIGATION_HEADING}' section lacks "
            f"'{_RECOMMENDATIONS_HEADING}' heading — investigation report is truncated"
        )

    out_path = Path(output_dir) / "investigation_from_issue.md"
    atomic_write(out_path, extracted)
    return {"investigation_report": str(out_path)}
