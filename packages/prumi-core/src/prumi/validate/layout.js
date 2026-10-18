// Layout measurements for the server's page check, run in an isolated world after rendering.
//
// Agents can't see the page they build, so the check measures it: overlapping or clipped
// text, a page wider than the screen, tiny text, bound elements that render nothing. Results
// hold only problem kinds, element paths and boxes, never text, numbers or attribute values
// from the page: company data never reaches the agent.
(function (root) {
  "use strict";

  var SKIP = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEMPLATE: 1, TITLE: 1 };
  var SHAPES = { rect: 1, path: 1, circle: 1, ellipse: 1, line: 1, polyline: 1, polygon: 1, text: 1 };
  var MAX_TEXT_ELEMENTS = 1500;
  var MIN_TEXT_PX = 10;
  var OVERLAP = 0.2;

  function box(r) {
    return { x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) };
  }

  function area(r) {
    return Math.max(0, r.right - r.left) * Math.max(0, r.bottom - r.top);
  }

  // tag#id, or tag.class.class with :nth-of-type when siblings share the tag, up to an id.
  function path(element) {
    var parts = [];
    for (var el = element; el && el.nodeType === 1; el = el.parentElement) {
      var tag = el.tagName.toLowerCase();
      if (tag === "html" || tag === "body") break;
      if (el.id) {
        parts.unshift(tag + "#" + el.id);
        break;
      }
      var cls = typeof el.className === "string" ? el.className.trim().split(/\s+/).filter(Boolean) : [];
      var part = tag + cls.map(function (c) { return "." + c; }).join("");
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
    for (var el = element; el; el = el.parentElement) if (SKIP[el.tagName]) return true;
    return false;
  }

  function rendered(element) {
    if (element.checkVisibility) {
      return element.checkVisibility({ opacityProperty: true, visibilityProperty: true });
    }
    return element.getClientRects().length > 0;
  }

  // Hidden on purpose for sighted readers: the screen-reader-only pattern (a 1 px box that
  // clips its content), or an element clipped away entirely.
  function visuallyHidden(element) {
    var view = element.ownerDocument.defaultView;
    for (var el = element; el && el.nodeType === 1; el = el.parentElement) {
      var s = view.getComputedStyle(el);
      if (/rect\(0(px)?,? 0(px)?,? 0(px)?,? 0(px)?\)/.test(s.clip) || /inset\(50%\)/.test(s.clipPath)) {
        return true;
      }
      var cuts = /hidden|clip/.test(s.overflowX) || /hidden|clip/.test(s.overflowY);
      if (cuts && (el.clientWidth <= 1 || el.clientHeight <= 1)) return true;
    }
    return false;
  }

  function visible(element) {
    return rendered(element) && !visuallyHidden(element);
  }

  // Elements with their own visible text, each with the box of that text.
  function textElements(doc) {
    var found = new Map();
    var walker = doc.createTreeWalker(doc.body || doc.documentElement, 4 /* SHOW_TEXT */);
    var range = doc.createRange();
    for (var node = walker.nextNode(); node && found.size < MAX_TEXT_ELEMENTS; node = walker.nextNode()) {
      var parent = node.parentElement;
      if (!parent || !node.data.trim() || skipped(parent) || !visible(parent)) continue;
      range.selectNodeContents(node);
      var r = range.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      var entry = found.get(parent);
      if (!entry) found.set(parent, { element: parent, rect: { left: r.left, top: r.top, right: r.right, bottom: r.bottom } });
      else {
        entry.rect.left = Math.min(entry.rect.left, r.left);
        entry.rect.top = Math.min(entry.rect.top, r.top);
        entry.rect.right = Math.max(entry.rect.right, r.right);
        entry.rect.bottom = Math.max(entry.rect.bottom, r.bottom);
      }
    }
    return Array.from(found.values()).map(function (e) {
      var r = e.rect;
      e.box = box({ left: r.left, top: r.top, width: r.right - r.left, height: r.bottom - r.top });
      return e;
    });
  }

  function overlaps(texts, add) {
    for (var i = 0; i < texts.length; i++) {
      for (var j = i + 1; j < texts.length; j++) {
        var a = texts[i], b = texts[j];
        if (a.element.contains(b.element) || b.element.contains(a.element)) continue;
        var w = Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left);
        var h = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top);
        if (w <= 0 || h <= 0) continue;
        if (w * h > OVERLAP * Math.min(area(a.rect), area(b.rect))) {
          if (!add("layout.text_overlap", [a, b])) return;
        }
      }
    }
  }

  function outside(inner, outer) {
    return inner.left < outer.left - 1 || inner.top < outer.top - 1 ||
      inner.right > outer.right + 1 || inner.bottom > outer.bottom + 1;
  }

  function clipped(texts, view, add) {
    texts.forEach(function (t) {
      var el = t.element;
      var style = view.getComputedStyle(el);
      if (style.textOverflow === "ellipsis" && el.scrollWidth > el.clientWidth + 1) {
        add("layout.text_clipped", [t, t]);
        return;
      }
      if (el instanceof view.SVGElement) {
        var svg = el.ownerSVGElement;
        while (svg && svg.ownerSVGElement) svg = svg.ownerSVGElement;
        if (svg && view.getComputedStyle(svg).overflow !== "visible" &&
            outside(t.rect, svg.getBoundingClientRect())) {
          add("layout.text_clipped", [t, { element: svg, box: box(svg.getBoundingClientRect()) }]);
        }
        return;
      }
      for (var up = el; up && up !== el.ownerDocument.body; up = up.parentElement) {
        var s = view.getComputedStyle(up);
        var cuts = /hidden|clip/.test(s.overflowX) || /hidden|clip/.test(s.overflowY);
        if (!cuts) continue;
        var r = up.getBoundingClientRect();
        var inner = { left: r.left + up.clientLeft, top: r.top + up.clientTop };
        inner.right = inner.left + up.clientWidth;
        inner.bottom = inner.top + up.clientHeight;
        if (outside(t.rect, inner)) add("layout.text_clipped", [t, { element: up, box: box(r) }]);
        return;
      }
    });
  }

  function tooWide(doc, view, add) {
    var width = doc.documentElement.scrollWidth;
    if (width <= view.innerWidth + 1) return;
    var all = doc.body ? doc.body.querySelectorAll("*") : [];
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var r = el.getBoundingClientRect();
      var parent = el.parentElement && el.parentElement.getBoundingClientRect();
      if (r.right > view.innerWidth + 1 && (!parent || parent.right <= view.innerWidth + 1)) {
        add("layout.too_wide", [{ element: el, box: box(r) }], { width: width, screen: view.innerWidth });
        return;
      }
    }
    add("layout.too_wide", [], { width: width, screen: view.innerWidth });
  }

  function smallText(texts, view, add) {
    texts.forEach(function (t) {
      var el = t.element;
      var size = parseFloat(view.getComputedStyle(el).fontSize) || 0;
      if (el instanceof view.SVGElement && el.getScreenCTM) {
        var m = el.getScreenCTM();
        if (m) size *= Math.sqrt(Math.abs(m.a * m.d - m.b * m.c));
      }
      if (size > 0 && size < MIN_TEXT_PX) add("layout.small_text", [t], { size: Math.round(size * 10) / 10 });
    });
  }

  function hasContent(el, view) {
    var walker = el.ownerDocument.createTreeWalker(el, 5 /* SHOW_ELEMENT | SHOW_TEXT */);
    var range = el.ownerDocument.createRange();
    for (var node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.nodeType === 3) {
        if (!node.data.trim() || !node.parentElement || skipped(node.parentElement)) continue;
        range.selectNodeContents(node);
        var r = range.getBoundingClientRect();
        if (r.width > 0 && r.height > 0 && visible(node.parentElement)) return true;
        continue;
      }
      var b = node.getBoundingClientRect();
      if (b.width <= 0 || b.height <= 0 || !visible(node)) continue;
      if (SHAPES[node.tagName.toLowerCase()] && node instanceof view.SVGElement) return true;
      var s = view.getComputedStyle(node);
      var background = s.backgroundColor && !/rgba\(0, 0, 0, 0\)|transparent/.test(s.backgroundColor);
      if (background || parseFloat(s.borderTopWidth) > 0 || parseFloat(s.borderLeftWidth) > 0) return true;
    }
    return false;
  }

  function emptyBlocks(doc, view, bindings, add) {
    Object.keys(bindings || {}).forEach(function (selector) {
      var matched;
      try {
        matched = doc.querySelectorAll(selector);
      } catch (error) {
        return;
      }
      Array.prototype.forEach.call(matched, function (el) {
        // Not rendered at all (a tooltip until hover, a hidden panel) is on purpose.
        if (!rendered(el)) return;
        var r = el.getBoundingClientRect();
        var zero = r.width <= 0 || r.height <= 0;
        if (zero || !hasContent(el, view)) {
          add("layout.empty_block", [{ element: el, box: box(r) }], {
            blocks: bindings[selector],
            zero: zero,
          });
        }
      });
    });
  }

  // Returns [{code, elements: [{path, box}], ...details}], at most `limit`.
  root.prumiLayout = function (doc, bindings, limit) {
    limit = limit || 20;
    var view = doc.defaultView;
    var found = [];
    var seen = {};
    function add(code, items, details) {
      if (found.length >= limit) return false;
      var elements = items.map(function (i) {
        return { path: path(i.element), box: i.box || box(i.element.getBoundingClientRect()) };
      });
      var key = code + "\u0000" + elements.map(function (e) { return e.path; }).join("\u0000");
      if (seen[key]) return true;
      seen[key] = true;
      var issue = { code: code, elements: elements };
      for (var k in details || {}) issue[k] = details[k];
      found.push(issue);
      return true;
    }
    var texts = textElements(doc);
    tooWide(doc, view, add);
    emptyBlocks(doc, view, bindings, add);
    clipped(texts, view, add);
    smallText(texts, view, add);
    overlaps(texts, add);
    return found;
  };
})(typeof window !== "undefined" ? window : globalThis);
