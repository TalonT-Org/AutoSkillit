## smoke-test

```
  clone
    |
  create_branch ----+
    |                |
  setup              |
    |                |
  implement_task     |
    |                |
  run_tests ---------+---> fail_delete_remote_branch
    |                |              |
  push_branch -------+     register_clone_failure
    |                              |
  create_pr ---------+         escalate [stop]
    |                |
  close_pr           |
    |  |             |
    +--+---> delete_remote_branch
                |
       register_clone_success
                |
            done [stop]
```
