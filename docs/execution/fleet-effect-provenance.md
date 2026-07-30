# Fleet Dispatch Effect Provenance

Fleet dispatch failures carry an `effect_provenance` snapshot. The snapshot is
request-scoped and records one stable operation ID, per-effect identities and
receipts, cancellation, local cleanup evidence, and a conservative retry
disposition.

## Effect lifecycle

Each retry-relevant effect moves through:

```text
not_started -> started -> confirmed
```

`started` without an authoritative confirmation is ambiguous. Cancellation and
deadline expiry preserve that ambiguity: neither proves that a child, durable
state write, or remote operation did not occur.

The aggregate phase is derived from the individual effects:

- `not_started`: no retry-relevant effect began.
- `started`: at least one effect is confirmed, but no commit is confirmed.
- `committed`: the dispatch commit is confirmed.
- `unknown`: at least one effect started without confirmation or carries
  ambiguity evidence.

## Retry disposition

Callers must branch on `effect_provenance.retry_disposition`:

- `fresh_dispatch_allowed` permits a new identity only when every
  retry-relevant effect is proven not started or explicitly compensated.
- `resume_by_identity` requires the recorded `dispatch_id`,
  `dispatched_session_id`, and downstream identities to be preserved.
- `reconcile_required` prohibits blind redispatch until the started,
  unconfirmed effect is reconciled.

Missing provenance fails closed and never authorizes a fresh dispatch.

## Cleanup is orthogonal

Local process cleanup records the observed `(pid, create_time)` identities,
terminated PIDs, survivors, and access-denied PIDs after the final bounded wait.
An empty survivor set proves only local process cleanup. It does not erase a
confirmed state write, commit, label mutation, or remote effect.

State cleanup, label cleanup, cancellation, and compensation remain separate
facets. Compensation is itself a fallible effect and must have its own
confirmation before it can contribute to fresh-retry authorization.

## Persistence boundary

The immutable snapshot is included in every `DispatchCompleted`,
`DispatchRejected`, timeout, cancellation, and outer application-error envelope.
Per-dispatch and campaign state records persist the latest snapshot. The
request-scoped tracker is in-memory; durable state and downstream identities are
the reconciliation authorities after process restart.
