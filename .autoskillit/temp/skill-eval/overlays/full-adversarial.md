For each output this plan produces, enumerate all callers/parsers/extractors in the codebase and verify the format matches their expectations. Include at least one round-trip test.

For each data type introduced or governed by a new rule, verify it appears in the type registry of every downstream analysis tool.

For rename migrations, trace all fixtures that construct objects containing the renamed field and verify all sibling fields remain type-correct.

When creating this plan, include an adversarial review section: assume the plan will be implemented exactly as written and actively identify what could go wrong. For each proposed change, check cross-component contracts, downstream consumers, implicit conventions, and fixture completeness. If you find a gap, revise the plan to address it before finalizing.