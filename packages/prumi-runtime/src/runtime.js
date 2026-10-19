// Prumi runtime: runs inside a dashboard's sandboxed frame.
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

  // The server's display locale (one per server), from this script's tag: every viewer sees
  // numbers formatted the same way, whatever their browser's language, and the number check
  // reads them in the same locale.
  var LOCALE = (function () {
    var tag = document.currentScript && document.currentScript.getAttribute("data-locale");
    if (!tag) return "en-US";
    try {
      Intl.getCanonicalLocales(tag); // throws on a malformed tag
      return tag;
    } catch (error) {
      return "en-US";
    }
  })();

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
  var bindings = {}; // "#element-id" -> [block ids]
  var pending = {};
  var nextRequest = 1;
  // The number matcher, loaded just before this file, taken before page scripts run.
  var provenance = window.prumiProvenance;

  function send(type, payload) {
    var message = { prumi: PROTOCOL, type: type };
    for (var key in payload) message[key] = payload[key];
    // The frame's origin is opaque, so there's no origin to name: "*".
    window.parent.postMessage(message, "*");
  }

  // Reported like an uncaught error, so it shows in the console and in Prumi's page check;
  // taken now, before the page's own scripts could replace it.
  var reportError =
    typeof window.reportError === "function"
      ? window.reportError.bind(window)
      : function (error) {
          setTimeout(function () {
            throw error;
          });
        };

  // A callback that throws doesn't stop the others; for a block's callbacks, the block's
  // bound elements say it couldn't load instead of staying blank or on "Loading…".
  function call(callbacks, value, block) {
    (callbacks || []).forEach(function (callback) {
      try {
        callback(value);
      } catch (error) {
        noteError(error);
        reportError(error);
        if (block !== undefined) showFailed(block);
      }
    });
  }

  var FAILED = "This part of the dashboard couldn't load.";

  function showFailed(block) {
    Object.keys(bindings).forEach(function (selector) {
      if ((bindings[selector] || []).indexOf(block) < 0) return;
      var element;
      try {
        element = document.querySelector(selector);
      } catch (error) {
        return;
      }
      if (!element) return;
      element.textContent = FAILED;
      element.setAttribute("data-prumi-failed", "");
    });
  }

  // The in-browser number check. After each render settles, every number shown is matched
  // against the values delivered since the page loaded (a block still loading may show its
  // previous values). Findings add up per filter state, so a tooltip that shows a number and
  // goes away still counts. The shell stores each new report and, when it flags numbers, tells
  // the viewer: the server's check at publish covers the default filters with that day's data,
  // this one covers what changes after.
  var CHECK_DEBOUNCE_MS = 300;
  var check = {
    values: {}, // block id -> every value it delivered since the page loaded
    allowed: [],
    spec: null,
    state: null,
    flagged: [],
    errors: [],
    canvas: false,
    sent: "",
    timer: null,
  };

  function stateKey() {
    return JSON.stringify(
      Object.keys(filters)
        .sort()
        .map(function (id) {
          return [id, filters[id]];
        })
    );
  }

  function checkState() {
    var key = stateKey();
    if (check.state !== key) {
      check.state = key;
      check.flagged = [];
      check.errors = [];
      check.canvas = false;
      check.sent = "";
    }
  }

  function noteError(error) {
    if (!connected) return;
    checkState();
    var message = String((error && error.message) || error).slice(0, 300);
    if (check.errors.indexOf(message) < 0 && check.errors.length < 20) check.errors.push(message);
    scheduleCheck();
  }

  function scheduleCheck() {
    if (!provenance || !connected || dead) return;
    clearTimeout(check.timer);
    check.timer = setTimeout(runCheck, CHECK_DEBOUNCE_MS);
  }

  function runCheck() {
    checkState();
    var found;
    try {
      found = provenance.scan(document, {
        blocks: check.values,
        bindings: bindings,
        allowed: check.allowed.concat(provenance.fromData([check.spec, labels], [], LOCALE)),
        locale: LOCALE,
      });
    } catch (error) {
      return;
    }
    found.flagged.forEach(function (item) {
      var known = check.flagged.some(function (f) {
        return f.text === item.text && f.path === item.path;
      });
      if (!known && check.flagged.length < 20) check.flagged.push(item);
    });
    check.canvas = check.canvas || found.canvas;
    var report = {
      filters: copy(filters),
      flagged: check.flagged,
      canvas: check.canvas,
      errors: check.errors,
    };
    var text = JSON.stringify(report);
    if (text !== check.sent) {
      check.sent = text;
      send("check", { report: report });
    }
  }

  function delivered(block, result) {
    var results = {};
    results[block] = result;
    var found = provenance ? provenance.fromResults(results, LOCALE) : null;
    if (!found) return;
    var values = (check.values[block] = check.values[block] || []);
    Array.prototype.push.apply(values, found.blocks[block] || []);
    Array.prototype.push.apply(check.allowed, found.allowed);
  }

  if (typeof MutationObserver === "function") {
    new MutationObserver(scheduleCheck).observe(document, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
    });
  }
  window.addEventListener("error", function (event) {
    noteError(event.error || event.message);
  });

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
        bindings = message.bindings || {};
        check.spec = message.spec || null;
        call(onFilters, copy(filters));
        scheduleCheck();
        break;
      case "data":
        delivered(message.block, message.result);
        call(onData[message.block], message.result, message.block);
        scheduleCheck();
        break;
      case "error":
        if (!(onError[message.block] || []).length) {
          noteError("Block " + message.block + ": " + ((message.issue || {}).message || "failed"));
          showFailed(message.block);
        }
        call(onError[message.block], message.issue, message.block);
        break;
      case "filters":
        filters = message.filters || {};
        call(onFilters, copy(filters));
        scheduleCheck();
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

  var warned = {};

  // The metric's format: type and unit always come from its definition (R10). Anything other
  // than a known metric name (a format object, a typo, nothing) is formatted as a plain
  // number, with one console warning per argument so the mistake shows.
  function resolveFormat(metric) {
    if (typeof metric === "string" && Object.prototype.hasOwnProperty.call(formats, metric)) {
      return formats[metric];
    }
    var key = typeof metric === "string" ? "name:" + metric : String(JSON.stringify(metric));
    if (!warned[key]) {
      warned[key] = true;
      console.warn(
        "prumi.format: unknown metric " + key.replace(/^name:/, "") +
          "; formatting as a plain number. Pass a metric name: its type and unit come from " +
          "the metric definition."
      );
    }
    return { type: "number" };
  }

  // options only change the presentation: {decimals: n} and {compact: true} ($80K, 2.7M).
  function format(value, metric, options) {
    var f = resolveFormat(metric);
    if (value === null || value === undefined) return "–";
    if (typeof value !== "number") return String(value);
    options = options && typeof options === "object" ? options : {};
    var compact = options.compact === true;
    var decimals = typeof options.decimals === "number" ? options.decimals : undefined;
    if (decimals === undefined && !compact && f.decimals !== null && f.decimals !== undefined) {
      decimals = f.decimals;
    }
    var intl;
    if (f.type === "integer") {
      intl = { maximumFractionDigits: decimals === undefined ? 0 : decimals };
    } else if (f.type === "currency") {
      intl = { style: "currency", currency: f.unit };
      if (decimals !== undefined) {
        intl.minimumFractionDigits = decimals;
        intl.maximumFractionDigits = decimals;
      }
    } else if (f.type === "percent") {
      intl = { style: "percent", maximumFractionDigits: decimals === undefined ? 1 : decimals };
    } else {
      intl = { maximumFractionDigits: decimals === undefined ? 2 : decimals };
    }
    if (compact) {
      intl.notation = "compact";
      if (decimals === undefined) delete intl.maximumFractionDigits;
    }
    try {
      return new Intl.NumberFormat(LOCALE, intl).format(value);
    } catch (error) {
      return String(value);
    }
  }

  // A change (a _delta_pct fraction) as a signed percent, and its sign as shown: "-0.05%" at
  // one decimal is "0%", sign 0, so an arrow or color picked from `sign` matches the text.
  function change(value, options) {
    if (value === null || value === undefined) return { text: "–", sign: null };
    if (typeof value !== "number") return { text: String(value), sign: null };
    options = options && typeof options === "object" ? options : {};
    var decimals = typeof options.decimals === "number" ? options.decimals : 1;
    try {
      var parts = new Intl.NumberFormat(LOCALE, {
        style: "percent",
        maximumFractionDigits: decimals,
        signDisplay: "exceptZero",
      }).formatToParts(value);
      var sign = 0;
      parts.forEach(function (part) {
        if (part.type === "plusSign") sign = 1;
        if (part.type === "minusSign") sign = -1;
      });
      return {
        text: parts
          .map(function (part) {
            return part.value;
          })
          .join(""),
        sign: sign,
      };
    } catch (error) {
      return { text: String(value), sign: null };
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
    change: change,
    // For the page's own Intl calls (dates, other text): new Intl.DateTimeFormat(prumi.locale).
    locale: LOCALE,
    label: function (name) {
      return Object.prototype.hasOwnProperty.call(labels, name) ? labels[name] : name;
    },
  });

  // After the document's scripts ran, so their prumi.on calls are in the ready message.
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else setTimeout(start, 0);
})();
