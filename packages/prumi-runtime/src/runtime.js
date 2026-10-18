// Prumi runtime: runs inside a dashboard's sandboxed frame (spec section 13).
//
// Every number on a dashboard arrives here from the shell, by postMessage. The frame can't
// call any server itself (connect-src 'none'). There are no aggregation helpers on purpose:
// the runtime never sums, averages or re-groups rows; the source computes every number.
(function () {
  "use strict";

  if (window.prumi) return; // the HTML loaded /runtime.js itself: ignore the second load

  var PROTOCOL = 0;
  var HANDSHAKE_MS = 2000;
  var NOTICE = "This dashboard only works inside Prumi";

  var inShell = window.parent !== window;
  var connected = false;
  var dead = false;
  var subscribed = [];
  var onData = {};
  var onError = {};
  var onFilters = [];
  var filters = {};
  var labels = {};
  var formats = {};
  var pending = {};
  var nextRequest = 1;

  function send(type, payload) {
    var message = { prumi: PROTOCOL, type: type };
    for (var key in payload) message[key] = payload[key];
    // The frame's origin is opaque, so there's no origin to name: "*".
    window.parent.postMessage(message, "*");
  }

  function call(callbacks, value) {
    (callbacks || []).forEach(function (callback) {
      try {
        callback(value);
      } catch (error) {
        console.error(error);
      }
    });
  }

  function copy(value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  }

  function showNotice() {
    dead = true;
    Object.keys(pending).forEach(function (request) {
      pending[request].reject(new Error(NOTICE));
    });
    pending = {};
    var notice = document.createElement("div");
    notice.id = "prumi-notice";
    notice.textContent = NOTICE;
    notice.setAttribute(
      "style",
      "position:fixed;inset:0;z-index:2147483647;display:flex;align-items:center;" +
        "justify-content:center;background:#fff;color:#333;font:16px system-ui,sans-serif"
    );
    (document.body || document.documentElement).appendChild(notice);
  }

  window.addEventListener("message", function (event) {
    if (!inShell || dead || event.source !== window.parent) return;
    var message = event.data;
    if (!message || message.prumi !== PROTOCOL) return;
    switch (message.type) {
      case "init":
        connected = true;
        filters = message.values || {};
        labels = message.labels || {};
        formats = message.formats || {};
        call(onFilters, copy(filters));
        break;
      case "data":
        call(onData[message.block], message.result);
        break;
      case "error":
        call(onError[message.block], message.issue);
        break;
      case "filters":
        filters = message.filters || {};
        call(onFilters, copy(filters));
        break;
      case "filterValues":
        var request = pending[message.request];
        if (!request) return;
        delete pending[message.request];
        if (message.error) request.reject(new Error(message.error));
        else request.resolve(message.values);
        break;
    }
  });

  function start() {
    if (!inShell) {
      showNotice();
      return;
    }
    // Repeated until the shell answers: its listener may not exist yet when the frame loads.
    var started = Date.now();
    (function hello() {
      if (connected || dead) return;
      if (Date.now() - started >= HANDSHAKE_MS) {
        showNotice();
        return;
      }
      send("ready", { blocks: subscribed.slice() });
      setTimeout(hello, 200);
    })();
  }

  var FORMAT_TYPES = ["number", "integer", "percent", "currency"];
  var warned = {};

  // A metric name, or a column's format object ({type, unit, decimals}). Anything else is
  // formatted as a plain number, with one console warning per argument so the mistake shows.
  function resolveFormat(metric) {
    if (typeof metric === "string" && Object.prototype.hasOwnProperty.call(formats, metric)) {
      return formats[metric];
    }
    if (metric && typeof metric === "object" && FORMAT_TYPES.indexOf(metric.type) >= 0) {
      return metric;
    }
    var key = typeof metric === "string" ? "name:" + metric : String(JSON.stringify(metric));
    if (!warned[key]) {
      warned[key] = true;
      console.warn(
        "prumi.format: unknown metric " + key.replace(/^name:/, "") +
          "; formatting as a plain number. Pass a metric name or a column's format object."
      );
    }
    return { type: "number" };
  }

  function format(value, metric) {
    var f = resolveFormat(metric);
    if (value === null || value === undefined) return "–";
    if (typeof value !== "number") return String(value);
    var decimals = f.decimals === null || f.decimals === undefined ? undefined : f.decimals;
    var options;
    if (f.type === "integer") {
      options = { maximumFractionDigits: 0 };
    } else if (f.type === "currency") {
      options = { style: "currency", currency: f.unit };
      if (decimals !== undefined) {
        options.minimumFractionDigits = decimals;
        options.maximumFractionDigits = decimals;
      }
    } else if (f.type === "percent") {
      options = { style: "percent", maximumFractionDigits: decimals === undefined ? 1 : decimals };
    } else {
      options = { maximumFractionDigits: decimals === undefined ? 2 : decimals };
    }
    try {
      return new Intl.NumberFormat(undefined, options).format(value);
    } catch (error) {
      return String(value);
    }
  }

  window.prumi = Object.freeze({
    // Called with the block's result on load and after each filter change.
    on: function (blockId, callback) {
      (onData[blockId] = onData[blockId] || []).push(callback);
      if (subscribed.indexOf(blockId) < 0) subscribed.push(blockId);
    },
    onError: function (blockId, callback) {
      (onError[blockId] = onError[blockId] || []).push(callback);
    },
    // value: [values] for a dimension filter ([] = all), a window object for a time filter.
    setFilter: function (filterId, value) {
      if (!connected) return;
      send("setFilter", { id: filterId, value: copy(value) });
    },
    getFilter: function (filterId) {
      return copy(filters[filterId]);
    },
    filterValues: function (filterId) {
      if (dead) return Promise.reject(new Error(NOTICE));
      return new Promise(function (resolve, reject) {
        var request = nextRequest++;
        pending[request] = { resolve: resolve, reject: reject };
        send("filterValues", { id: filterId, request: request });
      });
    },
    onFilters: function (callback) {
      onFilters.push(callback);
    },
    // Applies the interactions declared for the block (the shell knows them).
    click: function (blockId, row) {
      if (!connected) return;
      send("click", { block: blockId, row: copy(row) });
    },
    format: format,
    label: function (name) {
      return Object.prototype.hasOwnProperty.call(labels, name) ? labels[name] : name;
    },
  });

  // After the document's scripts ran, so their prumi.on calls are in the ready message.
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else setTimeout(start, 0);
})();
