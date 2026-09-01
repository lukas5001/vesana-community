// Läuft synchron im <head>, damit die gespeicherte Darstellung vor dem ersten
// Bild greift (kein Aufblitzen). Ohne Speicherwert entscheidet das System
// (prefers-color-scheme) — dann bleibt html ohne data-theme.
(function () {
  "use strict";
  var v = null;
  try {
    // ?theme=light|dark setzt die Darstellung und merkt sie sich (teilbarer Link).
    var q = new URLSearchParams(window.location.search).get("theme");
    if (q === "light" || q === "dark") {
      v = q;
      try { localStorage.setItem("vesana-community-theme", v); } catch (e) { /* egal */ }
    } else {
      v = localStorage.getItem("vesana-community-theme");
    }
  } catch (e) { /* Speicher gesperrt: System-Darstellung */ }
  if (v === "light" || v === "dark") {
    document.documentElement.setAttribute("data-theme", v);
  }
})();
