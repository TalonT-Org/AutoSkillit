"""Script-identity session-scope authority (worktree compat shim).

The ``HookDef.session_scope`` field declares each hook's runtime scope
constraint; PR #5103 (post-#5114 branch) routed enforcement through
``_hook_settings.enforce_session_scope`` (a literal overload that emits
SystemExit on mismatch). The worktree branch's original
``enforce_session_scope(script_identity) -> bool`` script-identity form
was lost during rebase.

This module restores the script-identity form as a thin wrapper around
the generated ``_hook_scope_table.HOOK_SCOPE_BY_SCRIPT`` map. The literal
overload continues to live in :mod:`_hook_settings`. Both surfaces are
required: the literal overload satisfies the ``HookDef.session_scope``
declaration in the registry, and the script-identity form is what
``tests/hooks/test_hook_scope_authority.py`` requires for fail-closed
behavior when the generated table is missing or stale.

Stdlib-only; runs as a bare sibling module under ``hooks/_runtime/``.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Final, Literal

# Canonical session-scope value set for the hook runtime + hook_registry layer.
# T17 (tests/hooks/test_hook_scope_authority.py) pins this value set equal to
# the IL-0 inline constant in core/types/_type_session_shape.py. Defined here
# because _session_scope_authority is the canonical authority surface for this
# layer (per the module docstring at lines 1-19).
SESSION_SCOPE_VALUES: Final[frozenset[str]] = frozenset(
    {"any", "headless_only", "interactive_only"}
)

# Static-only mirror of SESSION_SCOPE_VALUES for use as a typing.Literal[...].
# Literal cannot reference a runtime constant, so a type alias is the only way
# to keep the canonical value set in lock-step across annotation sites
# (_hooks_defs.HookDef.session_scope and LifecycleContractDef.session_scope).
SessionScopeLiteral = Literal["any", "headless_only", "interactive_only"]


def _deny_scope_authority_unavailable(script_identity: str) -> None:
    from _policy_event import PolicyEvent, render_provenance_prefix

    reason = render_provenance_prefix(
        PolicyEvent(
            hook_id="session-scope-authority",
            hook_version=1,
            event="PreToolUse",
            decision="deny",
            reason_code="scope_authority_unavailable",
            source=script_identity,
        )
    )
    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()


def enforce_script_session_scope(script_identity: str) -> bool:
    """Return whether a registered PreToolUse guard applies to this session.

    A missing, unreadable, malformed, or incomplete generated table denies
    the tool call before returning ``False``. A normal scope mismatch
    returns ``False`` so the caller can ``sys.exit(0)``. Resolves the
    script's declared scope from the generated
    ``_hook_scope_table.HOOK_SCOPE_BY_SCRIPT`` table and compares it
    against the runtime session class.

    Accepts either a relative path (``guards/ask_user_question_guard.py``
    — the canonical key in ``HOOK_SCOPE_BY_SCRIPT``) or an absolute path
    (``__file__`` when the guard subprocess calls us directly). Absolute
    paths are translated to the hooks-relative key before lookup so the
    same call site works in both in-process and subprocess contexts.
    """
    key = script_identity
    try:
        script_path = Path(script_identity)
        if script_path.is_absolute():
            try:
                # /src/autoskillit/hooks/_runtime/_session_scope_authority.py -> .../hooks/
                hooks_dir = Path(__file__).resolve().parent.parent
                key = script_path.relative_to(hooks_dir).as_posix()
            except ValueError:
                # Script lives outside the hooks tree — preserve identity
                # so the KeyError surfaces in the diagnostic.
                key = script_identity
        table_module_name = (
            f"{__package__}._hook_scope_table" if __package__ else "_hook_scope_table"
        )
        table_module = importlib.import_module(table_module_name)
        scope = table_module.HOOK_SCOPE_BY_SCRIPT[key]
        if scope not in SESSION_SCOPE_VALUES:
            raise ValueError(f"invalid scope {scope!r}")
    except (ImportError, AttributeError, KeyError, ValueError) as exc:
        print(
            f"hook_scope_authority_unavailable: script={script_identity!r} error={exc!r}",
            file=sys.stderr,
        )
        _deny_scope_authority_unavailable(script_identity)
        return False

    from _hook_settings import hook_session_shape

    headless, _ = hook_session_shape()
    if scope == "any":
        return True
    if scope == "headless_only":
        return headless
    return not headless


def read_session_binding(payload_cwd: str, session_id: str) -> dict[str, object] | None:
    """Dict-shaped binding reader for legacy callers that predate JoinAdmission.

    Returns the binding's serialized JSON (the same shape that
    ``_session_binding.SessionBinding.to_json`` produces — a plain ``dict``
    with a top-level ``loaded_skills`` list, where each entry carries
    ``skill_name`` and ``binding_valid`` keys) when a valid binding exists
    for the payload session, or ``None`` for missing / unreadable /
    mismatched binding artifacts. Preserves the legacy contract used by
    callers like ``write_guard._interactive_prefix_policy`` and
    ``tests/hooks/test_write_guard.py`` that predate the JoinAdmission
    refactor.

    Goes through ``read_binding`` rather than ``session_join_admission``
    so a valid binding with any loaded skills returns its dict, regardless
    of which skill the caller intends to project. ``session_join_admission``
    is for skill-specific join admission (it asserts the requested skill is
    loaded) and would always deny here.
    """
    binding_module = importlib.import_module(
        f"{__package__.rsplit('.', 1)[0]}._session_binding" if __package__ else "_session_binding"
    )
    binding_path = binding_module.resolve_binding_path(payload_cwd, session_id)
    try:
        binding = binding_module.read_binding(binding_path)
    except binding_module.SessionBindingError:
        return None
    if binding is None:
        return None
    if binding.session_id != session_id:
        return None
    if not binding.binding_valid:
        return None
    return json.loads(binding.to_json())
