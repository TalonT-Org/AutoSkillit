# Requirements slice

- `REQ-011`: Rename the `retry_delay_seconds` argument to `retry_pause_seconds` and update its callers.

# Implementation diff

```diff
diff --git a/src/autoskillit/retry.py b/src/autoskillit/retry.py
@@
-def retry_request(retry_delay_seconds: float) -> None:
+def retry_request(retry_pause_seconds: float) -> None:
     ...
diff --git a/tests/test_retry.py b/tests/test_retry.py
@@
+def test_retry_uses_renamed_argument(monkeypatch: pytest.MonkeyPatch) -> None:
+    monkeypatch.setattr(client, "send", lambda: Response(status=200))
+    retry_request(retry_pause_seconds=0.1)
```
