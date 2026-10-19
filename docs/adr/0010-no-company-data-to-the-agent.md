# 0010. No company data reaches the agent

Status: Accepted · 2026-10-19

## Context

Self-hosting keeps company data on the company's machines, but anything an MCP tool returns goes into the agent's context, and from there to its LLM provider. To design a dashboard, an agent needs the shape of the data (how many rows, how long the labels are, how big the numbers get), not the values.

## Decision

- **`query` returns a profile, not rows:** the row count, the window, freshness, and for each column its distinct count, empties, label lengths, sign, decimals and magnitude (the range of integer digit counts). Magnitude is the only statistic derived from values, kept because agents need it to design axes.
- **Every query result for an agent passes through one function,** `prumi.agent_output.for_agent`, which turns it into that profile.
- **Nothing agent-facing echoes data.** Number-check issues, layout warnings and the browser reports in `get_dashboard` give codes, element paths and counts. Source errors give the error type and a generic message, never the database's own text. Runtime error messages lose the page's dimension values and any number of 3 or more digits, and blocked requests name only the origin.
- **Allowed in agent output:** catalog names, labels and descriptions; counts and magnitudes; window and freshness dates; text the agent itself wrote.
- **Validation returns no screenshot.** Layout problems come back as measurements (element paths and boxes), never as an image or the page's text.
- Dashboard data goes warehouse → Prumi → browser. Prumi stores no company data: no query results, and viewers' check reports keep only codes, element paths and counts, with filter states redacted and error messages cleaned ([ADR 0013](0013-registry-sqlite.md)). Its logs hold no row values or filter values.

## Consequences

- The agent can't answer questions about the numbers. Analysis in chat would need new tools that keep this guarantee, or show their answers only to the user.
- The agent never sees the page it built. It relies on the layout warnings, and on the user looking at the draft.
- Every new tool output follows the same rules. Tests plant data and check that it never appears in agent output, nor in the registry file.
