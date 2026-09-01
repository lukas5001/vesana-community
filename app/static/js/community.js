// Progressive Verbesserung — ohne JS funktionieren alle Seiten (Formulare sind
// normale GETs/POSTs, Reiter zeigen dann alles untereinander).
(function () {
  "use strict";

  /* ---- Darstellung hell/dunkel ------------------------------------------- */
  var root = document.documentElement;
  function currentTheme() {
    var explicit = root.getAttribute("data-theme");
    if (explicit) return explicit;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-theme-toggle]");
    if (!btn) return;
    var next = currentTheme() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("vesana-community-theme", next); } catch (err) { /* egal */ }
  });

  /* ---- Sofortsuche / Filter ---------------------------------------------- */
  var debounce = null;

  function resultsUrl(overrides) {
    var params = new URLSearchParams(window.location.search);
    Object.keys(overrides).forEach(function (k) {
      var v = overrides[k];
      if (v) { params.set(k, v); } else { params.delete(k); }
    });
    var qs = params.toString();
    return window.location.pathname + (qs ? "?" + qs : "");
  }

  function isSafe(url) {
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
    // Chips/Zähler außerhalb der Ergebnisse mitziehen (Filter-Zeile).
    var freshTools = doc.querySelector("[data-tools]");
    var curTools = document.querySelector("[data-tools]");
    if (freshTools && curTools) {
      var active = document.activeElement;
      var keepValue = active && active.matches("[data-tools] input[type=search]") ? active.value : null;
      curTools.replaceWith(freshTools);
      if (keepValue !== null) {
        var input = document.querySelector("[data-tools] input[type=search]");
        if (input) { input.value = keepValue; input.focus(); input.setSelectionRange(keepValue.length, keepValue.length); }
      }
    }
  }

  function load(url) {
    if (!isSafe(url)) { window.location.href = url; return; }
    fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
      .then(function (r) { return r.text(); })
      .then(function (html) { swapResults(html, url); })
      .catch(function () { window.location.href = url; });
  }

  document.addEventListener("input", function (e) {
    if (e.target.matches("[data-tools] input[type=search]")) {
      clearTimeout(debounce);
      var name = e.target.name;
      var val = e.target.value.trim();
      debounce = setTimeout(function () {
        var o = {}; o[name] = val;
        load(resultsUrl(o));
      }, 220);
    }
  });

  document.addEventListener("change", function (e) {
    if (e.target.matches("[data-tools] select")) {
      var o = {}; o[e.target.name] = e.target.value;
      load(resultsUrl(o));
    }
  });

  document.addEventListener("submit", function (e) {
    var form = e.target.closest("form[data-tools]");
    if (form) { e.preventDefault(); }
    var confirmForm = e.target.closest("form[data-confirm]");
    if (confirmForm && !window.confirm(confirmForm.getAttribute("data-confirm"))) {
      e.preventDefault();
    }
  });

  document.addEventListener("click", function (e) {
    var chip = e.target.closest("[data-tools] a.chip, [data-tools] .view a");
    if (!chip || !chip.getAttribute("href")) return;
    e.preventDefault();
    load(chip.getAttribute("href"));
  });

  /* ---- Reiter (Profilseite) ---------------------------------------------- */
  function showTab(key, push) {
    var tabs = document.querySelectorAll("[data-tab-link]");
    var panels = document.querySelectorAll("[data-tab]");
    if (!tabs.length) return;
    var known = false;
    tabs.forEach(function (t) { if (t.getAttribute("data-tab-link") === key) known = true; });
    if (!known) key = tabs[0].getAttribute("data-tab-link");
    tabs.forEach(function (t) { t.setAttribute("aria-selected", String(t.getAttribute("data-tab-link") === key)); });
    panels.forEach(function (p) { p.hidden = p.getAttribute("data-tab") !== key; });
    if (push) {
      var params = new URLSearchParams(window.location.search);
      params.set("tab", key);
      window.history.replaceState(null, "", window.location.pathname + "?" + params.toString());
    }
  }
  if (document.querySelector("[data-tab-link]")) {
    var params = new URLSearchParams(window.location.search);
    var initial = params.get("tab") || (window.location.hash === "#comments" ? "comments" : null);
    showTab(initial || document.querySelector("[data-tab-link]").getAttribute("data-tab-link"), false);
    document.addEventListener("click", function (e) {
      var link = e.target.closest("[data-tab-link]");
      if (!link) return;
      e.preventDefault();
      showTab(link.getAttribute("data-tab-link"), true);
    });
  }

  /* ---- Kopieren (Icon-Slug) ---------------------------------------------- */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy]");
    if (!btn || !navigator.clipboard) return;
    navigator.clipboard.writeText(btn.getAttribute("data-copy")).then(function () {
      var old = btn.textContent;
      btn.textContent = btn.getAttribute("data-copied") || "✓";
      btn.classList.add("done");
      setTimeout(function () { btn.textContent = old; btn.classList.remove("done"); }, 1400);
    });
  });
})();
