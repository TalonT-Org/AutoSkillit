## remediation

### Flow

```
 investigate
      |
   rectify
      |
      +-- [review-approach] (optional)
      |
 dry-walkthrough
      |
  implement --- test <-> [x fail -> fix]
      |
      +-- [audit] (optional)
      |     x fail [-> make-plan -> dry-walkthrough]
      |
      +-- [open-pr] (optional)
```
