# execution/backends/_codex/

Codex backend command construction, transport, and session reconciliation.
`session_commands.py` builds interactive `codex exec` commands.
`headless_commands.py` builds the `codex app-server --listen stdio://` commands
used by headless and resumed sessions; `app_server.py` and
`app_server_events.py` drive and decode that JSON-RPC transport.
`session_setup.py`, `session_storage_layout.py`, `session_attempt_lease.py`,
and `session_reconciliation.py` prepare, locate, and reconcile sessions.
`explorer_projection.py` maps explorer requests into the Codex session shape.
