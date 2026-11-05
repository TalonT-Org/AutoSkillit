#!/usr/bin/env python3
"""PreToolUse hook: protect checked-out refs and block destructive headless Git.

Blocks commit --amend, push --force, reset --hard, clean -f, checkout .
and related operations that rewrite history or destroy uncommitted changes.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)  # hooks/
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


_GUARDS_DIR = str(Path(__file__).resolve().parent)  # hooks/guards/
if _GUARDS_DIR not in sys.path:
    sys.path.insert(0, _GUARDS_DIR)

from _classification import _option_grammar  # noqa: E402
from _command_classification import (  # noqa: E402
    WriteTargetScan,
    command_verb_and_args,
    extract_interpreter_command_payloads,
    extract_interpreter_write_paths,
    live_command_text,
    scan_write_targets,
)
from _git_command_classification import (  # noqa: E402
    _classify_git_invocation,
    _git_result,
    _git_text,
    _parse_worktree_owners,
    _resolve_attempted_sha,
)
from _git_option_tables import _SED_FLAG_SPEC  # noqa: E402
from _git_outcome import GitHeadEffect, is_ref_mutation  # noqa: E402
from _git_target import (  # noqa: E402
    _GIT_BUILTIN_SUBCOMMANDS,
    _GIT_MUTATION_SUBCOMMANDS,
    ChannelFlags,
    GitSegmentFacts,
    GitTarget,
    GitTargetKind,
    _mentions_git_mutation,
    _mutation_names,
    analyze_git_segment,
    command_channel_flags,
    contains_blocked_git_op,
    resolve_git_target_for_path,
)
from _github_mutation_analysis import (  # noqa: E402
    _DYNAMIC_SHELL_TOKEN_RE,
)
from _hook_constants import (  # noqa: E402
    DENY_REASON_BY_GUARD,
    DENY_TRIGGER_BY_GUARD,
    EXEMPT_SKILLS_BY_GUARD,
)
from _hook_constants import (  # noqa: E402
    RISKY_GIT_OPERATIONS as _BLOCKED_GIT_OPS,
)
from _hook_output import block, deny_tool_use  # noqa: E402
from _hook_payload import (  # noqa: E402
    parse_hook_command,
    resolve_state_root,
)
from _hook_settings import (  # noqa: E402
    hook_is_interactive_cook,
    hook_is_launched_interactive,
    hook_session_shape,
    read_merged_hook_config,
)
from _path_identity import destination_location  # noqa: E402
from _shell_segment_walk import WalkedSegment, walk_evaluated_segments  # noqa: E402

GIT_OPS_DENY_TRIGGER: str = DENY_TRIGGER_BY_GUARD["git_ops_guard"]
CHECKED_OUT_REF_DENY_PREFIX: str = "Checked-out ref mutation blocked: "
PRIMARY_HEAD_DENY_PREFIX: str = "Primary checkout HEAD move blocked: "
_LOGGER = logging.getLogger(__name__)  # noqa: TID251 - standalone stdlib guard

_DENY_REASON_TEMPLATE = DENY_REASON_BY_GUARD["git_ops_guard"]

_EXEMPT_SKILLS: frozenset[str] = EXEMPT_SKILLS_BY_GUARD["git_ops_guard"]
# Orchestrators remain subject to the all-session ref preflight, then bypass
# only the destructive headless operation phase below.
_DESTRUCTIVE_OP_EXEMPT_TIERS: frozenset[str] = frozenset({"orchestrator"})

_RAW_WRITE_VERBS = frozenset({"cp", "mv", "tee", "truncate", "rm", "unlink", "sed", "dd"})
_PACK_VERBS = frozenset({"git-receive-pack", "git-upload-pack"})
_READ_ONLY_SUBCOMMANDS = _GIT_BUILTIN_SUBCOMMANDS - _GIT_MUTATION_SUBCOMMANDS

# Commands that mention none of these cannot reach a Git ref or repository
# classification, so they skip the preflight entirely. This bounds the blast
# radius of the classifier; it is not a security boundary.
_GIT_RELEVANT_RE = re.compile(
    r"(?<![A-Za-z0-9])git(?![A-Za-z0-9])|refs/|packed-refs|worktrees/|\bHEAD\b"
    r"|\bGIT_(?:DIR|COMMON_DIR|WORK_TREE)\b"
)
_REF_STORAGE_RE = re.compile(
    r"(?<![\w.])\.git(?![\w-])|refs/|packed-refs|logs/HEAD|worktrees/|\bHEAD\b"
    r"|\bGIT_(?:DIR|COMMON_DIR|WORK_TREE)\b"
)
_INTERPRETER_WRITE_RE = re.compile(
    r"\bopen\s*\(|\.write_text\s*\(|\.write_bytes\s*\(|\bos\.(?:rename|replace|link|symlink)\s*\("
    r"|\bshutil\.(?:copy\w*|move)\s*\("
)
_PYTHON_VERB_RE = re.compile(r"python(?:3(?:\.\d+)?)?")


def _context_for(target: GitTarget, cache: dict[Any, Any]) -> dict[str, object]:
    key = ("owners", target.cwd)
    if key not in cache:
        worktrees = _git_result(target.cwd, "worktree", "list", "--porcelain", "-z")
        if worktrees.returncode != 0:
            raise RuntimeError(f"git worktree list failed in {target.cwd}")
        cache[key] = _parse_worktree_owners(worktrees.stdout)
    return {
        "common_git_dir": target.common_git_dir,
        "execution_cwd": target.cwd,
        "owners": cache[key],
        "requesting_worktree_path": target.toplevel or target.cwd,
        "worktree_git_dir": target.worktree_git_dir,
    }


def _all_threatened(context: dict[str, object]) -> list[dict[str, object]]:
    owners = context["owners"]
    if not isinstance(owners, dict):
        raise TypeError("context['owners'] must be a dict")
    rows: list[dict[str, object]] = []
    for target_ref in sorted(owners):
        owner_paths = owners[target_ref]
        if not isinstance(owner_paths, list):
            raise TypeError("owner_paths must be a list")
        rows.append(
            {
                "old_sha": _git_text(str(context["execution_cwd"]), "rev-parse", target_ref),
                "owner_paths": owner_paths,
                "target_ref": target_ref,
            }
        )
    return rows


def _threatened_for_target(context: dict[str, object], target_ref: str) -> list[dict[str, object]]:
    if target_ref == "HEAD":
        return [
            {
                "old_sha": _git_text(str(context["execution_cwd"]), "rev-parse", "HEAD"),
                "owner_paths": [str(context["requesting_worktree_path"])],
                "target_ref": "HEAD",
            }
        ]
    owners = context["owners"]
    if not isinstance(owners, dict):
        raise TypeError("context['owners'] must be a dict")
    owner_paths = owners.get(target_ref)
    if not isinstance(owner_paths, list):
        return []
    return [
        {
            "old_sha": _git_text(str(context["execution_cwd"]), "rev-parse", target_ref),
            "owner_paths": owner_paths,
            "target_ref": target_ref,
        }
    ]


def _ctx_str(context: dict[str, object] | None, key: str) -> str:
    """Return str(context[key]) when context has the key, else ""."""
    if context is None:
        return ""
    return str(context.get(key, ""))


def _deny_checked_out_ref(
    *,
    data: dict[str, object],
    context: dict[str, object] | None,
    attempted_value: str,
    threatened_refs: list[dict[str, object]],
) -> None:
    details = {
        "attempted_value": attempted_value,
        "common_git_dir": _ctx_str(context, "common_git_dir"),
        "execution_cwd": _ctx_str(context, "execution_cwd"),
        "requesting_worktree_path": _ctx_str(context, "requesting_worktree_path"),
        "resolved_attempted_new_sha": (
            _resolve_attempted_sha(_ctx_str(context, "execution_cwd"), attempted_value)
            if context
            else ""
        ),
        "session_id": str(data.get("session_id", "")),
        "threatened_refs": threatened_refs,
        "worktree_git_dir": _ctx_str(context, "worktree_git_dir"),
    }
    reason = CHECKED_OUT_REF_DENY_PREFIX + json.dumps(
        details, sort_keys=True, separators=(",", ":")
    )
    deny_tool_use(reason)


def _deny_unresolved(data: dict[str, object]) -> None:
    _deny_checked_out_ref(
        data=data, context=None, attempted_value="<unresolved>", threatened_refs=[]
    )


def _deny_for_mutations(
    data: dict[str, object], context: dict[str, object], mutations: list[tuple[str, str, bool]]
) -> None:
    for target_ref, attempted_value, ambiguous in mutations:
        threatened = (
            _all_threatened(context) if ambiguous else _threatened_for_target(context, target_ref)
        )
        if threatened:
            _deny_checked_out_ref(
                data=data,
                context=context,
                attempted_value=attempted_value,
                threatened_refs=threatened,
            )


def _deny_ambiguous(data: dict[str, object], execution_cwd: str, cache: dict[Any, Any]) -> None:
    """Deny an unattributable mutation against every owned ref of the cwd's repository."""
    target = resolve_git_target_for_path(execution_cwd, cache) if execution_cwd else None
    if target is not None and target.kind == GitTargetKind.RESOLVED:
        _deny_for_mutations(data, _context_for(target, cache), [("", "<unresolved>", True)])
        return
    _deny_unresolved(data)


