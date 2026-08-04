# github_review/

Authoritative, receipt-first GitHub pull-request review publication.

## Architecture Notes

The poster is the only PR-review mutation authority. The ledger persists exact attempts,
including omitted-finding dispositions, before transport and atomically finalizes the
terminal attempt with its receipt. The gateway exposes typed read/write evidence without
owning retry or reconciliation policy.

Canonicalization and operation identity remain pure. Pacing coordinates mutations
through the private ledger, and the package public surface exposes only the services
needed by the server factory.
