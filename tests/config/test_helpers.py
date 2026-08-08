"""Tests for autoskillit.config resolve_ingredient_defaults."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("config"), pytest.mark.medium]


def test_resolve_ingredient_defaults_uses_upstream_when_origin_is_file_url(tmp_path):
    """resolve_ingredient_defaults must return the upstream URL when origin is file://."""
    from autoskillit.config import resolve_ingredient_defaults

    # Create repo with file:// origin and real URL upstream
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", f"file://{tmp_path}/other"], cwd=str(repo), check=True
    )
    subprocess.run(
        ["git", "remote", "add", "upstream", "https://github.com/testowner/testrepo.git"],
        cwd=str(repo),
        check=True,
    )

    defaults = resolve_ingredient_defaults(repo)
    assert defaults.get("source_dir") == "https://github.com/testowner/testrepo.git"


def test_resolve_ingredient_defaults_still_works_with_github_origin(tmp_path):
    """Non-clone context: origin has real GitHub URL — must continue to work."""
    from autoskillit.config import resolve_ingredient_defaults

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/owner/repo.git"],
        cwd=str(repo),
        check=True,
    )
    defaults = resolve_ingredient_defaults(repo)
    assert defaults.get("source_dir") == "https://github.com/owner/repo.git"


def test_resolve_ingredient_defaults_includes_local_review_rounds(tmp_path):
    """T2.3: resolve_ingredient_defaults includes local_review_rounds with default value 2."""
    from autoskillit.config import resolve_ingredient_defaults

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/owner/repo.git"],
        cwd=str(repo),
        check=True,
    )

    defaults = resolve_ingredient_defaults(repo)
    assert defaults.get("local_review_rounds") == "2"


def test_resolve_ingredient_defaults_includes_adversarial_review_level(tmp_path):
    """T2.1: resolve_ingredient_defaults includes adversarial_review_level."""
    from autoskillit.config import resolve_ingredient_defaults

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/owner/repo.git"],
        cwd=str(repo),
        check=True,
    )

    defaults = resolve_ingredient_defaults(repo)
    assert defaults.get("adversarial_review_level") is not None


def test_resolve_ingredient_defaults_includes_pipeline_health(tmp_path):
    """DIAG_C3: resolve_ingredient_defaults includes pipeline_health default false."""
    from autoskillit.config import resolve_ingredient_defaults

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/org/repo"],
        cwd=str(repo),
        check=True,
    )

    defaults = resolve_ingredient_defaults(repo)
    assert defaults.get("pipeline_health") == "false"


def test_resolve_ingredient_defaults_includes_is_fleet_dispatch_false(tmp_path):
    """T1.1: resolve_ingredient_defaults includes is_fleet_dispatch=false with no DISPATCH_ID."""
    from autoskillit.config import resolve_ingredient_defaults

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/org/repo"],
        cwd=str(repo),
        check=True,
    )

    defaults = resolve_ingredient_defaults(repo)
    assert defaults.get("is_fleet_dispatch") == "false"


def test_resolve_ingredient_defaults_includes_is_fleet_dispatch_true(tmp_path, monkeypatch):
    """T1.2: resolve_ingredient_defaults sets is_fleet_dispatch=true when DISPATCH_ID is set."""
    from autoskillit.config import resolve_ingredient_defaults

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/org/repo"],
        cwd=str(repo),
        check=True,
    )
    monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", "test-dispatch-123")

    defaults = resolve_ingredient_defaults(repo)
    assert defaults.get("is_fleet_dispatch") == "true"
    assert defaults.get("dispatch_id") == "test-dispatch-123"


def test_resolve_ingredient_defaults_includes_dispatch_id_empty_when_absent(tmp_path):
    """T1.3: resolve_ingredient_defaults includes dispatch_id empty string when no DISPATCH_ID."""
    from autoskillit.config import resolve_ingredient_defaults

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/org/repo"],
        cwd=str(repo),
        check=True,
    )

    defaults = resolve_ingredient_defaults(repo)
    assert defaults.get("dispatch_id") == ""


def test_resolve_ingredient_defaults_fleet_keys_survive_config_failure(tmp_path, monkeypatch):
    """T1.4: Fleet env-var keys resolve even when load_config() fails."""
    from autoskillit.config import resolve_ingredient_defaults

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/org/repo"],
        cwd=str(repo),
        check=True,
    )
    monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", "dispatch-456")

    with patch(
        "autoskillit.config.settings.load_config",
        side_effect=RuntimeError("config error"),
    ):
        defaults = resolve_ingredient_defaults(repo)

    assert defaults.get("is_fleet_dispatch") == "true"
    assert defaults.get("dispatch_id") == "dispatch-456"


