"""Authoritative pull-request review contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._type_github_review import *  # noqa: F403
from ._type_github_review import __all__ as _github_review_all
from ._type_github_review_anchor import *  # noqa: F403
from ._type_github_review_anchor import __all__ as _github_review_anchor_all

if not TYPE_CHECKING:
    __all__ = _github_review_all + _github_review_anchor_all
