# prumi-runtime

The small JS library a dashboard's HTML loads. It fetches data from the Prumi server, provides shared filters and cross-filtering, and shows the server-issued trust badge and data freshness. All numbers on a dashboard come through the runtime.

Plain JS, no build step. The Prumi server serves it at `/runtime.js`.
