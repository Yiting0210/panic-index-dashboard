# ADR-0001: Main Agent with specialist subagents for development work

Status: Accepted
Date: 2026-11-02

## Context

The first development agents (`agents/`, 2026-11-01) formed a sequential pipeline:
product → architect → developer → tester → reviewer. Each stage handed artifacts to the next.
In practice most tasks do not need every stage, the pipeline had no owner of overall project
state, and review findings were passed on without independent validation.

## Decision

- One Main Project Agent (`agents/main.md`) is the default entry point for project work. It owns
  context, decisions, state, and workflow, and makes the final decision.
- Product, Architect, Planner, Developer, Tester, and Reviewer are specialists the Main Agent
  calls only when their expertise adds something concrete. They receive a scoped task and
  return structured output.
- The Main Agent validates important review findings individually (Confirmed, Partially
  Confirmed, Rejected, Needs More Evidence) before treating them as issues.
- Shared project context lives in `.private/` (local, not committed): project state, decisions,
  open issues, work history, handoff notes. Significant architecture decisions are ADRs in `docs/adr/`.
- The specialists are registered as Claude Code subagents in `.claude/agents/` with restricted
  tool sets (Planner and Reviewer cannot write files). Role definitions stay in `agents/`.
- A source-of-truth order (`agents/README.md`) settles conflicts; conflicts are always surfaced.
  `AGENTS.md` ranks second, after the user's explicit current instruction, so an approved spec
  cannot silently override it.
- Context is loaded progressively per task.

## Alternatives

- Keep the sequential pipeline: rejected; it adds process to small tasks and has no state owner.
- A single agent with no specialists: rejected; independent review and testing catch errors the implementer misses.
- Commit `.private/`: not chosen; the user asked for local context (`.private/decisions.md` D-4).

## Consequences

- Small tasks skip Product, Architect, and Planner.
- The Main Agent carries more responsibility: it must justify each specialist call and keep `.private/` current.
- `.private/` is not available in fresh clones or cloud sessions; agents must fall back to tracked files there.
- The Planner role is reintroduced. This deliberately reverses the earlier instruction to have no planner.
- Tool restrictions only apply in Claude Code; path limits and read-only Bash remain instructions.
- `.claude/` stays ignored except `.claude/agents/`, so local skills and launch config stay out of git.
