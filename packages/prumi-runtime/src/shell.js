// Prumi shell: runs on Prumi's own page around the dashboard frame.
//
// It holds the filter state, fetches block results from the server (the frame can't), pushes
// them into the frame, and shows freshness and block warnings in the bar. The badge is
// rendered by the server. Nothing from a result is ever inserted as HTML.
(function () {
  "use strict";

  var PROTOCOL = 0;
  var config = JSON.parse(document.getElementById("prumi-dashboard").textContent);
  var frame = document.getElementById("prumi-frame");

  var values = {}; // filter id -> current value
  config.filters.forEach(function (f) {
    values[f.id] = copy(f.default);
  });
  var blocks = {};
  config.blocks.forEach(function (b) {
    blocks[b.id] = b;
  });
  var ready = false;
  var subscribed = [];
  var latest = {}; // block id -> sequence number of the newest request
  var results = {}; // block id -> its latest result

  function copy(value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  }

  function post(type, payload) {
    var message = { prumi: PROTOCOL, type: type };
    for (var key in payload) message[key] = payload[key];
    // The sandboxed frame has an opaque origin, so it can't be named: "*".
    frame.contentWindow.postMessage(message, "*");
  }

  function filterDef(id) {
    return config.filters.filter(function (f) {
      return f.id === id;
    })[0];
  }

  window.addEventListener("message", function (event) {
    if (event.source !== frame.contentWindow) return;
    var message = event.data;
    if (!message || message.prumi !== PROTOCOL) return;
    switch (message.type) {
      case "ready":
        if (ready) return; // the frame repeats it until init arrives
        ready = true;
        subscribed = (message.blocks || []).filter(function (id) {
          return Object.prototype.hasOwnProperty.call(blocks, id);
        });
        post("init", {
          filters: config.filters,
          values: copy(values),
          labels: config.labels,
          formats: config.formats,
          spec: config.spec,
          bindings: config.bindings,
        });
        subscribed.forEach(fetchBlock);
        break;
      case "setFilter":
        setFilter(message.id, message.value);
        break;
      case "click":
        click(message.block, message.row);
        break;
      case "filterValues":
        fetchValues(message.id, message.request);
        break;
      case "check":
        sendCheck(message.report);
        showUnverified(message.report);
        break;
    }
  });

  // The frame's number check for one filter state, stored for the data team without data:
  // flagged numbers by code and element path only, error messages cleaned. The viewer bar
  // shows the flagged text from the report as the frame sent it, in this browser only.
  function sendCheck(report) {
    if (!report || typeof report !== "object") return;
    var scrub = scrubber();
    fetch(config.api + "/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filters: report.filters,
        flagged: (Array.isArray(report.flagged) ? report.flagged : []).map(function (item) {
          return { code: item && item.code, path: item && item.path };
        }),
        canvas: report.canvas,
        errors: (Array.isArray(report.errors) ? report.errors : []).map(function (message) {
          return scrub(String(message));
        }),
      }),
    }).catch(function () {});
  }

  // Page code can put data into its error messages. Every string value of a non-metric column
  // in the results shown (2 or more characters, longest first), then every number with 3 or
  // more digits, becomes "…", as in the server's check. The server cleans them again.
  function scrubber() {
    var found = new Set();
    Object.keys(results).forEach(function (id) {
      var metrics = new Set();
      (results[id].columns || []).forEach(function (column) {
        if (column.kind === "metric") metrics.add(column.name);
      });
      (results[id].rows || []).forEach(function (row) {
        Object.keys(row).forEach(function (key) {
          var value = row[key];
          if (metrics.has(key) || key.charAt(0) === "_") return;
          if (typeof value === "string" && value.length >= 2) found.add(value);
        });
      });
    });
    var ordered = Array.from(found).sort(function (a, b) {
      return b.length - a.length || (a < b ? -1 : a > b ? 1 : 0);
    });
    return function (text) {
      ordered.forEach(function (value) {
        text = text.split(value).join("…");
      });
      return text.replace(/\d[\d,.]*\d/g, function (number) {
        return number.replace(/\D/g, "").length >= 3 ? "…" : number;
      });
    };
  }
  // The server checked the default filters at publish; this viewer's browser found numbers
  // Prumi didn't deliver (new data, or another filter state), so the viewer is told.
  function showUnverified(report) {
    var flagged = (report && Array.isArray(report.flagged) ? report.flagged : []).slice(0, 20);
    var bar = document.getElementById("prumi-unverified");
    var list = document.getElementById("prumi-unverified-list");
    list.replaceChildren.apply(
      list,
      flagged.map(function (item) {
        var entry = document.createElement("li");
        entry.textContent = String(item.text) + " in " + String(item.path);
        return entry;
      })
    );
    bar.hidden = flagged.length === 0;
  }


  function fetchBlock(id) {
    var sequence = (latest[id] || 0) + 1;
    latest[id] = sequence;
    var body = { filters: {} };
    blocks[id].filters.forEach(function (f) {
      body.filters[f] = values[f];
    });
    fetch(config.api + "/blocks/" + encodeURIComponent(id), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (answer) {
        if (latest[id] !== sequence) return; // a newer request is under way
        if (answer.ok) {
          results[id] = answer.data;
          post("data", { block: id, result: answer.data });
        } else {
          delete results[id];
          var issues = answer.data.issues || [];
          post("error", {
            block: id,
            issue: issues[0] || {
              code: "request.failed",
              severity: "error",
              message: answer.data.error || "The block couldn't be loaded.",
            },
          });
        }
        updateBar();
      })
      .catch(function (error) {
        if (latest[id] !== sequence) return;
        post("error", {
          block: id,
          issue: { code: "request.failed", severity: "error", message: String(error) },
        });
      });
  }

  // Only the blocks that list the filter are fetched again.
  function setFilter(id, value) {
    if (!Object.prototype.hasOwnProperty.call(values, id)) return;
    values[id] = copy(value);
    post("filters", { filters: copy(values) });
    subscribed
      .filter(function (b) {
        return blocks[b].filters.indexOf(id) >= 0;
      })
      .forEach(fetchBlock);
  }

  // Each interaction from the block sets its filter to the clicked row's value; clicking the
  // same value again clears the filter.
  function click(blockId, row) {
    config.interactions
      .filter(function (i) {
        return i.from === blockId;
      })
      .forEach(function (interaction) {
        var f = filterDef(interaction.sets_filter);
        if (!f || f.type !== "dimension" || !row || !(f.dimension in row)) return;
        var value = row[f.dimension];
        var current = values[f.id];
        var same = Array.isArray(current) && current.length === 1 && current[0] === value;
        setFilter(f.id, same ? [] : [value]);
      });
  }

  function fetchValues(id, request) {
    fetch(config.api + "/filters/" + encodeURIComponent(id) + "/values")
      .then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) throw new Error(data.error || "No values");
          post("filterValues", { request: request, values: data.values });
        });
      })
      .catch(function (error) {
        post("filterValues", { request: request, error: String(error.message || error) });
      });
  }

  // Freshness: the earliest data_through among the dashboard's metrics. Details: warnings.
  function updateBar() {
    var earliest = null;
    var notes = [];
    Object.keys(results).forEach(function (id) {
      var result = results[id];
      Object.keys(result.freshness || {}).forEach(function (metric) {
        var day = result.freshness[metric];
        if (day && (earliest === null || day < earliest)) earliest = day;
      });
      if (result.window && result.window.partial) {
        notes.push(id + ": includes the current, partial " + result.window.grain + ".");
      }
      (result.warnings || []).forEach(function (w) {
        notes.push(id + ": " + w.message);
      });
    });
    document.getElementById("prumi-freshness").textContent = earliest
      ? "Data through " + earliest
      : "";
    var list = document.getElementById("prumi-warnings");
    list.replaceChildren.apply(
      list,
      notes.map(function (note) {
        var item = document.createElement("li");
        item.textContent = note;
        return item;
      })
    );
    document.getElementById("prumi-details").hidden = notes.length === 0;
  }
})();
