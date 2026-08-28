from __future__ import annotations

import ast

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


REVIEW_FUNCTION_ANCHORS: dict[str, str] = {
    "build_malformed_review_envelope": "_validation",
    "validate_experimental_auditor_outputs": "_validation",
    "deletion_regression_is_eligible": "_validation",
    "aggregate_combined_review_candidates": "_aggregation",
    "determine_experimental_review_verdict": "_aggregation",
    "render_review_finding_body": "_publication",
    "normalize_local_review_finding": "_publication",
    "prepare_experimental_review_publication": "_publication",
    "publish_experimental_review_artifacts": "_publication",
}


def test_smoke_utils_review_subpackage_is_a_package() -> None:
    """REQ-CNST-010-DECOMPOSE-4: #4855 splits _experimental_review.py into a
    smoke_utils/review/ sub-package of focused shards."""
    review = SRC_ROOT / "smoke_utils" / "review"
    assert not (SRC_ROOT / "smoke_utils" / "_experimental_review.py").exists(), (
        "_experimental_review.py must be removed (replaced by smoke_utils/review/ package)"
    )
    assert (review / "__init__.py").exists(), (
        "smoke_utils/review/__init__.py must exist as a regular package marker"
    )
    for shard in ("_constants.py", "_validation.py", "_aggregation.py", "_publication.py"):
        assert (review / shard).exists(), (
            f"smoke_utils/review/{shard} must exist as a private shard"
        )


def test_smoke_utils_review_facade_re_exports_contract() -> None:
    """REQ-CNST-010-DECOMPOSE-4: smoke_utils facade must re-export every public
    symbol from smoke_utils/review/ as the same object identity AND each symbol
    must be owned by its expected shard."""
    import importlib

    facade = importlib.import_module("autoskillit.smoke_utils")
    review = importlib.import_module("autoskillit.smoke_utils.review")
    declared = set(getattr(review, "__all__", ()))
    assert declared, "smoke_utils/review/__all__ is empty or missing"

    missing_from_facade = sorted(name for name in declared if not hasattr(facade, name))
    assert not missing_from_facade, f"smoke_utils facade does not re-export: {missing_from_facade}"
    identity_mismatch = sorted(
        name for name in declared if getattr(facade, name) is not getattr(review, name)
    )
    assert not identity_mismatch, (
        f"smoke_utils facade re-exports {identity_mismatch} as a different object "
        f"than smoke_utils.review"
    )

    # Shard-ownership assertions: each public symbol must be DEFINED (not merely
    # imported) in its declared shard module. Catches a misplacement such as
    # render_review_finding_body accidentally defined in _validation.py
    # instead of _publication.py — checking identity alone passes when the
    # function is re-exported from another shard.
    for name, expected_shard in REVIEW_FUNCTION_ANCHORS.items():
        shard_module = importlib.import_module(f"autoskillit.smoke_utils.review.{expected_shard}")
        assert hasattr(shard_module, name), (
            f"{name} must be defined in {expected_shard}.py per REVIEW_FUNCTION_ANCHORS"
        )
        symbol = getattr(shard_module, name)
        assert getattr(symbol, "__module__", "") == (
            f"autoskillit.smoke_utils.review.{expected_shard}"
        ), (
            f"{name} should be defined in {expected_shard}.py "
            f"but is defined in {getattr(symbol, '__module__', 'unknown')}"
        )
        assert getattr(facade, name) is symbol, (
            f"{name} is declared in {expected_shard}.py but facade resolves to a different object"
        )


def test_smoke_utils_review_shards_do_not_import_the_facade() -> None:
    """REQ-CNST-010-DECOMPOSE-4: shards must not import the parent facade (avoids
    re-entrant facade imports)."""

    review = SRC_ROOT / "smoke_utils" / "review"
    offenders: list[str] = []
    for path in review.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                # Sibling submodule imports (autoskillit.smoke_utils.review._X)
                # are allowed. Direct parent facade imports are forbidden.
                if node.module == "autoskillit.smoke_utils":
                    offenders.append(f"{path.name} imports from autoskillit.smoke_utils facade")
                elif node.module == "autoskillit" and any(
                    alias.name == "smoke_utils" for alias in node.names
                ):
                    offenders.append(
                        f"{path.name} imports autoskillit.smoke_utils via autoskillit alias"
                    )
                elif (node.level or 0) >= 2:
                    # Relative parent import (`from .. import ...` reaches
                    # autoskillit.smoke_utils when invoked from review/*).
                    offenders.append(
                        f"{path.name} uses relative parent import (level={node.level})"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "autoskillit.smoke_utils":
                        offenders.append(f"{path.name} imports autoskillit.smoke_utils facade")
                    elif alias.name == "autoskillit.smoke_utils.review":
                        offenders.append(
                            f"{path.name} imports autoskillit.smoke_utils.review facade"
                        )
    assert not offenders, "Shards must not import the smoke_utils facade:\n" + "\n".join(
        f"  {o}" for o in offenders
    )


def test_smoke_utils_review_shard_sizes_are_balanced() -> None:
    """REQ-CNST-010-DECOMPOSE-4: every shard in smoke_utils/review/ is at most
    750 lines per the issue #4855 acceptance criterion ('every extracted source
    module is at most 750 lines'). The facade ``__init__.py`` and the shared
    constants shard are exempt from the 25-line substance floor — both are
    intentionally narrow surfaces (declarative API + shared constants/helpers)."""
    review = SRC_ROOT / "smoke_utils" / "review"
    too_small = [
        path.name
        for path in review.glob("*.py")
        if path.name not in ("__init__.py", "_constants.py")
        and len(path.read_text().splitlines()) < 25
    ]
    too_large = [
        path.name for path in review.glob("*.py") if len(path.read_text().splitlines()) > 750
    ]
    assert not too_small, f"smoke_utils/review/ shards below 25 lines: {too_small}"
    assert not too_large, f"smoke_utils/review/ shards above 750 lines: {too_large}"
