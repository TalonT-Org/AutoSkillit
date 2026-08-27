---
id: review-approach-criteria
title: Review-Approach Benefit Criteria
summary: Signals for deciding whether a task benefits from a review-approach research pass.
---
## Review-Approach Benefit Criteria

Use these signals as guidance, then apply judgment to the concrete task.

**Benefit signals (recommend: true):**
- Involves integrating an unfamiliar external library or API
- Proposes a design decision with multiple viable architectural approaches
- References emerging patterns, standards, or technologies not yet in the codebase
- Contains open questions about *how* to approach the problem
- Requires understanding trade-offs between competing solutions

**No-benefit signals (recommend: false):**
- Well-scoped bug fix with a clear root cause
- Internal refactoring following established codebase patterns
- Adds a feature using patterns already present in the codebase
- Documentation update or configuration change
- Already contains a fully specified implementation approach in the WP body


