"""CLI session labels — display/routing labels for the session picker.

These are NOT ``SessionType`` enum members.  They are not recognized by
``session_type()``, guards, or ``_session_type.py`` visibility logic.
Do not use in ``AUTOSKILLIT_SESSION_TYPE`` for headless sessions.
"""

from __future__ import annotations

from typing import Final

SESSION_TYPE_COOK: Final = "cook"
SESSION_TYPE_ORDER: Final = "order"
