# Promotion policy

Conservative by design: one bad session must not poison long-term behavior.

## Destinations

| Destination | Target | Requires |
|---|---|---|
| GLOBAL RULE | `~/.agents/AGENTS.md` or shared instructions | ≥3 sessions AND ≥2 projects, high confidence |
| PROJECT RULE | project `AGENTS.md` / docs | ≥2 sessions in that project, medium+ confidence |
| EXISTING SKILL | patch to a `SKILL.md` | ≥2 sessions showing the skill's gap |
| NEW SKILL | new `~/.agents/skills/<name>/` | ≥3 sessions AND ≥2 projects needing it |
| AUTOMATION | script / hook / CLI workflow | repeated manual procedure ≥2 sessions |
| PROJECT KNOWLEDGE | docs / wiki / decision log | rediscovery ≥2 sessions in that project |
| TOOLING GAP | recommendation only | recurring errors no rule/skill can fix |
| EPHEMERAL | do not persist | single occurrence, transient workaround |

## Rules

- Prefer cross-session evidence over a single occurrence, always.
- Prefer cross-project evidence before recommending a global rule.
- A user correction is stronger evidence than agent self-assessment.
- Never promote transient workarounds (outages, version-specific breakage)
  to permanent guidance without repeated evidence across time.
- Mark low-confidence findings as watch-list; never overclaim.
- "Ignore / do not promote" is always an acceptable outcome — say so explicitly.
- Applying durable changes is OUTSIDE the default analysis pass. Propose
  diffs only after explicit user approval, never silently.
