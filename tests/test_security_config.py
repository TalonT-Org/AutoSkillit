"""Structural tests for security configuration integrity."""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def test_gitleaks_config_exists() -> None:
    """A .gitleaks.toml must exist to suppress known false positives."""
    assert (REPO_ROOT / ".gitleaks.toml").exists(), ".gitleaks.toml not found in repo root"


def test_gitleaks_config_valid_and_has_allowlist() -> None:
    """.gitleaks.toml must be valid TOML and contain [[allowlists]] entries."""
    path = REPO_ROOT / ".gitleaks.toml"
    with open(path, "rb") as fh:
        config = tomllib.load(fh)
    assert "allowlists" in config, "Missing [[allowlists]] entries in .gitleaks.toml"
    allowlists = config["allowlists"]
    assert isinstance(allowlists, list) and len(allowlists) > 0, (
        "[[allowlists]] must define at least one entry"
    )
    assert any(entry.get("regexes") or entry.get("paths") for entry in allowlists), (
        "At least one [[allowlists]] entry must define 'regexes' or 'paths'"
    )


def test_gitleaks_hook_registered() -> None:
    """gitleaks hook must be declared in .pre-commit-config.yaml."""
    content = (REPO_ROOT / ".pre-commit-config.yaml").read_text()
    assert "gitleaks" in content, "gitleaks hook not found in .pre-commit-config.yaml"


def test_claude_md_section5_mentions_gitleaks() -> None:
    """CLAUDE.md Section 5 must document the gitleaks hook."""
    content = (REPO_ROOT / "CLAUDE.md").read_text()
    assert "gitleaks" in content.lower(), "CLAUDE.md does not mention gitleaks — update Section 5"
