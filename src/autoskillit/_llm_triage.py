"""LLM-assisted triage for contract staleness.

This module explicitly depends on process_lifecycle for subprocess infrastructure
because triage_staleness spawns a Claude CLI process (claude -p) to perform
semantic comparison of SKILL.md changes. This is the intended home for all
LLM subprocess calls that support the contract validation system.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autoskillit.core.logging import get_logger
from autoskillit.core.types import SubprocessResult, TerminationReason
from autoskillit.execution.process import run_managed_async
from autoskillit.execution.session import parse_session_result
from autoskillit.recipe.contracts import StaleItem, load_bundled_manifest
from autoskillit.workspace.skills import bundled_skills_dir

logger = get_logger(__name__)


async def triage_staleness(stale_items: list[StaleItem]) -> list[dict[str, Any]]:
    """Use Haiku to determine if stale contracts changed meaningfully.

    For each stale item with reason="hash_mismatch", reads the current
    SKILL.md and asks Haiku whether the inputs or outputs changed.

    Returns a list of dicts with keys: skill, meaningful (bool), summary (str).
    """
    results: list[dict[str, Any]] = []
    skill_md_cache: dict[str, str] = {}

    for item in stale_items:
        if item.reason == "version_mismatch":
            results.append(
                {
                    "skill": item.skill,
                    "meaningful": True,
                    "summary": (
                        f"Manifest version changed from {item.stored_value} "
                        f"to {item.current_value}. Structure may have changed."
                    ),
                }
            )
            continue

        if item.reason != "hash_mismatch":
            continue

        skill_md_path = bundled_skills_dir() / item.skill / "SKILL.md"
        if not skill_md_path.is_file():
            results.append(
                {
                    "skill": item.skill,
                    "meaningful": True,
                    "summary": f"SKILL.md for {item.skill} not found.",
                }
            )
            continue

        if item.skill not in skill_md_cache:
            skill_md_cache[item.skill] = skill_md_path.read_text()
        skill_content = skill_md_cache[item.skill]

        manifest = load_bundled_manifest()
        contract_data = manifest.get("skills", {}).get(item.skill, {})

        prompt = (
            f"Compare the stored skill contract with the current SKILL.md content.\n\n"
            f"Stored contract:\n{json.dumps(contract_data, indent=2)}\n\n"
            f"Current SKILL.md:\n{skill_content[:3000]}\n\n"
            f"Did the inputs or outputs change? Respond with JSON only: "
            f'{{"meaningful_change": true/false, "summary": "brief explanation"}}'
        )

        try:
            result: SubprocessResult = await run_managed_async(
                cmd=["claude", "-p", prompt, "--model", "haiku", "--output-format", "json"],
                cwd=Path.cwd(),
                timeout=30.0,
                pty_mode=True,
            )
            if result.termination == TerminationReason.TIMED_OUT:
                raise TimeoutError(f"triage_staleness timed out for skill {item.skill!r}")
            session = parse_session_result(result.stdout)
            if session.is_error or not session.result:
                logger.warning(
                    "triage_staleness parse failed; treating as meaningful", skill=item.skill
                )
                results.append(
                    {
                        "skill": item.skill,
                        "meaningful": True,
                        "summary": f"Triage parse failed for {item.skill!r}.",
                    }
                )
            else:
                try:
                    data = json.loads(session.result)
                    results.append(
                        {
                            "skill": item.skill,
                            "meaningful": bool(data["meaningful_change"]),
                            "summary": data.get("summary", ""),
                        }
                    )
                except (json.JSONDecodeError, KeyError):
                    logger.warning(
                        "triage_staleness result parse failed; treating as meaningful",
                        skill=item.skill,
                    )
                    results.append(
                        {
                            "skill": item.skill,
                            "meaningful": True,
                            "summary": f"Result parse failed for {item.skill!r}.",
                        }
                    )
        except (TimeoutError, OSError):
            logger.warning(
                "triage_staleness failed; treating skill as meaningful",
                skill=item.skill,
                exc_info=True,
            )
            results.append(
                {
                    "skill": item.skill,
                    "meaningful": True,
                    "summary": f"Triage failed for {item.skill}; treating as meaningful.",
                }
            )

    return results
