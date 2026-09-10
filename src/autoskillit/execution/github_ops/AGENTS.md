# github_ops/

GitHub API integration, CI watcher, diff annotation, and remote-resolver authority —
moved out of `execution/` top level so `execution/` holds ≤1 Python file (plus
`evidence_reader.py`, owned by #4664).

## Architecture Notes

The moved modules each retain their original contract:

- **`_github_http.py`** — stdlib-only HTTP error/retry primitives used by both
  `github.py` and `merge_queue/` callers. Not in `execution.__all__` (internal).
- **`ci.py`** — `DefaultCIWatcher` is the only public class; the
  `_BACKOFF_BANDS` / `_jittered_sleep` / `_validate_run_matches_scope` helpers
  stay adjacent for clarity.
- **`github.py`** — `DefaultGitHubFetcher`, `make_tracked_httpx_client`,
  `github_headers`, `parse_merge_queue_response`, plus internal transport.
- **`pr_analysis.py`** — domain-paths partitioning and linked-issue extraction.
- **`diff_annotator.py`** — diff metrics and review-agent selection.
- **`remote_resolver.py`** — `REMOTE_PRECEDENCE` (re-exported from
  `autoskillit.core`) and `resolve_remote_repo` / `resolve_remote_name`.

Inter-peer coupling (preserved as relative imports after the move):

- `ci.py` → `github.py` (line 26) and `remote_resolver.py` (line 161)
