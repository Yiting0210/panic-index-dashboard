# Development agents

Agents for developing this repository. They are not runtime agents of the product
(the product's own agent is `assistant/agent.py`).

## Hierarchy

```text
Main Project Agent (main.md)       owns context, decisions, state, and workflow
├── Product Specialist   (product.md)    requirements, scope, acceptance criteria
├── Architect Specialist (architect.md)  architecture impact, ADR need
├── Planner Specialist   (planner.md)    task breakdown and sequencing
├── Developer Specialist (developer.md)  implementation
├── Tester Specialist    (tester.md)     independent verification
└── Reviewer Specialist  (reviewer.md)   independent review
```

The Main Agent is the default entry point for project work. Specialists are called only
when their expertise adds something concrete. There is no mandatory pipeline.

### How specialists run

In Claude Code, the Main Agent is the session itself. The six specialists are registered as
subagents in `.claude/agents/<role>.md`, each with a restricted tool set. Each registration
only points to its role file here; `agents/<role>.md` is the single source of the role definition.

| Specialist | Tools | Writes to |
|---|---|---|
| product | Read, Grep, Glob, Write, Edit | `specs/` |
| architect | Read, Grep, Glob, Bash (read-only), Write, Edit | `design/`, `docs/adr/` (Proposed only) |
| planner | Read, Grep, Glob | nothing |
| developer | Read, Grep, Glob, Bash, Write, Edit | code and tests in scope |
| tester | Read, Grep, Glob, Bash, Write, Edit | `tests/` |
| reviewer | Read, Grep, Glob, Bash (read-only) | nothing |

The tool lists are enforced by Claude Code. Path limits and "read-only Bash" are instructions,
not enforcement. Other tools (Codex, Cursor) read the role files directly.

## Shared rules (all agents)

1. Read `AGENTS.md` first. Its rules are not repeated in these files.
2. Load context progressively (see below). Do not read the whole repository by default.
3. Prefer repository files over chat history when both hold the same information.
4. Never silently expand scope.
5. Label statements as **fact** (with a file reference), **assumption**, **finding**,
   **recommendation**, or **open question** when it is not obvious which one they are.
6. Surface conflicts. Never resolve them silently.

## Source of truth

When sources conflict, use this order unless the user explicitly overrides it:

1. Explicit current user instruction
2. `AGENTS.md`
3. Approved spec (`specs/`, `Status: approved`)
4. Accepted ADR (`docs/adr/`, `Status: Accepted`)
5. Documented project decision (`.private/decisions.md`)
6. Current code behavior
7. Existing tests
8. Historical notes (`.private/work-history.md`, `.private/handoff-notes.md`, older docs)
9. Agent assumptions

Any conflict between levels is reported to the Main Agent, and by the Main Agent to the user.

## Where things live

| Location | Purpose | Tracked in git |
|---|---|---|
| `AGENTS.md` | Repository rules for every coding agent | yes |
| `docs/architecture/` | Current system: `system-overview.md` (end-to-end behavior), `module-map.md` (ownership, dependencies, entry points), `baseline-review.md` (baseline risks) | yes |
| `docs/adr/` | Architecture Decision Records | yes |
| `specs/` | Feature specs (Product) | yes |
| `design/` | Feature designs (Architect) | yes |
| `tests/BASELINE.md` | Test and eval coverage baseline | yes |
| `.claude/agents/` | Specialist subagent registrations for Claude Code | yes |
| `.private/` | Shared project context: state, decisions, open issues, history, handoffs | no (local only) |

## Progressive context loading

Always: `AGENTS.md`, then understand the task. Then load only what the task needs.

| Task | Load |
|---|---|
| Simple bug fix | relevant code, relevant tests, relevant `.private/project-state.md` entry |
| New feature | spec (or the request), `system-overview.md`, relevant ADRs, `.private/project-state.md` |
| Architecture change | spec, relevant ADRs, `docs/architecture/`, affected modules, `.private/project-state.md` |
| Review | changed code, relevant requirements, relevant ADRs, relevant tests |
| Point-in-time-sensitive change | the above, plus `tests/test_point_in_time.py` and the `as_of` path in `system-overview.md` |

`module-map.md` is read before changing an existing module's boundaries or dependencies.
`tests/BASELINE.md` is read before changing a module listed in its "Verify before changing" table.

## Specialist contract

Every specialist:

- receives a clearly scoped task from the Main Agent and stays within it;
- reads only the context relevant to that task;
- returns structured output to the Main Agent, separating facts, assumptions,
  recommendations, conflicts, and uncertainties;
- does not make project-wide decisions unless its role owns that decision;
- does not update `.private/` files (the Main Agent does), except `handoff-notes.md`
  when the Main Agent asks for it.

## Avoid agent theater

Every specialist call needs a concrete reason. One capable Main Agent with targeted
specialist calls is preferred over passing every task through six agents.
