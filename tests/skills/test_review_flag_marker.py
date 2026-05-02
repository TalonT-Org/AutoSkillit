"""Tests for the REVIEW-FLAG HTML comment marker used in resolve-review replies."""

from __future__ import annotations

import re

REVIEW_FLAG_RE = re.compile(r"<!--\s*REVIEW-FLAG:\s*severity=(\w+)\s+dimension=(\w+)\s*-->")


def test_review_flag_re_matches_discuss_comment():
    body = (
        "Valid observation — flagged for design decision. Evidence.\n"
        "<!-- REVIEW-FLAG: severity=warning dimension=arch -->"
        "\n<!-- autoskillit:resolved comment_id=12345 verdict=DISCUSS -->"
    )
    m = REVIEW_FLAG_RE.search(body)
    assert m is not None
    assert m.group(1) == "warning"
    assert m.group(2) == "arch"


def test_review_flag_re_matches_info_comment():
    body = (
        "Acknowledged — minor suggestion noted.\n"
        "<!-- REVIEW-FLAG: severity=info dimension=slop -->"
        "\n<!-- autoskillit:resolved comment_id=99 verdict=INFO -->"
    )
    m = REVIEW_FLAG_RE.search(body)
    assert m is not None
    assert m.group(1) == "info"
    assert m.group(2) == "slop"


def test_review_flag_re_no_match_on_legacy_comment():
    legacy = (
        "Valid observation — flagged for design decision. Some evidence.\n"
        "<!-- autoskillit:resolved comment_id=42 verdict=DISCUSS -->"
    )
    assert REVIEW_FLAG_RE.search(legacy) is None
