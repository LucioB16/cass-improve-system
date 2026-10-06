# AGENTS.md

## Project purpose

`cass-improve-system` is a portable coding-agent skill for **batch retrospective analysis across many historical coding-agent sessions and projects**.

It is deliberately different from per-session reflection hooks. The user should be able to open a fresh coding-agent session, request a period such as the last 24 hours or last 7 days, and receive evidence-backed recommendations about recurring mistakes, duplicated work, automation opportunities, candidate skills, instruction improvements, tooling gaps, and successful reusable patterns.

CASS (Coding Agent Session Search) is the external ingestion/search dependency. Do not reimplement CASS and do not vendor it.

Expected public repository:

`https://github.com/LucioB16/cass-improve-system`

Upstream CASS:

`https://github.com/Dicklesworthstone/coding_agent_session_search`

## Source of truth and execution

- Read `PLAN.md` before starting work.
- Execute the project through `PLAN.md` in order.
- Mark tasks `[x]` only after the work and its validation have succeeded.
- Add newly discovered required work to the appropriate phase before or while implementing it.
- `PLAN.md` is operational state, not archival documentation.
- Keep this file compact. Move detailed durable knowledge into `docs/`.

## Core product invariants

1. **Batch/global review, not per-session ceremony.**
   The product exists so a new session can analyze many earlier sessions without requiring hooks or commands to have been run in those original sessions.

2. **CASS is mandatory.**
   Every review must preflight the `cass` executable and required capabilities. If CASS is absent or incompatible, fail explicitly and actionably. Never fabricate review results.

3. **Never run bare `cass`.**
   Bare CASS may launch an interactive TUI. Agent-facing invocations must use the currently supported non-interactive/machine-readable mode verified against the installed version.

4. **Current CLI behavior must be discovered.**
   Do not hard-code assumptions from stale documentation. Check the installed CASS version/help during integration and isolate CASS-specific syntax in a narrow adapter.

5. **Read-only by default.**
   The default review analyzes and recommends. It must not silently edit `AGENTS.md`, `CLAUDE.md`, skills, source files, Git config, or other durable instructions.

6. **Evidence before promotion.**
   A one-off agent mistake is not automatically a global rule. Prefer repeated, verified evidence; distinguish global, project-specific and ephemeral findings.

7. **Cross-session and cross-project reasoning matters.**
   Do not merely produce N session summaries. Aggregate patterns, deduplicate equivalent findings, and distinguish local from systemic behavior.

8. **Human-readable provenance.**
   Recommendations need traceable support: sessions/projects/agents/counts and concise evidence references. Do not dump entire transcripts unless necessary.

9. **Protect secrets and private history.**
   Historical sessions are sensitive. Do not commit transcripts, CASS indexes, generated corpora, credentials, tokens, or machine-specific private paths.

10. **Portable core, thin adapters.**
    The skill should live cleanly under `~/.agents/skills/cass-improve-system/`. Claude-specific installation may live under `~/.claude/skills/cass-improve-system/`, but the core implementation should not fork into divergent versions without necessity.

## Intended repository structure

The likely structure is:

```text
skill/cass-improve-system/
    SKILL.md
    scripts/
    references/

scripts/
    install-skill.ps1
    verify-install.ps1

tests/
docs/
```

Update this map when the actual structure stabilizes.

## Technical principles

- Prefer small scripts with standard-library dependencies.
- Keep CASS subprocess execution and response parsing isolated.
- Normalize session data before analysis.
- Use hierarchical/batched analysis to avoid context blowups on large periods.
- Tests must include deterministic fixtures; the user's live history is for integration tests only.
- Report coverage gaps rather than hiding skipped/unreadable sessions.
- Use explicit exit codes and useful error messages for scripts.
- Support Windows first because that is the user's current environment, without gratuitously preventing cross-platform use.
- Do not rely on undocumented internal CASS database schemas when a supported CLI/robot interface exists.

## Safety and approval boundaries

The user has explicitly authorized:
- creating and editing files in this project;
- initializing local Git;
- creating a new public GitHub repo `LucioB16/cass-improve-system` via authenticated `gh`;
- installing CASS globally/user-globally if missing;
- indexing local coding-agent sessions as needed for testing;
- globally installing this skill;
- running tests and real retrospective analyses over local session history;
- committing and pushing this project's source/docs/tests.

Do not:
- publish transcript/session contents;
- commit secrets;
- delete unrelated repositories;
- destructively reset/repair CASS data unless necessary and safe;
- rewrite Git history unnecessarily;
- apply learned rules/skills automatically during the product's normal read-only review.

Stop only for genuine blockers or irreversible/destructive actions outside the authorization above.

## Documentation

Detailed knowledge belongs in `docs/`.

Create and maintain focused documentation under `docs/` as the implementation evolves. At minimum, once relevant, maintain:
- `docs/architecture.md`
- `docs/cass-integration.md`
- `docs/testing.md`

README is product-facing documentation and must be publication quality.

## Validation expectations

Before calling the project complete:
- deterministic tests pass;
- global CASS installation is verified;
- global skill installation is verified;
- missing-CASS behavior is tested;
- a real last-24h review succeeds;
- larger-period behavior is exercised;
- default review makes no durable project changes;
- public GitHub repository and README are verified from the remote view;
- Git tree is clean and pushed.

## Context discipline

Keep this file high-signal. Do not paste research dumps, transcripts, detailed CASS command catalogs, or implementation logs here. Put those in focused `docs/` files and link them when useful.
