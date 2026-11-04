# 0017. Inside the container, Docker's port publishing is the gate

Status: Proposed · 2026-11-03

## Context

Prumi has no login. Anyone who reaches the server can open every dashboard and call every MCP tool, with the rights of the warehouse connection. So far one rule kept that safe: `host` must be a loopback address, or the server refuses to start.

That rule doesn't work in a container. A container's loopback is its own. A server that listens on `127.0.0.1` inside it can't be reached from the host machine, even through a published port, because Docker forwards the port to the container's network address. To be reachable at all, Prumi in the image has to listen on every address of the container (`0.0.0.0`).

Two checks that rest on the loopback rule also assume Prumi knows its own address. The Host check, which blocks DNS rebinding, answers only `<host>:<port>` and `localhost:<port>`. The links Prumi hands out are built from `host` and `port`. In a container, `0.0.0.0:8484` isn't an address a browser can use, and the port the browser does use is whatever Docker publishes on the host machine, which Prumi can't see.

## Decision

- **The image sets `PRUMI_CONTAINER=1`.** Only exactly `1` turns container mode on; any other value counts as unset, so `PRUMI_CONTAINER=0` opens nothing. A `uv` install doesn't have it unless someone sets it on purpose.
- **In container mode `host` defaults to `0.0.0.0`,** and a non-loopback `host` is allowed. A loopback `host` still starts, with a warning that the server can't be reached from outside the container. Outside a container nothing changes, and the refusal doesn't mention the variable, so nobody is nudged to set it on a laptop.
- **The Host check answers loopback names on any port:** `localhost`, `127.0.0.1` and `[::1]`, with any port or none. Rebinding is blocked by the name, not the port. The MCP endpoint accepts the same Host names, and requests whose Origin is `http://localhost`, `http://127.0.0.1` or `http://[::1]`, on any port.
- **Links come from the request's `Host` header:** the dashboard links in MCP results and the MCP address on the home page. The Host check has already limited that header to loopback names, so a remapped port (`-p 127.0.0.1:9000:8484`) gives right links. The "This link only works on this machine." note stays.
- **Every documented `docker run` publishes on loopback only,** `-p 127.0.0.1:8484:8484`, and the docs say why next to the command.
- **`prumi certify` requires `--by` in a container.** The OS user there is the image's, so the default would record "prumi" as the certifier.

This amends, for container mode only, the last Decision bullet of [ADR 0003](0003-access-belongs-to-the-source.md): "`prumi serve` refuses a `host` that isn't a loopback address. Every route rejects a `Host` header other than the configured one." In a container a non-loopback `host` is allowed and the Host check answers loopback names on any port, as above. Outside a container that bullet still holds as written.

Considered: dropping the loopback rule (rejected: every install outside a container would be one config line away from exposing the warehouse, and the rule still protects them). A `prumi serve` flag to listen on every address (rejected: a flag gets copied from one command line to another, onto a laptop too; a variable set by the image belongs to the image, and setting it anywhere else is a deliberate step).

## Consequences

- **Docker's port publishing is the gate while there's no login.** `-p 127.0.0.1:8484:8484` makes Prumi reachable from this machine only. `-p 8484:8484` publishes it on every address of the host machine, to anyone on its network, and Docker's port rules can bypass a host firewall such as ufw. From inside the container Prumi can't tell the two apart, so it can't refuse. The docs keep `127.0.0.1:` in every command and say why.
- **Any local web page can call the MCP endpoint.** Prumi can't know the port Docker publishes it on, so it accepts an Origin of any loopback page on any port. Another page served on the same machine, such as a dev server or a local tool's web UI, could call Prumi's MCP tools from the browser. Pages from other sites are still refused. Outside a container only Prumi's own origin is accepted.
- **Other containers can reach it.** Inside the container the server listens on every address, so other containers on the same Docker network can connect. The Host check stops a browser tricked into calling Prumi under another name; it doesn't stop a program that sends its own `Host` header.
- **It ends with login.** Once Prumi checks who is calling, it is its own gate again, and a later ADR supersedes this one.
