"""Commit-tool support for validating and reporting self-revert scans."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from autoskillit.core import SubprocessRunner, get_logger

__all__ = ["scan_self_reverts", "validate_self_revert_base"]

logger = get_logger(__name__)

_CommandRunner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]
_OutputCombiner = Callable[[str, str, str], str]
_SelfRevertDetector = Callable[[SubprocessRunner, str, str, str], Awaitable[dict[str, object]]]


async def validate_self_revert_base(
    run_command: _CommandRunner,
    cwd: str,
    base_sha: str,
    combine_output: _OutputCombiner,
) -> tuple[str | None, str | None]:
    """Resolve a base commit and prove it is an ancestor before git mutation."""
    rc, stdout, stderr = await run_command(
        ["git", "-C", cwd, "rev-parse", f"{base_sha}^{{commit}}"]
    )
    base_ref = stdout.strip()
    if rc != 0 or not base_ref:
        return None, (
            "self-revert base validation failed: "
            + combine_output(
                stderr,
                stdout,
                f"git rev-parse exited with status {rc}",
            )
        )
    rc, _, _ = await run_command(
        ["git", "-C", cwd, "merge-base", "--is-ancestor", base_ref, "HEAD"]
    )
    if rc != 0:
        return None, "self-revert base validation failed: base is not an ancestor of HEAD"
    return base_ref, None


async def scan_self_reverts(
    detector: _SelfRevertDetector,
    runner: SubprocessRunner,
    cwd: str,
    base_ref: str,
    head_ref: str,
) -> dict[str, object]:
    """Keep an already-landed commit successful when its advisory scan fails."""
    try:
        return await detector(runner, cwd, base_ref, head_ref)
    except Exception as exc:
        logger.error("commit_files self-revert scan failed", exc_info=True)
        return {
            "pairs": [],
            "complete": False,
            "scan_error": f"self-revert scan failed: {type(exc).__name__}",
        }