def _classify_raw_write_target(
    target: Path, common: Path, worktree_git: Path
) -> tuple[str, str, bool] | None:
    def _relative_to(root: Path) -> Path | None:
        try:
            return target.relative_to(root)
        except ValueError:
            return None

    if target == worktree_git / "HEAD" or target == common / "HEAD":
        return ("HEAD", "<unresolved>", False)
    if target == common / "packed-refs":
        return ("", "<unresolved>", True)
    relative = _relative_to(common / "worktrees")
    if relative is not None:
        if relative.parts and len(relative.parts) == 2 and relative.parts[1] == "HEAD":
            return ("", "<unresolved>", True)
        if len(relative.parts) >= 2 and relative.parts[1] == "refs":
            return ("", "<unresolved>", True)
    relative = _relative_to(common / "refs" / "heads")
    if relative is None:
        return None
    return (f"refs/heads/{relative.as_posix()}", "<unresolved>", False)


def _verb_write_candidates(verb: str, args: list[str]) -> list[str]:
    if verb == "dd":
        return [token.split("=", 1)[1] for token in args if token.startswith("of=")]
    if verb == "sed":
        parsed = _option_grammar.parse_options(args, _SED_FLAG_SPEC)
        in_place = _option_grammar.last_option(parsed, "in-place") is not None
        return args[-1:] if parsed.unresolved or in_place else []
    return args[-1:]


