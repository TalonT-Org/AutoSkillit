"""GitHub validation diagnostics retain bounded response details."""

import json

import pytest

from autoskillit.execution.github_ops import _github_http
from tests.fixtures.github_rest import reviews_create_422_author_approve

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_error_detail_renders_string_errors() -> None:
    assert _github_http.github_error_detail(reviews_create_422_author_approve()) == (
        "Unprocessable Entity: Review Can not approve your own pull request"
    )


def test_error_detail_renders_object_errors() -> None:
    error = {"resource": "Issue", "field": "title", "code": "missing_field"}
    assert _github_http.github_error_detail(
        {"message": "Validation Failed", "errors": [error]}
    ) == "Validation Failed: " + json.dumps(error, sort_keys=True)


def test_error_detail_is_bounded() -> None:
    detail = _github_http.github_error_detail(
        {"message": "Validation Failed", "errors": ["x" * 1000] * 50}
    )
    assert detail is not None and len(detail) <= 500
    assert _github_http.github_error_detail({"message": "Unprocessable Entity"}) == (
        "Unprocessable Entity"
    )
