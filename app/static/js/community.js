// Progressive enhancement for browse + Q&A: instant search / sort / filter with
// no full page reload. Works on any page that has a `.filters` form and a
// `[data-results]` container. Without JS the forms still work (noscript submit +
// filter links are plain GETs).
(function () {
  "use strict";

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
    return window.location.pathname + (qs ? "?" + qs : "");
  }

  function isSafe(url) {
    // same-origin path only ("/...", but not "//host")
    return url.charAt(0) === "/" && url.charAt(1) !== "/";
  }

  function swapResults(html, url) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var fresh = doc.querySelector("[data-results]");
    var current = document.querySelector("[data-results]");
    if (fresh && current) {
      current.replaceWith(fresh);
      window.history.replaceState(null, "", url);
    }
  }

  function load(url) {
    if (!isSafe(url)) {
      window.location.href = url;
      return;
    }
    fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
      .then(function (r) { return r.text(); })
      .then(function (html) { swapResults(html, url); })
      .catch(function () { window.location.href = url; });
  }

  document.addEventListener("input", function (e) {
    if (e.target.matches(".filters input[type=search]")) {
      clearTimeout(debounce);
      var name = e.target.name;
      var val = e.target.value.trim();
      debounce = setTimeout(function () {
        var o = {};
        o[name] = val;
        load(resultsUrl(o));
      }, 250);
    }
  });

  document.addEventListener("change", function (e) {
    if (e.target.matches(".filters select")) {
      var o = {};
      o[e.target.name] = e.target.value;
      load(resultsUrl(o));
    }
  });

  document.addEventListener("click", function (e) {
    var chip = e.target.closest(".filters .chip");
    if (!chip || !chip.getAttribute("href")) return;
    e.preventDefault();
    var row = chip.closest(".filters-row");
    if (row) {
      row.querySelectorAll(".chip").forEach(function (c) { c.classList.remove("chip-active"); });
      chip.classList.add("chip-active");
    }
    load(chip.getAttribute("href"));
  });
})();