def _single_python_cwd(walk: list[WalkedSegment]) -> str:
    cwds = [
        seg.verb_cwd if seg.cwd_certain else ""
        for seg in walk
        if seg.start is not None
        and _PYTHON_VERB_RE.fullmatch(os.path.basename(seg.executable[seg.start]))
    ]
    return cwds[0] if len(cwds) == 1 else ""


def _raw_write_candidates(
    text: str, walk: list[WalkedSegment], scan: WriteTargetScan, execution_cwd: str
) -> tuple[list[tuple[str, str, bool]], bool]:
    """Raw write targets as ``(path, cwd, is_entry)``, and whether any is unresolvable."""
    targets = [(target, execution_cwd, False) for target in scan.targets]
    targets.extend((target, execution_cwd, True) for target in scan.entry_targets)
    ambiguous = scan.unresolved
    for seg in walk:
        if seg.start is None or seg.handled_builtin:
            continue
        verb, args = command_verb_and_args(seg.executable)
        verb = os.path.basename(verb)
        if verb not in _RAW_WRITE_VERBS:
            continue
        for candidate in _verb_write_candidates(verb, args):
            if _DYNAMIC_SHELL_TOKEN_RE.search(candidate):
                ambiguous = True
            elif candidate:
                targets.append((candidate, seg.verb_cwd if seg.cwd_certain else "", False))
    # None: not an interpreter write; []: a write whose path is not a literal.
    interpreter_paths = extract_interpreter_write_paths(text) or []
    if not interpreter_paths and _INTERPRETER_WRITE_RE.search(text):
        ambiguous = True
    interpreter_cwd = _single_python_cwd(walk)
    targets.extend((path, interpreter_cwd, False) for path in interpreter_paths)
    return targets, ambiguous


