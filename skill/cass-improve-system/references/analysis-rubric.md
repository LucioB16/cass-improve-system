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

## Normalization guarantees

- Roles: explicit `role` passes through (`human`→user); Antigravity
  `source`/`type` map conservatively to user/assistant; Copilot Chronicle
  dotted event types (`user.message`, `assistant.message`, tool execution,
  system/lifecycle) map from an exact allow-list; unknown stays `?`.
- Content: top-level `content` first, then nested `data.content`
  (Copilot Chronicle) / `payload.content`; tool payloads try
  result/output/summary keys plus a structured `error.message`
  diagnostic (combined with the payload, never replacing it); event
  envelopes are never dumped as messages.
- Copilot Chronicle control/telemetry events from an audited exact list
  (hooks, checkpoints, skill refs, subagent/canvas/compaction/mode
  lifecycle, notifications) map to `system`; external tool requests map
  to `tool`. Only genuinely unrecognized types stay `?`, so `unknown`
  counts keep meaning "unrecognized", not "control noise".
- Sessions carry semantic content coverage (full/partial/none/empty):
  unknown formats and undecodable turns are counted, never silently
  dropped; control-only sessions are `empty`, not failures.
- Signals are extracted from the FULL export; message bodies use bounded
  stratified retention (signal-bearing + user turns first, then head/tail,
  max 120). Evidence refs always point at original message indices.
- Activity ranges come from message timestamps (else inventory fields);
  sessions whose reliable range falls fully outside the requested period
  are flagged `out_of_period` but never dropped; missing timestamps are
  reported as uncertainty, never filtered.
- Findings carry a stable `id` (`type:fingerprint`) and a `destinations`
  list; reports render each finding once and cross-reference by destination.

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

## Precision model

- Provenance per message (structured role + structural envelope markers
  such as [Tool Output], never prose): user_text, assistant_text,
  tool_result, system, unknown. Recorded per signal, aggregated per
  finding and in coverage.
- Eligibility: correction/rediscovery/manual fire on user_text only;
  retry phrases on user_text only (plus session-level error recurrence
  across 3+ distinct messages in analyze.py); success on user + assistant
  prose; error on user + assistant + tool diagnostics.
- Tool error precision: a tool execution that reports SUCCESS returned
  content, not a failure — bare error vocabulary there (`$ErrorActionPreference`,
  `throw new Error(...)`, "failed to refresh" prose, "0 errors") does NOT
  qualify. Successful tool output needs a strong diagnostic shape
  (traceback header, `XxxError:`/`Exception:` line, nonzero exit-code
  mention, counted test failures, build failure, permission-denied /
  file-not-found, timeout-with-cause, segfault). Failed executions
  (`success: false`, nonzero exit, error object) and status-less tool
  output (other vendors) keep the broad patterns. Structured error-object
  messages are surfaced as evidence; a failure with no textual anchor
  stays conservatively silent.
- Spanish corrections use phrase structure (imperative-verb + todavia,
  contradiction verbs, recorda/acordate que), not bare keywords.
- Confidence is provenance-aware: user-backed findings use standard
  thresholds; findings with no user-authored evidence cap at medium
  (same 3-session/2-project systemic bar) and file-echo/pure-diagnostic
  noise stays low.
- Findings sharing an evidence fingerprint link via related_ids without
  merging or re-rendering.

Heuristics remain heuristic: patterns are leads with known precision
limits (Spanish corrections improved but imperfect; error_mention is
noisy by nature); the conservative promotion policy is the backstop.
