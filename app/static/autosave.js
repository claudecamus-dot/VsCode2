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
    var first = target.textContent.trim().charAt(0);
    // ✓ ET ⚠ s'effacent au même délai — un ⚠ serveur qui restait affiché à vie
    // divergeait des erreurs réseau du même slot (revue adversariale 2026-07-23).
    if (first === "✓" || first === "⚠") {
      target.classList.toggle("error", first === "⚠");
      scheduleClear(target);
    }
  });

  // Validation HTML5 en échec sur un form htmx : htmx N'ÉMET PAS la requête et
  // se tait (htmx:validation:halted) — sans ce handler, vider un champ requis
  // d'une ligne en autosave ne donnait NI requête NI message : l'utilisateur
  // croyait sa saisie enregistrée (revue adversariale 2026-07-23).
  document.body.addEventListener("htmx:validation:halted", function (evt) {
    var form = evt.target;
    if (!form || !form.querySelector) return;
    var slot = form.querySelector(".saved");
    if (!slot) return;
    var existing = timers.get(slot);
    if (existing) clearTimeout(existing);
    slot.textContent = "⚠ champ requis manquant — rien n'est enregistré";
    slot.classList.add("error");
    scheduleClear(slot);
  });

  function showError(evt, message) {
    var target = evt.detail.target;
    if (!target || !target.classList) return;
    // Cible .status-ind (autosave des réponses de capture — revue UX 2026-07-23
    // P1-2 : l'échec y était totalement invisible) : le span porte le badge de
    // statut ET l'horodatage « enr. HH:MM » (lui aussi en .saved) — l'erreur
    // s'affiche dans un enfant DÉDIÉ .autosave-err pour n'écraser ni l'un ni
    // l'autre (revue adversariale 2026-07-23 : réutiliser .saved détruisait
    // l'horodatage). Le slot est balayé au prochain swap réussi du badge.
    if (target.classList.contains("status-ind")) {
      var slot = target.querySelector(".autosave-err");
      if (!slot) {
        slot = document.createElement("span");
        slot.className = "saved autosave-err";
        target.appendChild(slot);
      }
      target = slot;
    } else if (!target.classList.contains("saved")) {
      return;
    }
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
