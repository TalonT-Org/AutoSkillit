# github_review/

Authoritative, receipt-first GitHub pull-request review publication.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Public gateway for canonicalization, gateway, ledger, coordinator, and poster services |
| `_poster_support.py` | Review-poster value objects, pacing coordinator, and pure payload/result helpers |
| `canonical.py` | Pure request validation, canonical finding ordering, and deterministic operation identity |
| `gateway.py` | Typed authenticated GitHub review reads, create-review transport, and response classification |
| `ledger.py` | Private SQLite operation, attempt, receipt, lease, and shared back-pressure authority |
| `poster.py` | Sole idempotent review-posting state machine and reconciliation authority |

## Architecture Notes

The poster is the only PR-review mutation authority. The ledger persists exact attempts
before transport, while the gateway exposes typed read/write evidence without owning
retry or reconciliation policy.
