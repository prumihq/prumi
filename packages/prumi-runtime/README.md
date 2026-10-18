# prumi-runtime

The browser side of Prumi, plain JS with no dependencies and no build step:

- `src/runtime.js` runs inside a dashboard's sandboxed frame and defines `window.prumi` (`on`, `onError`, `setFilter`, `getFilter`, `filterValues`, `onFilters`, `click`, `format`, `label`). Every number on a dashboard arrives through it. It has no aggregation helpers on purpose.
- `src/shell.js` runs on Prumi's page around the frame: it holds the filter state, fetches block results from the server and passes them to the frame by `postMessage` (protocol version 0). The frame itself can't call any server.

Outside a Prumi shell, the runtime shows "This dashboard only works inside Prumi" and calls no callbacks.

The Prumi server serves both at `/runtime.js` and `/shell.js`. The `prumi-core` wheel carries a copy of each (see `packages/prumi-core/hatch_build.py`); these files are the only source.