def _resolve_raw_targets(
    targets: list[tuple[str, str, bool]],
) -> tuple[list[Path], bool]:
    resolved: list[Path] = []
    ambiguous = False
    for raw_target, cwd, is_entry in targets:
        path = Path(raw_target)
        if not path.is_absolute():
            if not cwd:
                ambiguous = True
                continue
            path = Path(cwd) / path
        try:
            resolved.append(destination_location(path) if is_entry else path.resolve())
        except (OSError, RuntimeError, ValueError):
            ambiguous = True
    return resolved, ambiguous


def _preflight_raw_writes(
    data: dict[str, object],
    text: str,
    walk: list[WalkedSegment],
    scan: WriteTargetScan,
    execution_cwd: str,
    cache: dict[Any, Any],
) -> None:
    """Judge every raw write target against its own repository.

    An unresolvable target is ambiguous only when the command names ref
    storage, so ordinary writes through a variable are not denied.
    """
    candidates, ambiguous = _raw_write_candidates(text, walk, scan, execution_cwd)
    resolved, unresolvable = _resolve_raw_targets(candidates)
    names_ref_storage = _REF_STORAGE_RE.search(text) is not None
    if (ambiguous or unresolvable) and names_ref_storage:
        _deny_ambiguous(data, execution_cwd, cache)
    for path in resolved:
        target = resolve_git_target_for_path(str(path), cache)
        if target.kind == GitTargetKind.UNRESOLVED and names_ref_storage:
            _deny_unresolved(data)
        if target.kind != GitTargetKind.RESOLVED:
            continue
        mutation = _classify_raw_write_target(
            path, Path(target.common_git_dir), Path(target.worktree_git_dir)
        )
        if mutation is not None:
            _deny_for_mutations(data, _context_for(target, cache), [mutation])


def _lazy_anchor(project_root: Path, cache: dict[Any, Any]) -> Callable[[], str | None]:
    """The common Git directory of the session's project repository, resolved on first use.

    A repository the anchor cannot resolve raises, which blocks only the command
    that needed it. Drift is harmless: a linked worktree's state root shares the
    primary's common directory.
    """

    def anchor() -> str | None:
        target = resolve_git_target_for_path(str(project_root), cache)
        if target.kind == GitTargetKind.UNRESOLVED:
            raise RuntimeError(f"cook anchor repository unresolved: {target.reason}")
        return target.common_git_dir if target.kind == GitTargetKind.RESOLVED else None

    return anchor


def _effective_head_effect(facts: GitSegmentFacts) -> GitHeadEffect:
    if facts.identity_unknown:
        return GitHeadEffect.AMBIGUOUS
    if facts.outcome is None:
        return GitHeadEffect.NONE
    if facts.outcome.rewrites_current_branch:
        return GitHeadEffect.MOVES
    return facts.outcome.head_effect


def _primary_head_violation(
    facts: GitSegmentFacts, anchor: Callable[[], str | None]
) -> str | None:
    """Why *facts* would move HEAD of the anchor repository's primary checkout, if it would."""
    effect = _effective_head_effect(facts)
    if effect in (GitHeadEffect.NONE, GitHeadEffect.PATH_RESTORE):
        return None
    target = facts.target
    if target.kind == GitTargetKind.UNRESOLVED:
        return "unresolved_target"
    if target.kind != GitTargetKind.RESOLVED or not target.is_main_worktree:
        return None
    protected = anchor()
    if protected is None or protected != target.common_git_dir:
        return None
    return "moves_primary_head" if effect == GitHeadEffect.MOVES else "ambiguous_head_effect"


