"""Never-raises public boundary for GitHub review publication."""

from __future__ import annotations

from typing import TYPE_CHECKING

from autoskillit.core import (
    GitHubReviewPostResult,
    GitHubReviewRequest,
    ReviewOperationState,
    get_logger,
)

if TYPE_CHECKING:
    from .poster import DefaultGitHubReviewPoster

logger = get_logger(__name__)


async def post(
    poster: DefaultGitHubReviewPoster,
    request: GitHubReviewRequest,
) -> GitHubReviewPostResult:
    """Serialize one call and translate ordinary failures into durable outcomes."""

    async with poster._instance_lock:
        try:
            return await poster._post(request)
        except (TypeError, ValueError) as exc:
            return GitHubReviewPostResult(
                operation_key="",
                head_sha=request.head_sha,
                state=ReviewOperationState.TERMINAL,
                planned_comment_count=len(request.comments),
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception as exc:
            logger.error("github_review_post_failed", exc_info=True)
            return GitHubReviewPostResult(
                operation_key="",
                head_sha=request.head_sha,
                state=ReviewOperationState.AMBIGUOUS,
                planned_comment_count=len(request.comments),
                error=f"{type(exc).__name__}: {exc}",
            )
