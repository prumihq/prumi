# Security

## Reporting a vulnerability

Please don't open a public issue. Report it privately on GitHub: the repository's **Security** tab, then **Report a vulnerability**. Say what an attacker can do, how to reproduce it, and which version or commit you tested. You'll get an answer there, and the fix and its disclosure are coordinated with you.

## Supported versions

Prumi is 0.x: fixes go into the latest release.

## What 0.1 is built for

0.1 runs for one local user. It has no login, refuses to listen on anything but a loopback address, and checks every request's `Host` header against DNS rebinding. Exposing it to a network some other way (a reverse proxy, a tunnel) is outside what 0.1 supports.

Examples of what counts as a vulnerability:

- a dashboard page reaching the network, the shell around it, or Prumi's API from its sandboxed frame ([ADR 0004](docs/adr/0004-sandboxed-frame.md));
- company data (values or names from query results) reaching the agent through an MCP tool ([ADR 0010](docs/adr/0010-no-company-data-to-the-agent.md));
- SQL or template injection through a spec, a filter value or the block API ([ADR 0005](docs/adr/0005-structured-filters.md));
- a page on another site making a browser act on a local Prumi server;
- a page that shows a number Prumi didn't deliver and still passes the server's number check, beyond the known limits in [`docs/bi-rules.md`](docs/bi-rules.md) ([ADR 0009](docs/adr/0009-number-provenance.md)).
