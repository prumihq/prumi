// Prumi's home page: the search box and the copy button. Everything it needs is already on
// the page; it never calls the server.
(function () {
  "use strict";

  function searchText(card) {
    // The card's own text, plus the labels folded into "+N" (they're in its title).
    var more = card.querySelector(".labels .more");
    return (card.textContent + " " + (more ? more.title : "")).toLowerCase();
  }

  function setUpSearch() {
    var input = document.getElementById("search");
    if (!input) return;
    var cards = Array.prototype.slice.call(document.querySelectorAll("#cards .card"));
    var texts = cards.map(searchText);
    var none = document.getElementById("no-match");
    input.addEventListener("input", function () {
      var words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
      var shown = 0;
      cards.forEach(function (card, i) {
        var match = words.every(function (word) { return texts[i].indexOf(word) !== -1; });
        card.hidden = !match;
        if (match) shown += 1;
      });
      none.hidden = shown > 0;
    });
  }

  function selectText(element) {
    var range = document.createRange();
    range.selectNodeContents(element);
    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function setUpCopy() {
    document.querySelectorAll("button[data-copy]").forEach(function (button) {
      var source = document.getElementById(button.getAttribute("data-copy"));
      var label = button.textContent;
      var timer = null;
      function show(text) {
        button.textContent = text;
        clearTimeout(timer);
        timer = setTimeout(function () { button.textContent = label; }, 2000);
      }
      button.addEventListener("click", function () {
        var text = source.textContent;
        if (!navigator.clipboard) {
          selectText(source);
          show("Selected");
          return;
        }
        navigator.clipboard.writeText(text).then(
          function () { show("Copied"); },
          function () { selectText(source); show("Selected"); }
        );
      });
    });
  }

  setUpSearch();
  setUpCopy();
})();
