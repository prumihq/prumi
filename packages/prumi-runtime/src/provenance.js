// Prumi number provenance: is every number a dashboard shows a value Prumi delivered?
//
// One matcher, used in two places: the server loads it into an isolated world next to a page
// it renders at validation (so page code can't reach it), and the runtime runs it after each
// render in the viewer's browser. It only reads the DOM and the values it's given.
//
// A shown number passes when it's a date or a year, a count of periods ("4 weeks"), a number
// from the spec or from result metadata, an axis tick, or, inside an element bound to blocks,
// a value those blocks delivered, rounded to the precision shown (scaled for "%" and K/M/B,
// ignoring currency symbols, separators and signs).
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
  var PERIODS = /^\s*-?\s*(?:days?|weeks?|wks?|months?|mos?|quarters?|qtrs?|years?|yrs?)\b/i;
  var CURRENCY_BEFORE = /(?:[$€£¥₹₩₽¢]|\b[A-Z]{3})\s*$/;

  // What every locale's reader knows: these separators inside a number, and these scales
  // after it. A locale adds its own (see reader), so en-US reads exactly these.
  var SEPARATORS = ".,'   ";
  var SPACES = "    "; // between a number and its scale
  var SUFFIXES = ["%", "‰", "[kKMBT](?![\\p{L}])", "bn(?![\\p{L}])", "mn(?![\\p{L}])",
                  "Mio\\.?", "Mrd\\.?"];
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

  function escapeRegExp(text) {
    return text.replace(/[\\^$.*+?()[\]{}|\/-]/g, "\\$&");
  }

  // The locale's separators and compact scales, as Intl formats them: the group and decimal
  // separators of 1234567.5, and the compact suffix of 1e3, 1e6 and 1e9, as a number and as
  // a currency (some locales use other words there). The scale is the value over the digits
  // shown, so "100万" (1e6 in ja-JP) makes 万 1e4.
  function localeSymbols(locale) {
    var out = { group: "", decimal: "", scales: {} };
    try {
      new Intl.NumberFormat(locale).formatToParts(1234567.5).forEach(function (part) {
        if (part.type === "group") out.group = part.value;
        if (part.type === "decimal") out.decimal = part.value;
      });
      var styles = [
        { notation: "compact" },
        { notation: "compact", style: "currency", currency: "USD" },
      ];
      [1e3, 1e6, 1e9].forEach(function (value) {
        styles.forEach(function (options) {
          var digits = "";
          var suffix = "";
          new Intl.NumberFormat(locale, options).formatToParts(value).forEach(function (part) {
            if (part.type === "integer") digits += part.value;
            if (part.type === "compact") suffix = part.value.trim();
          });
          if (suffix && +digits > 0) out.scales[suffix] = value / +digits;
        });
      });
    } catch (error) {
      // a tag this browser doesn't know: read as en-US
    }
    return out;
  }

  // The reader for a locale: what a number looks like there, and its scales.
  var readers = {};

  function reader(locale) {
    locale = locale || "en-US";
    if (readers[locale]) return readers[locale];
    var symbols = localeSymbols(locale);
    var odd = [symbols.group, symbols.decimal].filter(function (c) {
      return c && SEPARATORS.indexOf(c) < 0 && c !== " ";
    });
    var own = Object.keys(symbols.scales)
      .sort(function (a, b) {
        return b.length - a.length;
      })
      .map(function (suffix) {
        return escapeRegExp(suffix) + (/\p{L}$/u.test(suffix) ? "(?![\\p{L}])" : "");
      });
    var separators = escapeRegExp(SEPARATORS + odd.join(""));
    var scales = {};
    Object.keys(SCALES).forEach(function (k) {
      scales[k] = SCALES[k];
    });
    Object.keys(symbols.scales).forEach(function (k) {
      scales[k] = symbols.scales[k];
    });
    readers[locale] = {
      // A number, not glued to a word on its left, with an optional scale after it.
      number: new RegExp(
        "(?<![\\p{L}\\d_])(\\d(?:[\\d" + separators + "]*\\d)?)(?:[" + SPACES + "]?(" +
          own.concat(SUFFIXES).join("|") + "))?",
        "gu"
      ),
      scales: scales,
      // Group separators other than . and , are removed; an odd decimal separator becomes ".".
      group: symbols.group && odd.indexOf(symbols.group) >= 0 ? symbols.group : "",
      decimal: symbols.decimal && odd.indexOf(symbols.decimal) >= 0 ? symbols.decimal : "",
    };
    return readers[locale];
  }

  // Readings of a number as shown: "1,234" is 1234 in en-US and 1.234 in de-DE.
  function readings(raw, locale) {
    var r = reader(locale);
    var s = raw.replace(/['   ]/g, "");
    if (r.group) s = s.split(r.group).join("");
    if (r.decimal) s = s.split(r.decimal).join(".");
    var commas = s.split(",").length - 1;
    var dots = s.split(".").length - 1;
    if (!commas && !dots) return [{ value: +s, decimals: 0, digits: s }];
    if (commas && dots) {
      var decimal = s.lastIndexOf(",") > s.lastIndexOf(".") ? "," : ".";
      var one = decimalReading(s, decimal);
      return one ? [one] : [];
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

  // The numbers in a piece of text as shown in `locale`, with what's around them and their
  // readings.
  function numbers(text, locale) {
    var r = reader(locale);
    var stripped = text.replace(DATES, function (m) {
      return " ".repeat(m.length);
    });
    var found = [];
    var match;
    r.number.lastIndex = 0;
    while ((match = r.number.exec(stripped))) {
      found.push({
        text: match[0],
        raw: match[1],
        readings: readings(match[1], locale),
        scale: match[2] ? r.scales[match[2]] || r.scales[match[2].replace(".", "")] : 1,
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

  // Why a number that isn't data is allowed anywhere, or null: numbers from the spec and
  // result metadata, years, counts of periods.
  function nonData(number, context) {
    if (number.scale !== 1) return null;
    var rs = number.readings;
    for (var i = 0; i < rs.length; i++) {
      var r = rs[i];
      if (r.decimals !== 0) continue;
      if (context.allowed.has(r.value)) return "metadata";
      if (r.value >= 1900 && r.value <= 2199 && r.digits === number.raw) return "year";
      if (PERIODS.test(number.after) && !CURRENCY_BEFORE.test(number.before)) return "periods";
    }
    return null;
  }

  function deliveredBy(number, sorted) {
    return number.readings.some(function (r) {
      return delivered(r, number.scale, sorted);
    });
  }

  // The blocks of the nearest element a binding selector matches, or null when none does.
  function boundBlocks(element, bindings) {
    var selectors = Object.keys(bindings || {});
    for (var el = element; el; el = el.parentElement || hostOf(el)) {
      if (!el.matches) continue;
      var blocks = [];
      selectors.forEach(function (selector) {
        try {
          if (el.matches(selector)) blocks = blocks.concat(bindings[selector]);
        } catch (error) {
          // not a selector this browser knows: it binds nothing
        }
      });
      if (blocks.length) return blocks;
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

  // The chart a number may be an axis tick of: its nearest axis-marked element or <svg>.
  // Outside both, nothing is a tick: in HTML an evenly spaced list is as easy to make up as
  // any other.
  function chartOf(element) {
    return element.closest ? element.closest('svg, [data-prumi-role="axis"]') : null;
  }

  // Axis ticks: three or more evenly spaced numbers in one chart, at any precision ($280,
  // $286, $292 on a narrow axis). A chart also holds data labels and tooltips, so a number
  // counts when it's part of some run v, v + d, v + 2d among the chart's numbers.
  function tickSeries(candidates) {
    var groups = new Map();
    candidates.forEach(function (c) {
      var chart = chartOf(c.element);
      if (!chart) return;
      if (!groups.has(chart)) groups.set(chart, []);
      groups.get(chart).push(c);
    });
    var ok = new Set();
    var key = function (v) {
      return Number(v).toPrecision(12);
    };
    groups.forEach(function (group) {
      var byValue = new Map();
      group.forEach(function (c) {
        var k = key(c.reading.value * c.number.scale);
        if (!byValue.has(k)) byValue.set(k, { value: Number(k), items: [] });
        byValue.get(k).items.push(c);
      });
      var values = Array.from(byValue.values()).sort(function (a, b) {
        return a.value - b.value;
      });
      if (values.length < 3) return;
      var mark = function (entry) {
        entry.items.forEach(function (c) {
          ok.add(c);
        });
      };
      for (var a = 0; a < values.length; a++) {
        for (var b = a + 1; b < values.length; b++) {
          var third = byValue.get(key(2 * values[b].value - values[a].value));
          if (third) {
            mark(values[a]);
            mark(values[b]);
            mark(third);
          }
        }
      }
    });
    return ok;
  }

  var UNBOUND = "R8.unbound_number";
  var UNVERIFIED = "R8.unverified_number";

  // context: {blocks: {blockId: [delivered values]}, bindings: {selector: [blockIds]},
  // allowed: [numbers from the spec and metadata], locale: the server's display locale}. A number that's data must sit in a bound
  // element and be a value of that binding's blocks: matching against every value on the page
  // lets a computed number through whenever it equals some other value at the precision shown.
  // Returns {flagged: [{text, path, code}], checked: count of numbers read, canvas: bool},
  // with at most `limit` flagged numbers.
  function scan(doc, context, limit) {
    limit = limit || 20;
    var ctx = { allowed: new Set((context.allowed || []).filter(isFinite).map(Number)) };
    var sorted = {};
    Object.keys(context.blocks || {}).forEach(function (id) {
      sorted[id] = sortedValues(context.blocks[id]);
    });
    var unverified = [];
    var checked = 0;
    var ticks = []; // every number in a chart, verified or not: runs are found among them all
    texts(doc).forEach(function (t) {
      numbers(t.text, context.locale).forEach(function (number) {
        checked++;
        var item = { number: number, element: t.element };
        var reading = number.readings[0];
        if (reading && chartOf(t.element)) {
          item.reading = reading; // a run of ticks is judged on every number in the chart
          ticks.push(item);
        }
        if (nonData(number, ctx)) return;
        // A single round number in an axis-marked element is a tick on its own.
        if (number.readings.some(tickLike) && isAxis(t.element)) return;
        var blocks = boundBlocks(t.element, context.bindings);
        if (!blocks) {
          item.code = UNBOUND;
        } else if (
          !blocks.some(function (id) {
            return sorted[id] && deliveredBy(number, sorted[id]);
          })
        ) {
          item.code = UNVERIFIED;
          item.blocks = blocks;
        } else {
          return;
        }
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
        code: item.code,
      };
      if (item.blocks) entry.blocks = item.blocks;
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
  // Returns {values: all of them, blocks: {blockId: values}, allowed}.
  function fromResults(results, locale) {
    var all = [];
    var blocks = {};
    var allowed = [];
    Object.keys(results || {}).forEach(function (id) {
      var result = results[id];
      if (!result) return;
      var values = (blocks[id] = blocks[id] || []);
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
          else if (typeof v === "string") numbersIn(v, allowed, locale);
        });
      });
      take(result.total);
      take(result.others);
      allowed.push((result.rows || []).length);
      Array.prototype.push.apply(all, values);
    });
    return { values: all, blocks: blocks, allowed: allowed };
  }

  // Every number written in the spec (a window's length, top_n) or in labels.
  function fromData(data, out, locale) {
    out = out || [];
    (function walk(v) {
      if (typeof v === "number") out.push(v);
      else if (typeof v === "string") numbersIn(v, out, locale);
      else if (Array.isArray(v)) v.forEach(walk);
      else if (v && typeof v === "object") Object.keys(v).forEach(function (k) {
        walk(v[k]);
      });
    })(data);
    return out;
  }

  function numbersIn(text, out, locale) {
    numbers(text, locale).forEach(function (n) {
      n.readings.forEach(function (r) {
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

  function copied(source, values, limit, locale) {
    limit = limit || 20;
    var sorted = sortedValues(values);
    var doc = new DOMParser().parseFromString(source, "text/html"); // runs no scripts
    var found = [];
    var seen = {};
    function read(text, minimum) {
      numbers(text, locale).forEach(function (number) {
        var match = number.readings.some(function (r) {
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
