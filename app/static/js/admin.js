// Admin-Shell — ergänzt community.js (Sofortsuche, Reiter, Kopieren, Darstellung).
// Hier nur, was der Admin zusätzlich braucht: ein eigener Bestätigungsdialog statt
// des Browser-Popups (Capture-Phase, damit community.js das Formular nie sieht) und
// die aufklappbare Seitenleiste am Handy. Ohne JS bleibt alles bedienbar —
// dann fragt niemand nach, aber jede Aktion ist ein normaler POST.
(function () {
  "use strict";

  /* ---- Bestätigung (kein Browser-Popup) ---------------------------------- */
  var dialog = document.querySelector("[data-confirm-dialog]");
  var pendingForm = null;

  function askConfirm(form) {
    if (!dialog || typeof dialog.showModal !== "function") {
      form.submit();
      return;
    }
    pendingForm = form;
    var text = dialog.querySelector("[data-confirm-text]");
    var ok = dialog.querySelector("[data-confirm-ok]");
    if (text) text.textContent = form.getAttribute("data-confirm") || "";
    if (ok) ok.textContent = form.getAttribute("data-confirm-ok") || ok.getAttribute("data-default") || ok.textContent;
    dialog.showModal();
    if (ok) ok.focus();
  }

  document.addEventListener("submit", function (e) {
    var form = e.target.closest("form[data-confirm]");
    if (!form || form.hasAttribute("data-confirmed")) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    askConfirm(form);
  }, true);

  if (dialog) {
    var okBtn = dialog.querySelector("[data-confirm-ok]");
    var cancelBtn = dialog.querySelector("[data-confirm-cancel]");
    if (okBtn) okBtn.setAttribute("data-default", okBtn.textContent);
    if (okBtn) okBtn.addEventListener("click", function () {
      var form = pendingForm;
      pendingForm = null;
      dialog.close();
      if (!form) return;
      form.setAttribute("data-confirmed", "1");
      // form.submit() löst kein submit-Ereignis aus — community.js bleibt außen vor.
      form.submit();
    });
    if (cancelBtn) cancelBtn.addEventListener("click", function () { pendingForm = null; dialog.close(); });
    dialog.addEventListener("click", function (e) { if (e.target === dialog) { pendingForm = null; dialog.close(); } });
    dialog.addEventListener("cancel", function () { pendingForm = null; });
  }

  /* ---- Seitenleiste am Handy --------------------------------------------- */
  var shell = document.querySelector("[data-shell]");
  document.addEventListener("click", function (e) {
    if (!shell) return;
    if (e.target.closest("[data-side-toggle]")) {
      if (shell.hasAttribute("data-side-open")) shell.removeAttribute("data-side-open");
      else shell.setAttribute("data-side-open", "");
      return;
    }
    if (shell.hasAttribute("data-side-open") && !e.target.closest(".ad-side")) {
      shell.removeAttribute("data-side-open");
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && shell && shell.hasAttribute("data-side-open")) shell.removeAttribute("data-side-open");
  });

  /* ---- Tabellen am Handy: Spaltenname an jede Zelle ------------------------ */
  function labelCells(scope) {
    (scope || document).querySelectorAll("table.reg--admin").forEach(function (table) {
      var heads = Array.prototype.map.call(table.querySelectorAll("thead th"), function (th) { return th.textContent.trim(); });
      table.querySelectorAll("tbody tr").forEach(function (tr) {
        Array.prototype.forEach.call(tr.children, function (td, i) {
          if (heads[i] && !td.hasAttribute("data-l")) td.setAttribute("data-l", heads[i]);
        });
      });
    });
  }
  labelCells();
  // Nach einem Sofortsuche-Swap (community.js ersetzt [data-results]) erneut beschriften.
  var results = document.querySelector("[data-results]");
  if (results && window.MutationObserver) {
    new MutationObserver(function () { labelCells(); }).observe(document.body, { childList: true, subtree: true });
  }

  /* ---- Meldungen blenden sich aus ------------------------------------------ */
  document.querySelectorAll(".flash--ok, .flash--info").forEach(function (el) {
    setTimeout(function () { el.style.transition = "opacity .4s"; el.style.opacity = "0"; }, 6000);
    setTimeout(function () { el.remove(); }, 6600);
  });
})();