_PRIMARY_HEAD_CLONE_NOTE = (
    "A worktree or clone created by the same command is judged by its nearest existing "
    "ancestor, so create it in a separate command first."
)
_PRIMARY_HEAD_REMEDY = {
    "moves_primary_head": (
        "A cook session must leave HEAD of its primary checkout alone. Run this in a linked "
        "worktree: `cd <worktree> && <command>` or `git -C <worktree> <command>`. "
        + _PRIMARY_HEAD_CLONE_NOTE
    ),
    "ambiguous_head_effect": (
        "This could switch the primary checkout's branch, so it is denied. To restore paths use "
        "`git checkout -- <path>` or `git restore <path>`; to switch branches or check out a "
        "commit use a linked worktree: `git -C <worktree> switch <branch>` or "
        "`git -C <worktree> switch --detach <commit>`. " + _PRIMARY_HEAD_CLONE_NOTE
    ),
    "unresolved_target": (
        "The repository this command acts on could not be proven. Name it with a literal "
        "`git -C <worktree> ...` or `cd <worktree> && ...`. " + _PRIMARY_HEAD_CLONE_NOTE
    ),
}


def _deny_primary_head(
    *,
    data: dict[str, object],
    cwd: str,
    subcommand: str,
    head_effect: str,
    reason: str,
    protected_primary: str,
) -> None:
    details = {
        "execution_cwd": cwd,
        "head_effect": head_effect,
        "protected_primary": protected_primary,
        "reason": reason,
        "remedy": _PRIMARY_HEAD_REMEDY[reason],
        "session_id": str(data.get("session_id", "")),
        "subcommand": subcommand,
    }
    deny_tool_use(
        PRIMARY_HEAD_DENY_PREFIX + json.dumps(details, sort_keys=True, separators=(",", ":"))
    )


def _enforce_primary_head(
    data: dict[str, object],
    seg: WalkedSegment,
    facts: GitSegmentFacts,
    anchor: Callable[[], str | None],
) -> None:
    reason = _primary_head_violation(facts, anchor)
    if reason is None:
        return
    target = facts.target
    _deny_primary_head(
        data=data,
        cwd=target.cwd or seg.verb_cwd,
        subcommand=facts.invocation.subcommand,
        head_effect=str(_effective_head_effect(facts)),
        reason=reason,
        protected_primary=(target.toplevel or target.cwd) if reason != "unresolved_target" else "",
    )


def _decide_shared_ref(
    data: dict[str, object], facts: GitSegmentFacts, cache: dict[Any, Any], cwd: str
) -> None:
    """Deny what *facts* would do to a checked-out ref; *cwd* is the proven cwd or empty."""
    target = facts.target
    invocation = facts.invocation
    if facts.identity_unknown:
        if target.kind == GitTargetKind.UNRESOLVED:
            _deny_unresolved(data)
        elif target.kind == GitTargetKind.RESOLVED:
            _deny_for_mutations(data, _context_for(target, cache), [("", "<unresolved>", True)])
        return
    if target.kind == GitTargetKind.UNRESOLVED:
        if target.reason == "unparsed_global_options":
            _deny_ambiguous(data, cwd, cache)
        elif is_ref_mutation(invocation.subcommand, invocation.flags):
            _deny_unresolved(data)
        return
    if target.kind == GitTargetKind.RESOLVED:
        context = _context_for(target, cache)
        _deny_for_mutations(data, context, _classify_git_invocation(invocation, context))


def _preflight_segment(
    data: dict[str, object],
    seg: WalkedSegment,
    flags: ChannelFlags,
    cache: dict[Any, Any],
    anchor: Callable[[], str | None] | None,
) -> None:
    if seg.position_unresolved and _mentions_git_mutation(" ".join(seg.executable)):
        _deny_unresolved(data)
    verb = os.path.basename(seg.executable[seg.start]) if seg.start is not None else ""
    if verb in _PACK_VERBS:
        if not seg.cwd_certain or not seg.verb_cwd:
            _deny_unresolved(data)
        return
    facts = analyze_git_segment(seg, flags, cache)
    if facts is None:
        return
    if anchor is not None:
        _enforce_primary_head(data, seg, facts, anchor)
    if facts.invocation.subcommand in _READ_ONLY_SUBCOMMANDS and not facts.identity_unknown:
        return
    _decide_shared_ref(data, facts, cache, seg.verb_cwd if seg.cwd_certain else "")


