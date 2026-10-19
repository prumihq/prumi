# prumi-runtime

The browser side of Prumi, plain JS with no dependencies and no build step:

- `src/runtime.js` runs inside a dashboard's sandboxed frame and defines `window.prumi` (`on`, `onError`, `setFilter`, `getFilter`, `filterValues`, `onFilters`, `click`, `format`, `change`, `locale`, `label`). Every number on a dashboard arrives through it. `format` and `change` write numbers and change figures in the server's locale, which `locale` exposes for the page's other `Intl` calls. It has no aggregation helpers on purpose.
- `src/shell.js` runs on Prumi's page around the frame: it holds the filter state, fetches block results from the server and passes them to the frame by `postMessage` (protocol version 0). The frame itself can't call any server.
- `src/theme.css` is the default theme: CSS variables (`--prumi-bg`, `--prumi-text`, `--prumi-series-1` … ) for light and dark mode, loaded into every frame and by the shell.
- `src/home.css` and `src/home.js` are Prumi's home page (`/`): its styles, over the theme's variables, and its search box and copy button. The script never calls the server.
- `src/provenance.js` is the number matcher: it reads every number a page shows and checks it is a value Prumi delivered (R8). The server runs it from an isolated world on each page it renders at validation, and the runtime runs it in viewers' browsers.

Outside a Prumi shell, the runtime shows "This dashboard only works inside Prumi" and calls no callbacks.

The Prumi server serves each at its name (`/runtime.js`, `/shell.js`, `/provenance.js`, `/theme.css`, `/home.css`, `/home.js`). The `prumi` wheel carries a copy of each (see `packages/prumi-core/hatch_build.py`); these files are the only source.
