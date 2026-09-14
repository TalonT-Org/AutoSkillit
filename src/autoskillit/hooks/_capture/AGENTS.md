# hooks/_capture/

Capture artifacts, publication, recovery, and cleanup for hook subprocesses.
The modules here remain stdlib-only and importable in standalone mode with only
the `hooks/` directory on `sys.path`, as required by `hooks/AGENTS.md`.

`_runner.py` and `_reader.py` handle the producer and consumer sides of a
capture. `_ledger.py`, `_ledger_view.py`, `_authority.py`, and `_store_port.py`
hold persisted ownership and access. `_publication.py`, `_delivery.py`, and
`_reference.py` handle publishing and resolving artifacts. `_replay.py`,
`_reconcile.py`, `_sweep.py`, and `_cleanup.py` recover or retire them; the
remaining modules define their protocol, lifecycle, capacity, and error rules.
