# Analysis rubric

Heuristic signal types emitted by `normalize_cass.py`. Every signal is
DERIVED by keyword heuristics, not observed by CASS — treat excerpts as
leads, always verify against the cited session before recommending anything.

| Signal | Meaning | Typical destination |
|---|---|---|
| `user_correction` | User told the agent it was wrong / redirected it | PROJECT RULE, or GLOBAL RULE if cross-project |
| `error_mention` | Errors, failures, timeouts, crashes mentioned | TOOLING GAP or PROJECT KNOWLEDGE |
| `retry_loop` | "still broken / same error / try again" loops | AUTOMATION or skill improvement |
| `manual_procedure` | Repetitive manual steps described | AUTOMATION or NEW SKILL |
| `rediscovery` | "How do I… / what is the right flag…" questions | PROJECT KNOWLEDGE or NEW SKILL |
| `success_pattern` | Explicit satisfaction, shipped/merged | PRESERVE as reusable pattern |

## Cross-session reasoning rules

1. One session is an anecdote. Two sessions is a lead. Three or more
   sessions across two or more projects is a pattern worth promoting.
2. A user correction outweighs an agent's self-assessment, but check context:
   the user may have changed requirements mid-session rather than corrected
   a genuine mistake.
3. Distinguish transient workarounds (version-specific breakage, outage) from
   durable gaps. Never promote a transient workaround to a global rule.
4. Prefer verbatim user phrasing in evidence; keep excerpts short.
5. De-duplicate: signals with the same fingerprint across sessions are one
   candidate, with occurrence/session/project counts as its weight.
6. Sort by expected value × confidence, not raw frequency: a twice-repeated
   30-minute debugging loop beats a ten-times-repeated trivial typo.
