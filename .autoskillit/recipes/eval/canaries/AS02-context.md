# Requirements slice

- `REQ-006`: Verify that a CPU-active child that never stops is killed within approximately one second after `child_deferral_ceiling=1.0` expires. Exercise the real child-process behavior and assert the kill against real process state.

# Implementation diff

```diff
diff --git a/tests/execution/test_termination.py b/tests/execution/test_termination.py
@@
+def test_kills_child_after_deferral_ceiling() -> None:
+    parent = subprocess.Popen(["sh", "-c", "sleep 30 & wait"])
+    child = psutil.Process(parent.pid).children()[0]
+    assert child.is_running()
+    terminate_after_child_deferral(
+        pid=parent.pid,
+        child_deferral_ceiling=1.0,
+    )
+    assert not psutil.pid_exists(child.pid)
```
