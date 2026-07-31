# session/

Interactive session management — cook (ephemeral) and order (orchestrator) entry points.

The `_session_process.py` module is the sole cook-attempt `Popen` owner. It owns
process groups, terminal foreground transfer, deterministic termination, and reap proof.
