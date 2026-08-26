# Reviewer Agent Contract

Reviewer is independent from Content Agent. It does not rewrite by default; it returns `PASS` or `REVISE` with machine-readable issues.

Checks:
1. topic / audience / intent alignment
2. actionable content quality
3. compliance redlines
4. title-body-cover consistency
5. evidence traceability
6. excessive textual similarity against the retrieved Golden source copy
7. versioned fitness/health policy gate, including title, body and cover text

Reviewer should compare against the exact Golden IDs recorded in Content Agent evidence, not against the whole internet.
