"""Script-identity session-scope authority for hook processes.

Provides ``enforce_script_session_scope`` as a thin wrapper around the
generated ``_hook_scope_table.HOOK_SCOPE_BY_SCRIPT`` map. The literal
overload lives in :mod:`_hook_settings`; the script-identity form here
is the fail-closed authority used by guard scripts when the generated
table is missing or stale.

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

    # Deferred bare-name import: doing this inside the function body breaks
    # the cycle that would otherwise arise if _hook_settings imported from
    # this module at module scope (which it cannot, since _hook_settings is
    # the canonical accessor). The bare-name form matches the subprocess
    # sys.path bootstrap that resolves _hook_settings against hooks/_runtime/.
    headless, _ = hook_session_shape()
    if scope == "any":
        return True
    if scope == "headless_only":
        return headless
    return not headless
