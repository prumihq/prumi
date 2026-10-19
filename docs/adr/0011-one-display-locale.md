# 0011. One display locale per server

Status: Accepted · 2026-10-19

## Context

The number check ([ADR 0009](0009-number-provenance.md)) reads numbers as text, so it must know how they're written: separators, compact words (`K`, `mil`, `Mio.`), month names. When pages formatted numbers in the viewer's browser language, a `pt-BR` viewer saw `US$ 47 mil` while the server had checked the page in English, and every dashboard was flagged by its first viewer with a different language.

## Decision

- **One locale per server:** `[server] locale`, a BCP 47 tag, default `en-US`. `prumi.format`, `prumi.change`, the server check and the browser check all use it. The runtime exposes it as `prumi.locale` for the page's other `Intl` calls.
- **The reader is strict.** It uses only the configured locale's separators, group sizes, compact words, month names and period words, all derived from `Intl` (`formatToParts`), with no tables per language. With `en-US`, `1.234` is 1.234; with `pt-BR`, it's 1234. Accepting both conventions would give a made-up number two chances to match.
- **Pages that format in the browser's language are refused.** Validation renders the default state a second time in another language (`de-DE`, or `en-US` when the locale is German). If a bound element's text differs between the two renders, the page fails with `R8.browser_locale`.
- An unknown tag stops `prumi serve` at startup.

Considered: reading each viewer's own locale, so everyone sees numbers their way. Rejected for now: every locale's separators and compact words would have to be read correctly in every browser, and mistakes there fail silently.

## Consequences

- Every viewer of a server sees numbers formatted the same way, whatever their browser language.
- The second render adds about 0.45 s to each validation. The test suite skips it through an internal keyword that neither the config nor the MCP tools expose.
- Narrow unit forms (`12m`) aren't read as periods, because `12m` usually means 12 million.
- Showing numbers in each viewer's own language needs a new design.