def _preflight_checked_out_ref_mutation(
    data: dict[str, object],
    command: str,
    execution_cwd: str,
    cache: dict[Any, Any],
    anchor: Callable[[], str | None] | None,
) -> None:
    # The live-text projection (rectify #4941 Part A): heredoc and herestring
    # bodies are blanked at their source spans and appended once when their
    # consumer executes them, so an inert `cat <<'EOF'` body mentioning
    # "git push --force" as prose does not match, while a heredoc/pipe-fed
    # shell that actually runs it does.
    live_text = live_command_text(command)
    walk = walk_evaluated_segments(command, execution_cwd)
    if walk is None:
        if _mentions_git_mutation(live_text):
            _deny_unresolved(data)
        return
    flags = command_channel_flags(live_text)
    for seg in walk:
        _preflight_segment(data, seg, flags, cache, anchor)
    scan = scan_write_targets(live_text, execution_cwd)
    _preflight_raw_writes(data, live_text, walk, scan, execution_cwd, cache)
    if extract_interpreter_command_payloads(command)[1] and _mentions_git_mutation(live_text):
        _deny_unresolved(data)


def _git_relevant(probe: str) -> bool:
    """Whether *probe* (quotes and backslashes removed) can reach a Git classification."""
    return bool(
        _GIT_RELEVANT_RE.search(probe)
        or (
            ("$" in probe or "`" in probe)
            and re.search(rf"\b(?:{_mutation_names(_GIT_MUTATION_SUBCOMMANDS)})\b", probe)
        )
    )


def _kitchen_marker_exists(project_root: Path) -> bool:
    try:
        return (project_root / ".autoskillit" / "temp" / ".hook_config.json").exists()
    except OSError:
        return False


def _parse_hook_event() -> tuple[dict[str, object], Any] | None:
    try:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            return None
        return data, parse_hook_command(data)
    except (json.JSONDecodeError, AttributeError, OSError, TypeError, ValueError):
        return None


def _git_op_policy_allows(project_root: Path, blocked: tuple[str, ...]) -> bool:
    try:
        hook_data = read_merged_hook_config(root=project_root)
        git_ops_policy = hook_data.get("git_ops_policy", {})
        return bool(git_ops_policy.get(f"allow_{blocked[0]}"))
    except (OSError, json.JSONDecodeError, AttributeError, TypeError) as exc:
        sys.stderr.write(f"git_ops_guard: config read error: {exc}\n")
        return False


def main() -> None:
    hook_event = _parse_hook_event()
    if hook_event is None:
        sys.exit(0)
    data, parsed = hook_event

    cmd = parsed.command or ""

    if not cmd:
        sys.exit(0)

    if not _git_relevant(re.sub(r"['\"\\]", "", cmd)):
        sys.exit(0)

    # Any unexpected classification error denies. SystemExit raised by the
    # deny helpers is BaseException, not Exception, so it bypasses this
    # handler and serves as the explicit verdict signal.
    try:
        project_root = resolve_state_root(parsed.payload_cwd)
        # The marker is written by open_kitchen and removed by close_kitchen, but it
        # is project-wide: a launched interactive session is checked without it.
        kitchen_open = _kitchen_marker_exists(project_root)
        cache: dict[Any, Any] = {}

        if kitchen_open or hook_is_launched_interactive():
            anchor = _lazy_anchor(project_root, cache) if hook_is_interactive_cook() else None
            _preflight_checked_out_ref_mutation(data, cmd, parsed.execution_cwd, cache, anchor)

        headless, tier = hook_session_shape()
        # Orchestrator-typed headless sessions bypass the destructive-op deny but
        # remain subject to the all-session ref preflight above.
        skill_name = os.environ.get("AUTOSKILLIT_SKILL_NAME", "")
        if (
            not headless
            or not kitchen_open
            or skill_name in _EXEMPT_SKILLS
            or tier in _DESTRUCTIVE_OP_EXEMPT_TIERS
        ):
            sys.exit(0)

        blocked = contains_blocked_git_op(cmd, _BLOCKED_GIT_OPS, parsed.execution_cwd, cache)
        if blocked is None or _git_op_policy_allows(project_root, blocked):
            sys.exit(0)

        op_str = " ".join(("git",) + blocked)
        deny_tool_use(_DENY_REASON_TEMPLATE.format(op=op_str))
    except Exception as exc:
        _LOGGER.error("git_ops_guard classification failed", exc_info=True)
        block(f"git_ops_guard: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
