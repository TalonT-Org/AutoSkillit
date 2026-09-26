"""Tests for cook's source-currency warning renderer (_source_currency_warning)."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli.session._session_cook import _source_currency_warning
from autoskillit.core import SourceCurrency, SourceCurrencyStatus

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

_EXPECTED_DECISIONS: dict[SourceCurrencyStatus, type | None] = {
    SourceCurrencyStatus.CURRENT: None,
    SourceCurrencyStatus.NOT_SOURCE_CHECKOUT: None,
    SourceCurrencyStatus.UNKNOWN: None,
    SourceCurrencyStatus.DIVERGED: str,
    SourceCurrencyStatus.STALE: str,
}


def _currency(
    status: SourceCurrencyStatus,
    *,
    behind_by: int | None = None,
    installed_version: str | None = None,
    checkout_version: str | None = None,
    install_type: str | None = None,
) -> SourceCurrency:
    return SourceCurrency(
        status,
        None,
        None,
        behind_by,
        None,
        installed_version=installed_version,
        checkout_version=checkout_version,
        install_type=install_type,
    )


@pytest.mark.parametrize("status", list(SourceCurrencyStatus))
def test_source_currency_warning_decision_per_status(
    status: SourceCurrencyStatus, tmp_path: Path
) -> None:
    expected = _EXPECTED_DECISIONS[status]
    currency = _currency(status, behind_by=3 if status is SourceCurrencyStatus.STALE else None)

    warning = _source_currency_warning(currency, checkout=tmp_path, color=False)

    if expected is None:
        assert warning is None
    else:
        assert isinstance(warning, expected)


def test_stale_version_based_local_path_names_versions_type_and_remedy(tmp_path: Path) -> None:
    currency = _currency(
        SourceCurrencyStatus.STALE,
        installed_version="1.2.3",
        checkout_version="1.3.0",
        install_type="local-path",
    )

    warning = _source_currency_warning(currency, checkout=tmp_path, color=False)

    assert warning is not None
    assert "1.2.3" in warning
    assert "1.3.0" in warning
    assert "local-path" in warning
    assert "autoskillit update" in warning
    assert f"uv tool install --force --reinstall {tmp_path}" in warning


def test_stale_version_based_unknown_install_type_uses_install_dev_remedy(
    tmp_path: Path,
) -> None:
    currency = _currency(
        SourceCurrencyStatus.STALE,
        installed_version="1.2.3",
        checkout_version="1.3.0",
        install_type="unknown",
    )

    warning = _source_currency_warning(currency, checkout=tmp_path, color=False)

    assert warning is not None
    assert "1.2.3" in warning
    assert "1.3.0" in warning
    assert "unknown" in warning
    assert "task install-dev" in warning
    assert "autoskillit update" not in warning


def test_stale_commit_based_text_is_unchanged(tmp_path: Path) -> None:
    currency = _currency(SourceCurrencyStatus.STALE, behind_by=4)

    warning = _source_currency_warning(currency, checkout=tmp_path, color=False)

    assert warning is not None
    assert "4 commits behind" in warning


def test_stale_commit_based_text_handles_missing_behind_by(tmp_path: Path) -> None:
    """When ``behind_by`` is None, the warning renders an unknown-commits phrase."""
    currency = _currency(SourceCurrencyStatus.STALE, behind_by=None)

    warning = _source_currency_warning(currency, checkout=tmp_path, color=False)

    assert warning is not None
    assert "None" not in warning
    assert "unknown" in warning.lower()
