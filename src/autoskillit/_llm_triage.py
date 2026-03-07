"""LLM-assisted triage for contract staleness.

Top-level autoskillit module. Depends on execution/ for subprocess infrastructure
because triage_staleness spawns a Claude CLI process (claude -p) to perform
semantic comparison of SKILL.md changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autoskillit.core import (
    ClaudeFlags,
    OutputFormat,
    SubprocessResult,
    TerminationReason,
    get_logger,
)
from autoskillit.execution import build_headless_cmd, parse_session_result, run_managed_async
from autoskillit.recipe import StaleItem, load_bundled_manifest
from autoskillit.workspace import bundled_skills_dir

logger = get_logger(__name__)

# Characters of SKILL.md content included per skill in a batched prompt.
_SKILL_MD_TRUNCATE = 1500


async def triage_staleness(stale_items: list[StaleItem]) -> list[dict[str, Any]]:
    """Use Haiku to determine if stale contracts changed meaningfully.

    For each stale item with reason="hash_mismatch", reads the current
    SKILL.md and asks Haiku whether the inputs or outputs changed. All
    hash_mismatch items are sent in a single Haiku call via _triage_batch.

    Returns a list of dicts with keys: skill, meaningful (bool), summary (str).
    """
    results: list[dict[str, Any]] = []
    hash_items: list[StaleItem] = []

    # version_mismatch items are always meaningful — no LLM call needed.
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
        elif item.reason == "hash_mismatch":
            hash_items.append(item)

    if not hash_items:
        return results

    # Pre-load all SKILL.md content synchronously before any async task starts.
    # Path.read_text() is blocking I/O and must not run inside async tasks.
    skill_md_cache: dict[str, str] = {}
    for item in hash_items:
        if item.skill not in skill_md_cache:
            skill_md_path = bundled_skills_dir() / item.skill / "SKILL.md"
            if skill_md_path.is_file():
                skill_md_cache[item.skill] = skill_md_path.read_text()

    results.extend(await _triage_batch(hash_items, skill_md_cache))

    return results


async def _triage_batch(
    batch: list[StaleItem], skill_md_cache: dict[str, str]
) -> list[dict[str, Any]]:
    """Triage one batch of hash_mismatch items with a single Haiku subprocess call.

    Items whose SKILL.md is missing are returned immediately as meaningful=True
    without launching a subprocess. The remaining items are sent as a single
    batched prompt.
    """
    triageable: list[StaleItem] = []
    pre_results: list[dict[str, Any]] = []

    for item in batch:
        if item.skill not in skill_md_cache:
            pre_results.append(
                {
                    "skill": item.skill,
                    "meaningful": True,
                    "summary": f"SKILL.md for {item.skill} not found.",
                }
            )
        else:
            triageable.append(item)

    if not triageable:
        return pre_results

    prompt = _build_batch_prompt(triageable, skill_md_cache)

    try:
        fmt = OutputFormat.JSON
        spec = build_headless_cmd(prompt, model="claude-haiku-4-5-20251001")
        triage_cmd = spec.cmd + [ClaudeFlags.OUTPUT_FORMAT, fmt.value]
        for flag in fmt.required_cli_flags:
            if flag not in triage_cmd:
                triage_cmd.append(flag)
        result: SubprocessResult = await run_managed_async(
            cmd=triage_cmd,
            cwd=Path.cwd(),
            timeout=30.0,
            pty_mode=True,
        )
        if result.termination == TerminationReason.TIMED_OUT:
            raise TimeoutError("triage_staleness batch timed out")
        session = parse_session_result(result.stdout)
        if session.is_error or not session.result:
            logger.warning(
                "triage_staleness batch parse failed; treating all as meaningful",
                batch=[i.skill for i in triageable],
            )
            triageable_results = [
                {"skill": i.skill, "meaningful": True, "summary": "Batch triage parse failed."}
                for i in triageable
            ]
        else:
            triageable_results = _parse_batch_response(session.result, triageable)
    except (TimeoutError, OSError):
        logger.warning(
            "triage_staleness batch failed; treating all as meaningful",
            batch=[i.skill for i in triageable],
            exc_info=True,
        )
        triageable_results = [
            {
                "skill": i.skill,
                "meaningful": True,
                "summary": f"Triage failed for {i.skill}; treating as meaningful.",
            }
            for i in triageable
        ]

    return pre_results + triageable_results


def _build_batch_prompt(batch: list[StaleItem], skill_md_cache: dict[str, str]) -> str:
    """Build an XML-structured batch prompt for Haiku skill comparison."""
    manifest = load_bundled_manifest()
    items_xml = ""
    for i, item in enumerate(batch, 1):
        contract_data = manifest.get("skills", {}).get(item.skill, {})
        skill_content = skill_md_cache.get(item.skill, "")[:_SKILL_MD_TRUNCATE]
        items_xml += (
            f'<item index="{i}"><name>{item.skill}</name>\n'
            f"<contract>{json.dumps(contract_data, indent=2)}</contract>\n"
            f"<skill_md>{skill_content}</skill_md></item>\n"
        )

    return (
        "You are comparing stored skill contracts against current SKILL.md content.\n"
        "Analyze each skill independently. Determine if inputs or outputs changed.\n\n"
        f"<items>\n{items_xml}</items>\n\n"
        f"Return a JSON array with exactly {len(batch)} objects preserving index order:\n"
        "[\n"
        '  {"index": 1, "skill": "...", "meaningful_change": true/false, "summary": "..."},\n'
        "  ...\n"
        "]\n"
        "Return JSON only, no other text."
    )


def _parse_batch_response(raw_json: str, batch: list[StaleItem]) -> list[dict[str, Any]]:
    """Parse a batch Haiku response array into per-skill result dicts.

    On length mismatch or complete JSON parse failure, all batch items are
    returned as meaningful=True. On per-item key mismatch only that item is
    marked meaningful=True; valid siblings are preserved.
    """
    fallback = [
        {"skill": i.skill, "meaningful": True, "summary": "batch parse failed"} for i in batch
    ]
    try:
        array = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("triage_staleness batch JSON parse failed")
        return fallback

    if not isinstance(array, list) or len(array) != len(batch):
        logger.warning(
            "triage_staleness batch response length mismatch",
            expected=len(batch),
            got=len(array) if isinstance(array, list) else "not a list",
        )
        return fallback

    results: list[dict[str, Any]] = []
    for item, response in zip(batch, array):
        try:
            if not isinstance(response, dict) or response.get("skill") != item.skill:
                logger.warning(
                    "triage_staleness batch item mismatch",
                    expected=item.skill,
                    got=response.get("skill") if isinstance(response, dict) else response,
                )
                results.append(
                    {"skill": item.skill, "meaningful": True, "summary": "batch item mismatch"}
                )
            else:
                results.append(
                    {
                        "skill": item.skill,
                        "meaningful": bool(response["meaningful_change"]),
                        "summary": response.get("summary", ""),
                    }
                )
        except (KeyError, TypeError):
            results.append(
                {"skill": item.skill, "meaningful": True, "summary": "batch item parse failed"}
            )

    return results