def test_resolve_ingredient_defaults_base_branch_from_config(tmp_path):
    """1d: base_branch must reflect cfg.branching.default_base_branch when config loads."""
    from unittest.mock import MagicMock

    from autoskillit.config import resolve_ingredient_defaults

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/org/repo"],
        cwd=str(repo),
        check=True,
    )
    mock_cfg = MagicMock()
    mock_cfg.branching.default_base_branch = "develop"
    mock_cfg.review.local_review_rounds = 3
    mock_cfg.plan.adversarial_review_level = "aggressive"
    mock_cfg.diagnostics.pipeline_health = False

    with patch("autoskillit.config.settings.load_config", return_value=mock_cfg):
        defaults = resolve_ingredient_defaults(repo)

    assert defaults["base_branch"] == "develop"


def test_server_authoritative_ingredients_covers_resolved_defaults(tmp_path):
    """1a: Every config-resolvable key from resolve_ingredient_defaults must be declared
    in SERVER_AUTHORITATIVE_INGREDIENTS or CONFIG_DEFAULT_INGREDIENTS."""
    from autoskillit.config import (
        CONFIG_DEFAULT_INGREDIENTS,
        SERVER_AUTHORITATIVE_INGREDIENTS,
        resolve_ingredient_defaults,
    )

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    defaults = resolve_ingredient_defaults(tmp_path)
    config_resolvable = {k for k in defaults if k != "source_dir"}
    missing = config_resolvable - SERVER_AUTHORITATIVE_INGREDIENTS - CONFIG_DEFAULT_INGREDIENTS
    assert not missing, (
        f"Config-resolvable keys missing from SERVER_AUTHORITATIVE_INGREDIENTS or "
        f"CONFIG_DEFAULT_INGREDIENTS: {missing}"
    )


def test_config_authority_keys_superset_of_server_authoritative() -> None:
    """CONFIG_AUTHORITY_KEYS in core must be a superset of SERVER_AUTHORITATIVE_INGREDIENTS."""
    from autoskillit.config.ingredient_defaults import SERVER_AUTHORITATIVE_INGREDIENTS
    from autoskillit.core import CALLER_SOVEREIGN_INGREDIENTS, CONFIG_AUTHORITY_KEYS

    assert SERVER_AUTHORITATIVE_INGREDIENTS <= CONFIG_AUTHORITY_KEYS
    assert CONFIG_AUTHORITY_KEYS - SERVER_AUTHORITATIVE_INGREDIENTS == CALLER_SOVEREIGN_INGREDIENTS


def test_caller_sovereign_ingredients_partition_config_authority_keys() -> None:
    """CALLER_SOVEREIGN_INGREDIENTS must partition CONFIG_AUTHORITY_KEYS together with
    SERVER_AUTHORITATIVE_INGREDIENTS: non-empty, disjoint, and union-complete."""
    from autoskillit.config.ingredient_defaults import SERVER_AUTHORITATIVE_INGREDIENTS
    from autoskillit.core import CALLER_SOVEREIGN_INGREDIENTS, CONFIG_AUTHORITY_KEYS

    assert isinstance(CALLER_SOVEREIGN_INGREDIENTS, frozenset)
    assert CALLER_SOVEREIGN_INGREDIENTS
    assert CONFIG_AUTHORITY_KEYS == SERVER_AUTHORITATIVE_INGREDIENTS | CALLER_SOVEREIGN_INGREDIENTS
    assert SERVER_AUTHORITATIVE_INGREDIENTS & CALLER_SOVEREIGN_INGREDIENTS == frozenset()
    assert "source_dir" in CALLER_SOVEREIGN_INGREDIENTS


def test_apply_config_authoritative_overrides_unknown_key_retains_caller_value(tmp_path):
    """A config-authority key not in SERVER_AUTHORITATIVE_INGREDIENTS or
    BACKEND_CAPABILITY_INGREDIENTS is caller-sovereign — the caller-supplied value
    is retained silently (no warning)."""
    from types import SimpleNamespace

    import structlog.testing

    from autoskillit.config import apply_config_authoritative_overrides

    recipe_ingredients = {
        "totally_unknown_key": SimpleNamespace(authority="config"),
    }

    with (
        patch(
            "autoskillit.config.ingredient_defaults.resolve_ingredient_defaults",
            return_value={},
        ),
        structlog.testing.capture_logs() as cap_logs,
    ):
        result = apply_config_authoritative_overrides(
            {"totally_unknown_key": "caller-value"},
            recipe_ingredients,
            tmp_path,
        )

    assert result["totally_unknown_key"] == "caller-value"
    assert not any("config-authority key" in e.get("event", "") for e in cap_logs)


# T4: REQ-ING-003
def test_pipeline_health_not_server_authoritative() -> None:
    """pipeline_health must be a config-default, not server-authoritative."""
    from autoskillit.config import SERVER_AUTHORITATIVE_INGREDIENTS

    assert "pipeline_health" not in SERVER_AUTHORITATIVE_INGREDIENTS


# T6: REQ-ING-005
def test_pipeline_health_in_config_default_ingredients() -> None:
    """pipeline_health must be in CONFIG_DEFAULT_INGREDIENTS."""
    from autoskillit.config import CONFIG_DEFAULT_INGREDIENTS

    assert "pipeline_health" in CONFIG_DEFAULT_INGREDIENTS
