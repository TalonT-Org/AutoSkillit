"""Verify audit-trail-format.md exists and documents the audit/ structure."""

from pathlib import Path

AUDIT_FORMAT_PATH = Path("docs/research/audit-trail-format.md")


def test_audit_trail_format_doc_exists():
    assert AUDIT_FORMAT_PATH.exists()


def test_format_doc_documents_audit_directory():
    content = AUDIT_FORMAT_PATH.read_text()
    assert "audit/" in content
    assert "design-review-dashboard.md" in content
    assert "visualization-plan-trace.md" in content
