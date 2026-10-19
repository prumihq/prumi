# 0009. Every number shown is a value Prumi delivered

Status: Accepted · 2026-10-19

## Context

Prumi's core rule is that numbers only come from the source. Static checks on the HTML caught literal datasets, but a test page with a hard-coded total, a JavaScript-computed average and a `fetch()` call still passed validation. Counting on the reviewer to catch that isn't enough. The rule has to be about the result, what the page shows, not about how the page computed it.

## Decision

- **Every number a page shows must match a value Prumi delivered** (metric values in rows, totals, "Others", comparison values) to the block its element is bound to, at the precision shown. Dates, numbers from the spec or result metadata, and axis ticks are allowed. The details and issue codes are under R8 in [`docs/bi-rules.md`](../bi-rules.md).
- **The server check is authoritative.** `validate_spec` renders the page in headless Chromium (Playwright) with the dry run's results, waits for rendering to settle, hovers chart marks to reveal tooltips, and scans from an isolated world, so page code can't switch the scan off or feed it values. Errors block publishing, and certification runs the check again.
- **The browser check covers other filter states.** The runtime runs the same matcher in viewers' browsers and reports to the server. A flagged current version shows that viewer a bar, is marked `needs_attention`, and can't be certified.
- **Nothing the scan can't read:** canvas, images, iframes and closed shadow roots are refused. Numbers copied into the HTML source are refused even when they're correct today.
- **Chromium is a server dependency.** `prumi serve` won't start without it. One browser runs per process, with a new context per check, one check at a time.

Considered: values that refuse arithmetic, so that `revenue / 12` throws. Rejected for now: they break every chart library, and the escape hatch agents would need defeats them. Also considered: a static warning for hard-coded KPIs. Dropped, because the server check refuses those numbers anyway.

## Consequences

- Every install carries Chromium, and a validation takes about 2 seconds.
- Canvas chart libraries can't be used; SVG and HTML charts work.
- What's left for a reviewer is visual encoding, such as whether a bar's length matches its number.
- The matcher is heuristic. Its known limits are listed in `docs/bi-rules.md`, and the fixtures in `packages/prumi-core/tests/fixtures/provenance/` pin what must pass and what must fail.
- The browser check runs inside the page, so page code could interfere with it; it's informational. The server check and certification are the guarantees.
