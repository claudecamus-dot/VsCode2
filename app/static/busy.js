// Feedback immédiat sur les générations IA longues (2026-07-22).
// Une génération synchrone (synthèse, SWOT, difficultés, recommandations,
// régénération d'analyse, extractions d'entretien) prend de ~30 s à plusieurs
// minutes avec Ollama local : sans retour visuel, le clic sur « Générer /
// Régénérer (IA) » paraissait ne rien déclencher (bug remonté). Tout
// <form data-busy-label="…"> voit, dès la soumission effective (après le
// confirm() éventuel), ses boutons submit désactivés et le bouton cliqué
// relabellisé — indépendant d'htmx, fonctionne même si htmx n'a pas chargé.
//
// Revue adversariale 2026-07-23 : le gel est PAR FORMULAIRE, pas par bouton —
// tous les submit du form (y compris un jumeau externe associé par form=) sont
// gelés, sinon le second bouton « Exporter en PPT » permettait une double
// génération concurrente pendant que le premier était gelé.
(function () {
  // Boutons submit d'un form à geler : ceux du form (hors `formaction` — les
  // boutons secondaires export-PDF/« Revenir » ne naviguent pas toujours, un
  // gel resterait à vie) + les boutons externes associés par l'attribut form=.
  function submitButtons(form) {
    var inForm = form.querySelectorAll(
      'button[type="submit"]:not([formaction]), button:not([type]):not([formaction]), input[type="submit"]:not([formaction])'
    );
    var btns = Array.prototype.slice.call(inForm);
    if (form.id) {
      var ext = document.querySelectorAll('button[form="' + form.id + '"]:not([formaction])');
      btns = btns.concat(Array.prototype.slice.call(ext));
    }
    return btns;
  }

  function unfreeze(btn) {
    if (!btn.dataset.busyFrozen) return;
    btn.disabled = false;
    if (btn.dataset.busyOriginal !== undefined) btn.textContent = btn.dataset.busyOriginal;
    delete btn.dataset.busyFrozen;
    delete btn.dataset.busyOriginal;
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    var label = form.getAttribute("data-busy-label");
    if (!label || e.defaultPrevented) return; // confirm() refusé → rien à geler
    // Bouton secondaire formaction (export PDF, « Revenir ») : le POST télécharge
    // un fichier ou repart en arrière SANS recharger cette page — ne rien geler.
    // NB : sans e.submitter (navigateur ancien, form.requestSubmit() sans bouton),
    // on ne peut pas savoir quel bouton a soumis — on gèle le form entier, les
    // boutons formaction restant exclus de submitButtons() dans tous les cas.
    if (e.submitter && e.submitter.hasAttribute("formaction")) return;
    var btns = submitButtons(form);
    if (!btns.length) return;
    var labelled = (e.submitter && !e.submitter.hasAttribute("formaction")) ? e.submitter : btns[0];
    // Geler APRÈS l'envoi (un bouton disabled avant envoi sortirait du payload).
    // data-busy-release="<ms>" : pour un form qui TÉLÉCHARGE un fichier (export
    // PPT), la page ne navigue jamais — sans libération, les boutons resteraient
    // gelés à vie. Borne basse « vraisemblable », pas une détection de fin : une
    // génération plus longue relibère le bouton pendant que la 1re requête est
    // encore en vol (assumé — préférable à un gel définitif).
    var release = parseInt(form.getAttribute("data-busy-release") || "0", 10) || 0;
    setTimeout(function () {
      btns.forEach(function (btn) {
        btn.dataset.busyFrozen = "1";
        btn.dataset.busyOriginal = btn.textContent;
        btn.disabled = true;
        if (btn === labelled) btn.textContent = label;
      });
      if (release > 0) {
        setTimeout(function () { btns.forEach(unfreeze); }, release);
      }
    }, 0);
  });

  // Retour bfcache (bouton Back) : la page restaurée garderait les boutons gelés
  // « Génération en cours… » à vie. On DÉGÈLE les boutons marqués au lieu de
  // recharger la page : un rechargement re-GET vierge perdrait le travail en vol
  // des écrans d'enregistrement (transcription dans le textarea/champs cachés —
  // revue adversariale 2026-07-23, régression évitée), et un bouton disabled BY
  // DESIGN (ex. « Extraire » avant toute transcription) ne doit pas déclencher
  // de rechargement : seuls les boutons portant le marqueur data-busy-frozen bougent.
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    document.querySelectorAll("button[data-busy-frozen]").forEach(unfreeze);
  });
})();
