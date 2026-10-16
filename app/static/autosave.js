// Comportement générique pour tous les champs en autosave HTMX de l'app
// (synthèse, recommandations, entretiens, trame — même motif partout :
// hx-post sur un champ, hx-target vers un <span class="saved">). Un seul
// script délégué sur <body> plutôt qu'un handler par champ/gabarit :
// US9.15 (le badge "✓ enregistré" reste affiché indéfiniment) et US9.14
// (un échec HTMX 4xx/5xx ne donne aujourd'hui aucun retour visible).
(function () {
  var CLEAR_DELAY_MS = 3000;
  var timers = new WeakMap();
  // Slots portant un message PERSISTANT (404/401) : à effacer au prochain succès.
  // Valeur : l'élément (evt.detail.elt) dont l'échec a posé le message, ou null.
  var persistants = new WeakMap();

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
    marquerArbre(target);  // slots INSÉRÉS par ce swap (absents au chargement)
    if (!target.classList || !target.classList.contains("saved")) return;
    persistants.delete(target);  // le swap remplace le message persistant
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
    if (persistants.has(slot)) return;  // ne jamais remplacer un message persistant
    var existing = timers.get(slot);
    if (existing) clearTimeout(existing);
    slot.textContent = "⚠ champ requis manquant — rien n'est enregistré";
    slot.classList.add("error");
    scheduleClear(slot);
  });

  // Accessibilité (posée ici faute de droit sur les gabarits) : chaque slot de
  // statut est une région live, posée AVANT tout remplissage pour que le texte
  // ajouté ensuite soit annoncé.
  function marquerLive(el) {
    if (!el || !el.setAttribute) return;
    if (!el.getAttribute("role")) el.setAttribute("role", "status");
    if (!el.getAttribute("aria-live")) el.setAttribute("aria-live", "polite");
  }
  // Le slot .status-ind des réponses de capture reste NON live (prudence) : seul
  // son enfant .autosave-err l'est (posé dans showError).
  function marquerArbre(racine) {
    if (!racine || !racine.classList) return;
    if (racine.classList.contains("saved") && !racine.classList.contains("status-ind")) marquerLive(racine);
    if (racine.querySelectorAll) {
      Array.prototype.forEach.call(racine.querySelectorAll(".saved"), function (el) {
        if (!el.classList.contains("status-ind")) marquerLive(el);
      });
    }
  }
  Array.prototype.forEach.call(document.querySelectorAll(".saved"), marquerLive);
  document.body.addEventListener("htmx:load", function (evt) { marquerArbre(evt.target); });

  // opts.persistant : le message RESTE affiché (pas d'effacement à 3 s) ;
  // opts.lien : {texte, href} ajouté après le message (nouvel onglet, pour ne
  // pas perdre la saisie de l'onglet courant). Contenu bâti au DOM, jamais innerHTML.
  function showError(evt, message, opts) {
    opts = opts || {};
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
        marquerLive(slot);
        target.appendChild(slot);
      }
      target = slot;
    } else if (!target.classList.contains("saved")) {
      return;
    }
    // Un message persistant (404/401) n'est jamais remplacé par un transitoire
    // (500/réseau) : il s'effacerait à 3 s alors que rien n'est enregistré.
    if (!opts.persistant && persistants.has(target)) return;
    marquerLive(target);
    var existing = timers.get(target);
    if (existing) { clearTimeout(existing); timers.delete(target); }
    target.textContent = message;
    if (opts.lien) {
      var a = document.createElement("a");
      a.setAttribute("href", opts.lien.href);
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener");
      a.textContent = opts.lien.texte;
      target.appendChild(a);
    }
    target.classList.add("error");
    if (opts.persistant) persistants.set(target, evt.detail.elt || null);
    else { persistants.delete(target); scheduleClear(target); }
  }

  // Succès 2xx (204, réponse vide, swap none/outerHTML : aucun afterSwap sur le
  // slot) APRÈS un message persistant : l'enregistrement a réussi, le message
  // « fiche introuvable » / « session expirée » serait un mensonge.
  document.body.addEventListener("htmx:afterRequest", function (evt) {
    var d = evt.detail || {};
    var statut = d.xhr ? d.xhr.status : 0;
    if (!(d.successful === true || (d.failed !== true && statut >= 200 && statut < 300))) return;
    var slot = d.target;
    if (!slot || !slot.classList) return;
    if (slot.classList.contains("status-ind") && slot.querySelector) {
      slot = slot.querySelector(".autosave-err");
    }
    if (!slot || !persistants.has(slot)) return;
    // Succès d'un AUTRE élément ciblant le même slot : le message reste vrai pour
    // l'élément en erreur. (elt absent des deux côtés : on efface, comme avant.)
    var source = persistants.get(slot);
    // Source remplacée par un swap outerHTML (détachée du DOM) : son message ne
    // peut plus être « vrai pour elle », le succès d'un elt neuf l'efface.
    if (source && d.elt && source !== d.elt && source.isConnected !== false) return;
    persistants.delete(slot);
    slot.textContent = "";
    slot.classList.remove("error");
  });

  document.body.addEventListener("htmx:responseError", function (evt) {
    var xhr = evt.detail && evt.detail.xhr;
    var statut = xhr ? xhr.status : 0;
    if (statut === 404) {
      showError(evt, "⚠ fiche introuvable — rien n'est enregistré. Copiez votre texte avant de quitter.",
        { persistant: true });
    } else if (statut === 401) {
      showError(evt, "⚠ session expirée — ",
        { persistant: true, lien: { texte: "reconnectez-vous", href: "/connexion" } });
    } else {
      showError(evt, "⚠ échec de l'enregistrement");
    }
  });
  document.body.addEventListener("htmx:sendError", function (evt) {
    showError(evt, "⚠ connexion perdue");
  });
})();
