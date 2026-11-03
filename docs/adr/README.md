# Architecture Decision Records

Significant architecture decisions. Smaller accepted decisions live in the local
`.private/decisions.md`.

Agents treat an ADR with `Status: Accepted` as authoritative unless the user explicitly asks
to revisit it. A superseded ADR stays in place with `Status: Superseded by ADR-NNNN`.

## Format

File name: `NNNN-short-title.md`, numbered in order.

```text
# ADR-NNNN: <title>

Status: Proposed | Accepted | Rejected | Superseded by ADR-NNNN
Date: YYYY-MM-DD

## Context
## Decision
## Alternatives
## Consequences
```

Only the user accepts an ADR. The Architect Specialist may draft one as `Proposed`.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-main-agent-with-specialists.md) | Main Agent with specialist subagents for development work | Accepted |
