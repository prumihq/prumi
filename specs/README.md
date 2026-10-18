# Specs

## Terms

- **Milestone:** a goal, such as MVP, Installable or Power BI. Planned in advance.
- **Release:** a published version (a git tag, plus packages on PyPI), such as 0.1.0 or 0.2.1. The number follows Semantic Versioning and is confirmed when the release ships. Releases stay 0.x until the maintainer decides 1.0 is earned.
- **Spec:** the plan for **one minor release**. A milestone has one or more specs: a small milestone has one, a big one is split into several.

## One folder per spec

Folders are named by goal, never by version (`mvp`, `installable-docker`, `installable-login`), so plans can be reordered without renaming anything. The version lives in the spec's header.

```
specs/<goal>/
  spec.md       # what we're building and why; acceptance criteria
  tasks.md      # ordered, small tasks; checked off as they land
  questions.md  # open questions found while implementing (created when needed)
```

## When a spec changes

Write the spec before the code. When the plan changes, update the spec first, then the code. When implementation hits something the spec doesn't answer, log it in `questions.md` and stop; don't guess.

| Situation | Where the change goes |
|---|---|
| The spec's release hasn't shipped | **Edit that spec.** New sections go back to `draft` status until the maintainer accepts them |
| A bug fix after the release (a patch, e.g. 0.1.1) | **No spec.** An issue, plus a CHANGELOG entry |
| New behavior after the release | **A new spec** for the next minor release |
| A spec whose release shipped (`done`) | **Frozen.** Never edited again: it's the record of what that release was |

## spec.md template

```markdown
# <Goal>: <one-line summary>

Milestone: <MVP | Installable | ...> · Release: <version, e.g. 0.2.0> (planned | shipped) · Status: draft | accepted | done

## Goal
What this release proves or delivers, in a short paragraph.

## Context
Links to the ADRs, spikes and earlier specs this spec builds on.

## In scope
What gets built, grouped by component. Precise enough to implement
without guessing; include the technical "why" where a rule isn't obvious.

## Out of scope
What is deliberately NOT built in this release, even if it looks
natural to add. Name the milestone or spec it belongs to when known.

## Design for later
Places where this release's code must leave room for a known later
need, and what to do now. Example: `execute(query, viewer)` takes a
viewer even while there is a single local user, because later
releases run queries as each viewer.

## Acceptance criteria
Numbered, checkable statements. The spec is done when all pass and its release ships.
```

## tasks.md format

An ordered checklist. Each task is small enough for one commit or PR, says when it's done, and points to the acceptance criteria it serves.

```markdown
- [ ] 1. <task> — done when <observable result> (AC 2, 3)
```

## Specs

| Spec | Milestone | Release | Status |
|---|---|---|---|
| [`mvp`](mvp/spec.md): prove the core loop on one machine ([tasks](mvp/tasks.md)) | MVP | 0.1.0 (planned) | accepted |
