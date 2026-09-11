"""Server state, session-type visibility, access guards, and lifespan boot.

Owns the mutable singleton state and context accessors (``_state``),
orchestration-level tool-access gate functions built on that state
(``_guards``), session-type tag visibility dispatch (``_session_type``),
and the FastMCP lifespan boot sequence (``_lifespan``).
"""
