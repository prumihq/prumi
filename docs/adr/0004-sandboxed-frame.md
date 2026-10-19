# 0004. Dashboards run in a sandboxed frame; the browser sends only filter values

Status: Accepted · 2026-10-19

## Context

A dashboard is HTML and JavaScript written by an AI agent. Prumi has to show a trust badge and data freshness that the page can't fake or hide, stop the page from sending viewer data elsewhere, and make sure a certified version keeps running the queries that were reviewed. Once login exists, agent-written code on the same origin as Prumi's API could act as its viewer: stored XSS by design.

## Decision

- **Two documents.** `/d/<id>` is Prumi's shell page: a bar with the title, the badge, freshness and details. The agent's HTML runs below it in `<iframe sandbox="allow-scripts">`, with an opaque origin and no access to the shell.
- **The frame's own response is locked down.** `/d/<id>/v/<n>/frame` carries a CSP with the `sandbox` directive, so opening the frame URL directly changes nothing, and with `connect-src 'none'` (no network calls, Prumi's included), `img-src 'none'`, `form-action 'none'` and `base-uri 'none'`. Scripts, styles and fonts load only from Prumi and the allowed hosts in the config.
- **Data arrives only through the shell.** The runtime in the frame and the shell talk by `postMessage` (protocol version `prumi: 0`), and each side accepts messages only from the other window. The shell fetches block results and passes them in.
- **The browser sends only filter values.** `POST /api/dashboards/<id>/versions/<n>/blocks/<block>` takes `{"filters": {...}}`. The server loads the block from the stored version and builds the query itself, and refuses a filter the block doesn't list.
- The server adds the runtime and the theme to the stored HTML when it serves the frame; neither is part of what the agent wrote.

Considered: the badge in a shadow root on the same page as the agent's HTML. Rejected: the HTML can still cover it, and the same-origin problem would remain for when login comes.

## Consequences

- The badge, freshness and status come from Prumi, so a page can't claim to be certified.
- A version's queries can't change without a new stored spec, so the fingerprint ([ADR 0007](0007-certification-fingerprint.md)) covers what viewers see.
- Pages can't call `fetch`, load images or nest frames. Icons work as inline SVG, and chart libraries load from the allowed hosts.
- A copied HTML file shows "This dashboard only works inside Prumi": it has no data and no badge.
- Login can be added without changing the runtime API, and the viewer's session never reaches agent-written code.
- With an opaque origin, `event.origin` can't be checked; checking `event.source` replaces it.
