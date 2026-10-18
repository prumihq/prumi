# Specs

One folder per milestone:

```
specs/<milestone>/
  spec.md       # what we're building and why; acceptance criteria
  tasks.md      # ordered, small tasks; checked off as they land
  questions.md  # open questions found while implementing (created when needed)
```

Write the spec before the code. When the plan changes, update the spec first, then the code. When implementation hits something the spec doesn't answer, log it in `questions.md` and stop; don't guess.

## spec.md template

```markdown
# <Milestone>: <one-line goal>

Release: <version, e.g. 0.1.0> · Status: draft | accepted | done

## Goal
What this milestone proves or delivers, in a short paragraph.

## Context
Links to the ADRs and spikes this spec builds on.

## In scope
What gets built, grouped by component. Precise enough to implement
without guessing; include the technical "why" where a rule isn't obvious.

## Out of scope
What is deliberately NOT built in this milestone, even if it looks
natural to add. Name the milestone it belongs to when known.

## Design for later
Places where this milestone's code must leave room for a known later
need, and what to do now. Example: `execute(query, viewer)` takes a
viewer even while there is a single local user, because later
milestones run queries as each viewer.

## Acceptance criteria
Numbered, checkable statements. The milestone is done when all pass.
```

## tasks.md format

An ordered checklist. Each task is small enough for one commit or PR, says when it's done, and points to the acceptance criteria it serves.

```markdown
- [ ] 1. <task> — done when <observable result> (AC 2, 3)
```

## Milestones

- [`mvp`](mvp/spec.md): prove the core loop on one machine. Release 0.1.0. Status: accepted. [Tasks](mvp/tasks.md).
