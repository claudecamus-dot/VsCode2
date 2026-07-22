// Feedback immédiat sur les générations IA longues (2026-07-22).
// Une génération synchrone (synthèse, SWOT, difficultés, recommandations,
// régénération d'analyse) prend de ~30 s à plusieurs minutes avec Ollama local :
// sans retour visuel, le clic sur « Générer / Régénérer (IA) » paraissait ne rien
// déclencher (bug remonté). Tout <form data-busy-label="…"> voit, dès la soumission
// effective (après le confirm() éventuel), son bouton submit désactivé et
// relabellisé — indépendant d'htmx, fonctionne même si htmx n'a pas chargé.
document.addEventListener("submit", function (e) {
  var form = e.target;
  if (!(form instanceof HTMLFormElement)) return;
  var label = form.getAttribute("data-busy-label");
  if (!label || e.defaultPrevented) return; // confirm() refusé → rien à geler
  // Couvre aussi le <button> sans attribut type (submit implicite en HTML).
  var btn = form.querySelector('button[type="submit"], button:not([type]), input[type="submit"]');
  if (!btn) return;
  // Geler APRÈS l'envoi du POST (un bouton disabled avant envoi sortirait du
  // payload — aucun de ces boutons ne porte de name/value, ceinture quand même).
  setTimeout(function () {
    btn.disabled = true;
    btn.textContent = label;
  }, 0);
});
// Retour bfcache (bouton Back) : la page restaurée garderait le bouton gelé
// « Génération en cours… » à vie — recharger rend l'état réel (GET idempotent).
window.addEventListener("pageshow", function (e) {
  if (e.persisted && document.querySelector("form[data-busy-label] button:disabled")) {
    location.reload();
  }
});
