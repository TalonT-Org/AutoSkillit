"""Investigation extraction for the remediation bridge_investigation step."""

from __future__ import annotations

from pathlib import Path

import regex as re

from autoskillit.core import atomic_write, run_gh

_INVESTIGATION_HEADING = "## Investigation"
_INVESTIGATION_HEADING_RE = re.compile(r"^" + re.escape(_INVESTIGATION_HEADING), re.MULTILINE)
# A structured report carries at least one ``## `` subsection. This is a property of the
# content, not of any particular heading vocabulary — see _is_structured.
_SUBSECTION_RE = re.compile(r"^## ", re.MULTILINE)


def _is_structured(text: str) -> bool:
    """Whether ``text`` looks like a structured report rather than a bare stub.

    The defect this guards (#4381) produced a preamble of a few lines — a marker plus a
    one-line note — with no subsections at all. Requiring at least one ``## `` subsection
    rejects exactly that shape while remaining agnostic about which headings a report
    uses. An earlier revision required a literal ``## Recommendations`` heading; that
    proxied completeness on one vocabulary and rejected 16 of 34 real investigations
    (#4392).
    """
    return _SUBSECTION_RE.search(text) is not None


def extract_investigation(
    investigation_path: str = "",
    issue_number: str = "",
    output_dir: str = "",
) -> dict[str, str]:
    """Resolve the effective investigation path for rectify.

    Called by run_python from the bridge_investigation step in remediation.yaml.
    When investigation_path points to a structured report, validates and returns it.

    Otherwise, fetches the issue body and extracts the ``## Investigation`` section to
    end-of-body. Because extraction runs to end-of-body, the section itself cannot be
    truncated; what must still be established is that it *carries* the analysis.

    Two shapes occur in practice. Most issues put the report under the heading, and the
    extracted section is handed to rectify. A sizeable minority use the heading as an
    attestation — "prior investigation completed interactively … included above" — with
    the analysis earlier in the body. Failing those would halt the pipeline over a
    formatting convention, so the whole body is handed over instead: it demonstrably
    contains the analysis, and rectify is better served by more context than by none.
    """
    if investigation_path and Path(investigation_path).is_file():
        content = Path(investigation_path).read_text()
        if not _is_structured(content):
            raise ValueError(
                f"investigation_path file has no '## ' subsections, so it carries no "
                f"structured investigation: {investigation_path}"
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

    if _is_structured(extracted):
        payload = extracted
    elif _is_structured(body[: heading_match.start()]):
        # Attestation-style section: the heading records that an investigation happened
        # and the analysis sits *above* it. Hand rectify the whole body rather than a
        # three-line note, and rather than halting the pipeline (#4392). The test is
        # deliberately against the text preceding the heading — testing the whole body
        # would match the ``## Investigation`` heading itself and always pass.
        payload = body
    else:
        raise ValueError(
            f"issue {issue_number} has a '{_INVESTIGATION_HEADING}' heading but neither "
            f"the section nor the text above it contains any '## ' subsections — there "
            f"is no investigation to hand to rectify"
        )

    out_path = Path(output_dir) / "investigation_from_issue.md"
    atomic_write(out_path, payload)
    return {"investigation_report": str(out_path)}
