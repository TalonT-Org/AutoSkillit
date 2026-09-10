"""Server state, session-type visibility, access guards, and lifespan boot.

Owns the mutable singleton state and context accessors (``_state``),
orchestration-level tool-access gate functions (``_guards``), session-type
tag visibility dispatch (``_session_type``), the pre-deletion editable
install guard for ``perform_merge()`` (``_editable_guard``), and the FastMCP
lifespan boot sequence (``_lifespan``).
"""
