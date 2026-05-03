"""Structural contracts for the token summary pipeline."""

from __future__ import annotations

import inspect

import pytest

from autoskillit.smoke_utils import patch_pr_token_summary

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_patch_pr_token_summary_uses_order_id_not_cwd_filter():
    """Structural contract: patch_pr_token_summary must accept order_id parameter.

    The presence of order_id in the signature is the canonical guard against regression
    to cwd_filter-based scoping. If someone removes it, this test fails immediately.
    """
    sig = inspect.signature(patch_pr_token_summary)
    assert "order_id" in sig.parameters, (
        "patch_pr_token_summary must have an 'order_id' parameter. "
        "This is the canonical scoping key for multi-clone pipelines. "
        "Do not remove it or replace it with cwd_filter."
    )


def test_patch_pr_token_summary_cwd_is_optional():
    """cwd must have a default value so callers can omit it when using order_id."""
    sig = inspect.signature(patch_pr_token_summary)
    assert "cwd" in sig.parameters
    cwd_param = sig.parameters["cwd"]
    assert cwd_param.default == "", (
        "cwd must default to '' so fleet callers can omit it and rely on order_id scoping."
    )
