// Progressive enhancement for the browse page: instant search / sort / filter
// without a full page reload. Without JS the form still works (noscript Apply
// button + filter links are plain GETs).
(function () {
  "use strict";

  var RESULTS = "#browse-results";
  var debounce = null;

  function resultsUrl(overrides) {
    var params = new URLSearchParams(window.location.search);
    Object.keys(overrides).forEach(function (k) {
      var v = overrides[k];
      if (v) {
        params.set(k, v);
      } else {
        params.delete(k);
      }
    });
    var qs = params.toString();
    return "/browse" + (qs ? "?" + qs : "");
  }

  function swapResults(html, url) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var fresh = doc.querySelector(RESULTS);
    var current = document.querySelector(RESULTS);
    if (fresh && current) {
      current.replaceWith(fresh);
      window.history.replaceState(null, "", url);
    }
  }

  function load(url) {
    fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
      .then(function (r) { return r.text(); })
      .then(function (html) { swapResults(html, url); })
      .catch(function () { window.location.href = url; });
  }

  document.addEventListener("input", function (e) {
    if (e.target.matches(".filters input[name=q]")) {
      clearTimeout(debounce);
      var val = e.target.value.trim();
      debounce = setTimeout(function () { load(resultsUrl({ q: val })); }, 250);
    }
  });

  document.addEventListener("change", function (e) {
    if (e.target.matches(".filters select[name=sort]")) {
      load(resultsUrl({ sort: e.target.value }));
    }
  });

  document.addEventListener("click", function (e) {
    var chip = e.target.closest(".filters .chip");
    if (!chip || !chip.getAttribute("href")) return;
    e.preventDefault();
    // Reflect the new active state immediately within this chip's group.
    var row = chip.closest(".filters-row");
    if (row) {
      row.querySelectorAll(".chip").forEach(function (c) { c.classList.remove("chip-active"); });
      chip.classList.add("chip-active");
    }
    load(chip.getAttribute("href"));
  });
})();
