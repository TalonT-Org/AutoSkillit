"""REQ-R741-A01 / REQ-R741-A04 — Vendored mermaid.min.js presence and size."""

from __future__ import annotations

from pathlib import Path

_MERMAID_DIR = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "assets" / "mermaid"


def test_mermaid_min_js_exists_and_nontrivial() -> None:
    """mermaid.min.js must exist at the vendored path and be > 1 MB."""
    asset = _MERMAID_DIR / "mermaid.min.js"
    assert asset.exists(), (
        f"mermaid.min.js not vendored at {asset}; run: task vendor-mermaid"
    )
    size = asset.stat().st_size
    assert size > 1_000_000, (
        f"mermaid.min.js is suspiciously small ({size} bytes); expected > 1 MB. "
        "Botched vendor pass? Re-run: task vendor-mermaid"
    )


def test_mermaid_license_file_exists() -> None:
    """LICENSE.mermaid must accompany the vendored bundle (MIT attribution)."""
    lic = _MERMAID_DIR / "LICENSE.mermaid"
    assert lic.exists(), f"LICENSE.mermaid missing from {_MERMAID_DIR}"
    assert lic.read_text().strip(), "LICENSE.mermaid is empty"


def test_mermaid_version_file_is_v11() -> None:
    """VERSION file must exist and declare a mermaid 11.x release."""
    ver = _MERMAID_DIR / "VERSION"
    assert ver.exists(), f"VERSION file missing from {_MERMAID_DIR}"
    content = ver.read_text().strip()
    assert content, "VERSION file is empty"
    assert content.startswith("11."), (
        f"Expected mermaid 11.x, got {content!r}. Re-run: task vendor-mermaid"
    )
