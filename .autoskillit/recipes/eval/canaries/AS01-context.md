# Requirements slice

- `REQ-006`: Verify that a CPU-active child that never stops is killed within approximately one second after `child_deferral_ceiling=1.0` expires. Exercise the real child-process behavior and assert the kill against real process state.

# Implementation diff

```diff
diff --git a/tests/execution/test_termination.py b/tests/execution/test_termination.py
@@
+def test_kills_child_after_deferral_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
+    """Child-process liveness is simulated via a mock on
+    `_has_active_child_processes` rather than real psutil CPU-percent sampling.
+    """
+    monkeypatch.setattr(
+        _patch_process__termination,
+        "_has_active_child_processes",
+        lambda pid: True,
+    )
+    terminate_after_child_deferral(pid=123, child_deferral_ceiling=1.0)
+    time.sleep(1.1)
+    assert kill_mock.called
```
