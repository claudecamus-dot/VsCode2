// Comportement générique pour tous les champs en autosave HTMX de l'app
// (synthèse, recommandations, entretiens, trame — même motif partout :
// hx-post sur un champ, hx-target vers un <span class="saved">). Un seul
// script délégué sur <body> plutôt qu'un handler par champ/gabarit :
// US9.15 (le badge "✓ enregistré" reste affiché indéfiniment) et US9.14
// (un échec HTMX 4xx/5xx ne donne aujourd'hui aucun retour visible).
(function () {
  var CLEAR_DELAY_MS = 3000;
  var timers = new WeakMap();

  function scheduleClear(el) {
    var existing = timers.get(el);
    if (existing) clearTimeout(existing);
    timers.set(el, setTimeout(function () {
      el.textContent = "";
      el.classList.remove("error");
      timers.delete(el);
    }, CLEAR_DELAY_MS));
  }

  document.body.addEventListener("htmx:afterSwap", function (evt) {
    var target = evt.detail.target;
    if (!target.classList || !target.classList.contains("saved")) return;
    if (target.textContent.trim().indexOf("✓") === 0) {
      scheduleClear(target);
    }
  });

  function showError(evt, message) {
    var target = evt.detail.target;
    if (!target || !target.classList || !target.classList.contains("saved")) return;
    var existing = timers.get(target);
    if (existing) clearTimeout(existing);
    target.textContent = message;
    target.classList.add("error");
    scheduleClear(target);
  }

  document.body.addEventListener("htmx:responseError", function (evt) {
    showError(evt, "⚠ échec de l'enregistrement");
  });
  document.body.addEventListener("htmx:sendError", function (evt) {
    showError(evt, "⚠ connexion perdue");
  });
})();
