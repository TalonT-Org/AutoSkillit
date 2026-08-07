"""Tests for strict Codex CLI version-token normalization."""

from __future__ import annotations

import pytest

from autoskillit.core import normalize_codex_cli_version

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.mark.parametrize("value", ["1.2.3", "codex-cli 1.2.3"])
def test_normalize_codex_cli_version_accepts_exact_supported_forms(value: str) -> None:
    assert normalize_codex_cli_version(value) == "1.2.3"


@pytest.mark.parametrize(
    "value",
    [
        "codex 1.2.3",
        "codex-cli v1.2.3",
        "codex-cli 1.2.3-beta",
        "codex-cli 1.2.3+build",
        "codex-cli 10.1.2.3",
        "codex-cli 1.2.3 ",
        " 1.2.3",
        "1.2",
        "unknown",
    ],
)
def test_normalize_codex_cli_version_rejects_malformed_tokens(value: str) -> None:
    with pytest.raises(ValueError, match="invalid Codex CLI version token"):
        normalize_codex_cli_version(value)


def test_normalized_versions_do_not_allow_suffix_collisions() -> None:
    assert normalize_codex_cli_version("codex-cli 11.2.3") != normalize_codex_cli_version("1.2.3")
