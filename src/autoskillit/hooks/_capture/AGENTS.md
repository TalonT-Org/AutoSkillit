# hooks/_capture/

Small stdlib-only primitives shared by the shell-capture producer and cleanup
owners. Modules must remain importable when the hooks directory alone is on
`sys.path`.

## Files

| File | Purpose |
|------|---------|
| `_authority.py` | Project/root descriptor authority and lifecycle context factory |
| `_cleanup.py` | Descriptor cleanup helpers that preserve the authoritative failure |
| `_ledger.py` | Strict bounded V1/current frame decoding, canonical encoding, and checked ledger writes |
| `_lifecycle_policy.py` | Canonical lifecycle status enums and shared successor graphs |
| `_reader.py` | Self-contained verified descriptor reader with exact bounded reads and retained carrier leases |
| `_replay.py` | Checked hook-output replay, bounded failure transport, and runner settlement |
| `_resolver.py` | Lifecycle-linearized published-reference resolution and producer-exclusive reader adoption |
| `_delivery.py` | Transactional reference publication and hook-stdout delivery-state transitions |
| `_descriptor.py` | Stdlib-only retained-descriptor metadata and digest verification |
| `_syntax.py` | Canonical capture ID, incarnation, digest, and reference-token syntax |
| `_snapshot.py` | Factory-only descriptor verification, immutable FINAL manifest, and opaque reference authority |
| `_sweep.py` | Bounded cleanup sweep and verified recovery-operation orchestration |
| `_types.py` | Lifecycle outcome, observation, and internal signal types |
