# Configuration

Prumi reads one file, passed with `--config` (`prumi serve --config prumi.toml`). Relative paths in it resolve against the folder the file is in. An unknown section or key stops the server with a message that names it.

```toml
[server]
locale = "en-US"

[source]
adapter = "dbt"
project = "analytics"

[source.dbt]
semantic_manifest = "my-dbt-project/target/semantic_manifest.json"

[source.dbt.connection]
type = "duckdb"
path = "warehouse.duckdb"
```

The [demo's config](../examples/demo-dbt-project/prumi.toml) is a working example, but it pins `as_of` to a date: leave that out for your own data.

## `[server]`

Every key is optional.

| Key | Default | What it does |
|---|---|---|
| `host` | `127.0.0.1`, or `0.0.0.0` in the container image | The address Prumi listens on. Outside a container it must be a loopback address (`127.0.0.1`, `::1` or `localhost`): this release has no login, so anything else would expose the warehouse to the network. In the container image any address is allowed, and publishing with `-p 127.0.0.1:…` keeps Prumi off the network; see [Running in a container](#running-in-a-container). |
| `port` | `8484` | The port. The MCP endpoint is `http://<host>:<port>/mcp`. In the container images, leave it at `8484` and change the host side of `-p` instead. |
| `data_dir` | `.prumi` | Where the registry (`registry.sqlite3`) is kept. Use a local disk: SQLite's WAL mode doesn't work over a network filesystem. |
| `locale` | `en-US` | The display locale, a tag such as `en-US`, `pt-BR` or `de-DE`. Every viewer sees numbers and dates formatted this way, whatever their browser's language. |
| `as_of` | today | Pins "today" to a date (`2026-10-15`), so time windows and partial periods are computed as of that day. For demos and tests. |
| `script_hosts` | `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `unpkg.com` | The hosts a dashboard may load scripts from. Validation warns about a script URL with no exact version. |
| `style_hosts` | the same, plus `fonts.googleapis.com` | The hosts a dashboard may load stylesheets from. |
| `font_hosts` | the same, plus `fonts.gstatic.com` | The hosts a dashboard may load fonts from. |

## `[source]`

| Key | Default | What it does |
|---|---|---|
| `adapter` | required | The adapter that reads your metrics. This release has one: `dbt`. |
| `project` | none | A name for people: the home page shows it in its header. |

The adapter's own settings go in `[source.<adapter>]`.

## `[source.dbt]`

| Key | What it does |
|---|---|
| `semantic_manifest` | The path to your dbt project's `target/semantic_manifest.json`, which `dbt parse` writes. Prumi reads only this file, and reloads it when it changes; see [Preparing your dbt project](../packages/prumi-adapter-dbt/README.md#preparing-your-dbt-project). |

### `[source.dbt.connection]`

| Key | What it does |
|---|---|
| `type` | The warehouse type: `duckdb` or `postgres`. |

For `duckdb`:

| Key | What it does |
|---|---|
| `path` | The DuckDB file dbt built. Prumi opens it read-only. |

For `postgres`:

| Key | What it does |
|---|---|
| `host` | Required. The Postgres server: a host name, not a connection string. |
| `dbname` | Required. The database dbt built: its name, not a connection string. |
| `user` | Required. The role Prumi connects as. |
| `port` | An integer. Default `5432`. |
| `sslmode` | One of `disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full`. Unset, libpq decides. |
| `timezone` | A time zone name (such as `America/Sao_Paulo`) for the session. Unset, the connection keeps its own time zone, from the server, database or role setting, or `PGTZ`. Set, Prumi refuses to start if `PGTZ` overrides it. |

There is no `password` key, and Prumi refuses one. libpq finds the password itself: `PGPASSWORD`, `~/.pgpass` or a service file. It never goes in `prumi.toml`. Every Postgres session is read-only (`default_transaction_read_only = on`), so nothing Prumi runs can write, and prints dates in ISO format (`DateStyle = ISO`), whatever the role or database sets. Any other key is refused by name.

Changes to the config take effect when Prumi restarts. The server reloads the manifest when it changes: a new one that fails a check is logged and the current one stays in use.

## Running in a container

The images, `ghcr.io/prumihq/prumi` and the demo's `ghcr.io/prumihq/prumi-demo`, set the environment variable `PRUMI_CONTAINER=1`. It changes how Prumi listens, because a container's loopback address is its own: a server that listens only there can't be reached from the host machine, even through a published port.

| | `PRUMI_CONTAINER` unset (a `uv` install) | `PRUMI_CONTAINER=1` (the images) |
|---|---|---|
| Default `host` | `127.0.0.1` | `0.0.0.0` |
| A non-loopback `host` in the config | refused | allowed |
| Host names the server answers | `<host>:<port>` and `localhost:<port>` | `localhost`, `127.0.0.1` and `[::1]`, on any port |
| The links Prumi hands out | built from `host` and `port` | built from the address the request came in on |
| `prumi certify` without `--by` | certifies as the OS user | refused |

Only `1` turns it on: any other value, `0` included, counts as unset. Don't set it outside a container; it's what lets Prumi listen beyond loopback. Inside the image, a config that sets a loopback `host` still starts, with a warning that the server can't be reached from outside the container: leave `host` unset there.

**Keep `127.0.0.1:` in `-p`.** Inside the container Prumi listens on every address, and Docker decides who can reach it. `-p 127.0.0.1:8484:8484` publishes the port on the host machine's loopback address only, so only this machine can open Prumi. `-p 8484:8484` would publish it on every address of the host machine, to anyone on its network, and Docker's port rules can bypass a host firewall such as ufw. Prumi can't see the difference from inside the container, so it can't refuse. This release has no login: anyone who reaches Prumi can open every dashboard and call every MCP tool. [ADR 0017](adr/0017-docker-is-the-gate-in-the-container.md) has the reasoning.

**Any local web page can call the MCP endpoint.** Prumi can't know which port Docker publishes it on, so in a container its MCP endpoint accepts requests from a page served on `localhost`, `127.0.0.1` or `[::1]`, on any port. Another web page running on your machine, such as a dev server, could call Prumi's MCP tools from your browser. Pages from other sites are refused. This ends with login.

**Another port.** The images listen on `8484`, and their healthcheck checks `8484`. To use another port, change the host side of `-p` (`-p 127.0.0.1:9000:8484`), not `port` in the config. The links Prumi hands out then name the port you chose, and the MCP endpoint is `http://127.0.0.1:9000/mcp`.

**Your own project.** The main image reads `/prumi/prumi.toml`. Started without one, it stops with a message naming that path; it never falls back to the demo. Mount your config there:

```sh
docker run -p 127.0.0.1:8484:8484 -v "$PWD/prumi.toml:/prumi/prumi.toml:ro" \
  -v prumi-data:/prumi/.prumi ghcr.io/prumihq/prumi
```

The `prumi-data` volume holds the registry (`/prumi/.prumi`: dashboards and certifications), so it survives a new container, such as one from a newer image; without it, they're lost with the container. Any folder you bind-mount that Prumi writes to must be writable by uid 10001, the user Prumi runs as in the image.

Paths and host names in the config are seen from inside the container. Mount the semantic manifest and a DuckDB file too, at the paths the config names, and remember that `localhost` there is the container itself, not your machine.

**The CLI** runs inside the container with `docker exec`: `docker exec <container> prumi list`, where `<container>` is the name or ID `docker ps` shows. `prumi certify` needs `--by` there, because the OS user inside is the image's, not yours:

```sh
docker exec <container> prumi certify <id> 1 --by "Your Name"
```

**Chromium's own sandbox is off,** in the images as everywhere else in this release: Playwright starts Chromium without it unless told otherwise. The dashboard's sandboxed frame ([ADR 0004](adr/0004-sandboxed-frame.md)) is a different thing and stays on. Turning Chromium's sandbox on is [#31](https://github.com/prumihq/prumi/issues/31). The images run as a non-root user, which limits what a page that broke out of Chromium could reach.

The images are tested with Docker Engine on Linux. They should work with Docker Desktop and Podman, but those aren't tested.
