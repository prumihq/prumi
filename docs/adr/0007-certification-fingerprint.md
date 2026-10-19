# 0007. Certification follows a fingerprint; status is computed, not stored

Status: Proposed · 2026-10-19

## Context

Certifying a dashboard says the data team reviewed it. Presentation can mislead as much as a query (a wrong label, a truncated axis), and labels live in the free HTML, not in the spec. A status that's stored, or written in the spec, can go stale or lie.

## Decision

- **Versions are immutable.** Publishing creates version N+1, unless the content is identical to the current version.
- **Fingerprint** = `"v0:" + sha256(spec_hash + ":" + html_hash)`. `spec_hash` is the sha256 of the canonical spec JSON (defaults included, keys sorted, no whitespace); `html_hash` is the sha256 of the HTML exactly as stored, with line endings normalized. A golden test pins the output.
- **Status is computed on every read:** a version is certified if a certification that hasn't been revoked exists for that dashboard with the same fingerprint; otherwise it's a draft. Any change makes a draft. Republishing content identical to a certified version makes it certified again, because that's what was reviewed.
- **Status isn't part of the spec,** and the badge comes from the server ([ADR 0004](0004-sandboxed-frame.md)).
- **Certification re-validates** the version against the current catalog, including the number check ([ADR 0009](0009-number-provenance.md)), and refuses while a viewer's browser has flagged that version.
- Spec and HTML hashes are stored separately.
- The registry is SQLite in WAL mode, so the CLI and the server use it at the same time, and a `certify` shows on the next page load.

Considered: labels declared in the spec and passed to the HTML by the runtime, so only the spec would need reviewing. Rejected: covering the whole stored HTML is stricter and simpler.

## Consequences

- Any edit, even a typo in a title, needs a new review.
- A later review flow can tell a presentation-only change from a semantic one, because the two hashes are stored separately.
- External scripts are rendered but aren't in the fingerprint, so the validator warns about scripts without an exact version (`html.unpinned_script`).
- The fingerprint carries a version prefix (`v0:`); changing the canonical form needs a new one.
