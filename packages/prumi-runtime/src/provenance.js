// Prumi number provenance: is every number a dashboard shows a value Prumi delivered?
//
// One matcher, used in two places: the server loads it into an isolated world next to a page
// it renders at validation (so page code can't reach it), and the runtime runs it after each
// render in the viewer's browser. It only reads the DOM and the values it's given.
//
// A shown number passes when it's a delivered value rounded to the precision shown (scaled for
// "%" and K/M/B, ignoring currency symbols, separators and signs), a date or a year, a count
// of periods ("4 weeks"), a number from the spec or from result metadata, or an axis tick.
(function (root) {
  "use strict";

  var SKIP = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEMPLATE: 1 };
  var MONTH = "(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\\.?";
  // Removed from text before numbers are read.
  var DATES = new RegExp(
    [
      "\\b\\d{4}-\\d{2}-\\d{2}(?:[T ]\\d{2}:\\d{2}(?::\\d{2}(?:\\.\\d+)?)?Z?)?\\b",
      "\\b\\d{4}-\\d{2}\\b",
      "\\b\\d{4}/\\d{1,2}(?:/\\d{1,2})?\\b",
      "\\b\\d{1,2}[/.]\\d{1,2}[/.]\\d{2,4}\\b",
      "\\b" + MONTH + "\\s+\\d{1,2}(?:st|nd|rd|th)?(?:,?\\s+\\d{4})?(?!\\d)",
      "\\b\\d{1,2}(?:st|nd|rd|th)?\\.?\\s+" + MONTH + "(?:,?\\s+\\d{4})?(?!\\d)",
      "\\b" + MONTH + "\\s+'?\\d{2,4}\\b",
      "\\b(?:fy|q[1-4])\\s*'?\\d{2,4}\\b",
      "\\b\\d{4}\\s*-?\\s*(?:q[1-4]|h[12]|w\\d{1,2})\\b",
      "\\bq[1-4]\\b",
      "\\b(?:w|wk|week)\\s*\\d{1,2}\\b",
      "\\b\\d{1,2}:\\d{2}(?::\\d{2})?\\s*(?:am|pm)?",
      "'\\d{2}\\b",
    ].join("|"),
    "gi"
  );
  // A number, not glued to a word on its left, with an optional scale after it.
  var NUMBER =
    /(?<![\p{L}\d_])(\d(?:[\d.,'   ]*\d)?)(?:[    ]?(%|‰|[kKMBT](?![\p{L}])|bn(?![\p{L}])|mn(?![\p{L}])|Mio\.?|Mrd\.?))?/gu;
  var SCALES = {
    "%": 0.01,
    "‰": 0.001,
    k: 1e3,
    K: 1e3,
    M: 1e6,
    mn: 1e6,
    Mio: 1e6,
    "Mio.": 1e6,
    B: 1e9,
    bn: 1e9,
    Mrd: 1e9,
    "Mrd.": 1e9,
    T: 1e12,
  };
  var PERIODS = /^\s*-?\s*(?:days?|weeks?|wks?|months?|mos?|quarters?|qtrs?|years?|yrs?)\b/i;
  var CURRENCY_BEFORE = /(?:[$€£¥₹₩₽¢]|\b[A-Z]{3})\s*$/;

  // Readings of a number as shown: "1,234" is 1234 in en-US and 1.234 in de-DE.
  function readings(raw) {
    var s = raw.replace(/['   ]/g, "");
    var commas = s.split(",").length - 1;
    var dots = s.split(".").length - 1;
    if (!commas && !dots) return [{ value: +s, decimals: 0, digits: s }];
    if (commas && dots) {
      var decimal = s.lastIndexOf(",") > s.lastIndexOf(".") ? "," : ".";
      var r = decimalReading(s, decimal);
      return r ? [r] : [];
    }
    var sep = commas ? "," : ".";
    var parts = s.split(sep);
    if (parts.length > 2) {
      var grouped = groupReading(parts);
      return grouped ? [grouped] : [];
    }
    var out = [];
    if (parts[1].length === 3) {
      var g = groupReading(parts);
      if (g) out.push(g);
    }
    out.push(decimalReading(s, sep));
    return out.filter(Boolean);
  }

  function groupReading(parts) {
    for (var i = 1; i < parts.length; i++) if (parts[i].length !== 3) return null;
    var digits = parts.join("");
    return { value: +digits, decimals: 0, digits: digits };
  }

  function decimalReading(s, decimal) {
    var group = decimal === "," ? "." : ",";
    var i = s.lastIndexOf(decimal);
    var whole = s.slice(0, i);
    var fraction = s.slice(i + 1);
    if (fraction.indexOf(group) >= 0 || fraction.indexOf(decimal) >= 0) return null;
    var wholeParts = whole.split(group);
    for (var k = 1; k < wholeParts.length; k++) if (wholeParts[k].length !== 3) return null;
    whole = wholeParts.join("");
    return { value: +(whole + "." + fraction), decimals: fraction.length, digits: whole + fraction };
  }

  function significant(digits) {
    return digits.replace(/^0+/, "").replace(/0+$/, "").length;
  }

  // The numbers in a piece of text, with what's around them.
  function numbers(text) {
    var stripped = text.replace(DATES, function (m) {
      return " ".repeat(m.length);
    });
    var found = [];
    var match;
    NUMBER.lastIndex = 0;
    while ((match = NUMBER.exec(stripped))) {
      found.push({
        text: match[0],
        raw: match[1],
        scale: match[2] ? SCALES[match[2]] || SCALES[match[2].replace(".", "")] : 1,
        suffix: match[2] || "",
        before: stripped.slice(0, match.index),
        after: stripped.slice(match.index + match[0].length),
      });
    }
    return found;
  }

  // Sorted absolute values, for binary search.
  function sortedValues(values) {
    var out = [];
    (values || []).forEach(function (v) {
      if (typeof v === "number" && isFinite(v)) out.push(Math.abs(v));
    });
    return out.sort(function (a, b) {
      return a - b;
    });
  }

  function anyBetween(sorted, lo, hi) {
    var a = 0;
    var b = sorted.length;
    while (a < b) {
      var mid = (a + b) >> 1;
      if (sorted[mid] < lo) a = mid + 1;
      else b = mid;
    }
    return a < sorted.length && sorted[a] <= hi;
  }

  function delivered(reading, scale, sorted) {
    var half = 0.5 * Math.pow(10, -reading.decimals);
    var slack = 1e-9 * Math.max(1, reading.value);
    var lo = (reading.value - half - slack) * scale;
    var hi = (reading.value + half + slack) * scale;
    return anyBetween(sorted, Math.min(lo, hi), Math.max(lo, hi));
  }

  function tickLike(reading) {
    return significant(reading.digits) <= 2;
  }

  // Why a number is allowed, or null.
  function verdict(number, context, sorted) {
    var rs = readings(number.raw);
    if (!rs.length) return null;
    var plain = number.scale === 1;
    for (var i = 0; i < rs.length; i++) {
      var r = rs[i];
      if (delivered(r, number.scale, sorted)) return "delivered";
      if (plain && r.decimals === 0) {
        if (context.allowed.has(r.value)) return "metadata";
        if (r.value >= 1900 && r.value <= 2199 && r.digits === number.raw) return "year";
        if (PERIODS.test(number.after) && !CURRENCY_BEFORE.test(number.before)) return "periods";
      }
    }
    return null;
  }

  function isAxis(element) {
    for (var el = element; el; el = el.parentElement || hostOf(el)) {
      if (el.getAttribute && el.getAttribute("data-prumi-role") === "axis") return true;
    }
    return false;
  }

  function hostOf(node) {
    var r = node.getRootNode && node.getRootNode();
    return r && r.host ? r.host : null;
  }

  function path(element) {
    var parts = [];
    for (var el = element; el && el.nodeType === 1; el = el.parentElement || hostOf(el)) {
      var tag = el.tagName.toLowerCase();
      if (tag === "html" || tag === "body") break;
      if (el.id) {
        parts.unshift(tag + "#" + el.id);
        break;
      }
      var cls = typeof el.className === "string" ? el.className.trim().split(/\s+/)[0] : "";
      var part = tag + (cls ? "." + cls : "");
      var parent = el.parentElement;
      if (parent) {
        var same = Array.prototype.filter.call(parent.children, function (c) {
          return c.tagName === el.tagName;
        });
        if (same.length > 1) part += ":nth-of-type(" + (same.indexOf(el) + 1) + ")";
      }
      parts.unshift(part);
    }
    return parts.join(" > ") || element.tagName.toLowerCase();
  }

  function skipped(element) {
    for (var el = element; el; el = el.parentElement || hostOf(el)) {
      if (SKIP[el.tagName]) return true;
    }
    return false;
  }

  // Every piece of text a viewer can see now or later (tooltips): text nodes, including SVG
  // text, and title attributes. Open shadow roots are read too.
  function texts(doc) {
    var out = [];
    (function walk(rootNode) {
      var walker = doc.createTreeWalker(rootNode, 5 /* SHOW_ELEMENT | SHOW_TEXT */);
      for (var node = walker.currentNode; node; node = walker.nextNode()) {
        if (node.nodeType === 3) {
          var parent = node.parentElement || hostOf(node);
          if (parent && !skipped(parent) && /\d/.test(node.data)) {
            out.push({ text: node.data, element: parent });
          }
        } else if (node.nodeType === 1) {
          var title = node.getAttribute("title");
          if (title && /\d/.test(title) && !skipped(node)) out.push({ text: title, element: node });
          if (node.shadowRoot) walk(node.shadowRoot);
        }
      }
    })(doc.documentElement);
    return out;
  }

  // Axis ticks without the marker: at least three tick-like numbers in one chart (the same
  // <svg>, or the same parent element) that are evenly spaced, like 0, 50K, 100K.
  function tickSeries(candidates) {
    var groups = new Map();
    candidates.forEach(function (c) {
      var key = c.element.closest ? c.element.closest("svg") || c.element.parentElement : null;
      if (!key) return;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(c);
    });
    var ok = new Set();
    groups.forEach(function (group) {
      var values = [];
      group.forEach(function (c) {
        var v = c.reading.value * c.number.scale;
        if (values.indexOf(v) < 0) values.push(v);
      });
      if (values.length < 3) return;
      values.sort(function (a, b) {
        return a - b;
      });
      var step = values[1] - values[0];
      for (var i = 2; i < values.length; i++) {
        if (Math.abs(values[i] - values[i - 1] - step) > 1e-9 * Math.max(1, Math.abs(step))) return;
      }
      group.forEach(function (c) {
        ok.add(c);
      });
    });
    return ok;
  }

  // context: {values: [delivered numbers], allowed: [numbers from the spec and metadata]}
  // Returns {flagged: [{text, path}], checked: count of numbers read, canvas: bool}, with at
  // most `limit` flagged numbers.
  function scan(doc, context, limit) {
    limit = limit || 20;
    var sorted = sortedValues(context.values);
    var ctx = { allowed: new Set((context.allowed || []).filter(isFinite).map(Number)) };
    var unverified = [];
    var checked = 0;
    var ticks = []; // every tick-like number, verified or not: a series is judged whole
    texts(doc).forEach(function (t) {
      numbers(t.text).forEach(function (number) {
        checked++;
        var item = { number: number, element: t.element };
        var tick = readings(number.raw).filter(tickLike)[0];
        if (tick) {
          item.reading = tick;
          ticks.push(item);
        }
        if (verdict(number, ctx, sorted)) return;
        if (tick && isAxis(t.element)) return;
        unverified.push(item);
      });
    });
    var series = tickSeries(ticks);
    var flagged = [];
    var seen = {};
    unverified.forEach(function (item) {
      if (series.has(item)) return;
      var symbol = item.number.before.match(/[$€£¥₹₩₽¢]\s*$/);
      var entry = {
        text: (symbol ? symbol[0].trim() : "") + item.number.text.trim(),
        path: path(item.element),
      };
      var key = entry.text + "\u0000" + entry.path;
      if (seen[key] || flagged.length >= limit) return;
      seen[key] = true;
      flagged.push(entry);
    });
    return { flagged: flagged, checked: checked, canvas: !!doc.querySelector("canvas") };
  }

  // The numbers a block result delivers: every metric value in rows, total, others and the
  // comparison fields. And what else a page may show: row counts and numbers in dimension
  // values (a "Store 12" category).
  function fromResults(results) {
    var values = [];
    var allowed = [];
    Object.keys(results || {}).forEach(function (id) {
      var result = results[id];
      if (!result) return;
      var metrics = (result.columns || [])
        .filter(function (c) {
          return c.kind === "metric";
        })
        .map(function (c) {
          return c.name;
        });
      var take = function (row) {
        if (!row) return;
        metrics.forEach(function (m) {
          values.push(row[m]);
        });
      };
      (result.rows || []).forEach(function (row) {
        take(row);
        take(row._previous);
        take(row._delta);
        take(row._delta_pct);
        Object.keys(row).forEach(function (key) {
          if (key.charAt(0) === "_" || metrics.indexOf(key) >= 0) return;
          var v = row[key];
          if (typeof v === "number") allowed.push(v);
          else if (typeof v === "string") numbersIn(v, allowed);
        });
      });
      take(result.total);
      take(result.others);
      allowed.push((result.rows || []).length);
    });
    return { values: values, allowed: allowed };
  }

  // Every number written in the spec (a window's length, top_n) or in labels.
  function fromData(data, out) {
    out = out || [];
    (function walk(v) {
      if (typeof v === "number") out.push(v);
      else if (typeof v === "string") numbersIn(v, out);
      else if (Array.isArray(v)) v.forEach(walk);
      else if (v && typeof v === "object") Object.keys(v).forEach(function (k) {
        walk(v[k]);
      });
    })(data);
    return out;
  }

  function numbersIn(text, out) {
    numbers(text).forEach(function (n) {
      readings(n.raw).forEach(function (r) {
        out.push(r.value);
      });
    });
  }

  // Numbers in a page's source that equal a delivered value: copied into the page instead of
  // coming through prumi.on, so they go stale. Honest pages render data numbers from
  // callbacks, so their source holds none. Text needs 3+ significant digits and script
  // literals 4+, so round layout constants (1200) don't count; attributes (SVG coordinates)
  // aren't read. Returns [{text, line}], at most `limit`.
  var COPIED_IN_TEXT = 3;
  var COPIED_IN_SCRIPT = 4;

  function copied(source, values, limit) {
    limit = limit || 20;
    var sorted = sortedValues(values);
    var doc = new DOMParser().parseFromString(source, "text/html"); // runs no scripts
    var found = [];
    var seen = {};
    function read(text, minimum) {
      numbers(text).forEach(function (number) {
        var match = readings(number.raw).some(function (r) {
          if (significant(r.digits) < minimum) return false;
          var year = r.decimals === 0 && r.value >= 1900 && r.value <= 2199 && r.digits === number.raw;
          return !(number.scale === 1 && year) && delivered(r, number.scale, sorted);
        });
        if (!match) return;
        var symbol = number.before.match(/[$€£¥₹₩₽¢]\s*$/);
        var text = (symbol ? symbol[0].trim() : "") + number.text.trim();
        if (seen[text] || found.length >= limit) return;
        seen[text] = true;
        var at = source.indexOf(number.text.trim());
        found.push({ text: text, line: at < 0 ? null : source.slice(0, at).split("\n").length });
      });
    }
    var walker = doc.createTreeWalker(doc.documentElement, 4 /* SHOW_TEXT */);
    for (var node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.parentElement && !skipped(node.parentElement)) read(node.data, COPIED_IN_TEXT);
    }
    Array.prototype.forEach.call(doc.querySelectorAll("script"), function (script) {
      read(script.textContent, COPIED_IN_SCRIPT);
    });
    return found;
  }

  // What the rendered page has that the scan can't read: frames and images (img, SVG image,
  // CSS background images).
  function unreadable(doc) {
    var image = !!doc.querySelector("img, image, picture, input[type=image]");
    if (!image) {
      var view = doc.defaultView;
      image = Array.prototype.some.call(doc.querySelectorAll("*"), function (el) {
        return /url\(/.test(view.getComputedStyle(el).backgroundImage || "");
      });
    }
    return { iframe: !!doc.querySelector("iframe, frame"), image: image };
  }

  root.prumiProvenance = Object.freeze({
    scan: scan,
    copied: copied,
    unreadable: unreadable,
    fromResults: fromResults,
    fromData: fromData,
    numbers: numbers,
    readings: readings,
  });
})(typeof window !== "undefined" ? window : globalThis);
