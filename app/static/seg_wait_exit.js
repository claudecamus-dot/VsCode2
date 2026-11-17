// Panneau de sortie des pages d'attente des tranches (Story 3.3, A-8) — UNE
// source pour record_segment_wait.html et libre_segment_wait.html.
//
// L'écran d'attente détient la seule copie de la transcription brute : quand
// l'attente s'éternise (borne de temps, suivi muet, session expirée, aucune
// tranche côté serveur), il propose de la copier/télécharger et, sauf session
// expirée, de finaliser avec le texte disponible.
//
// Règles (revue de code du 2026-11-16) :
//   • le nœud role=alert n'est réécrit que si la RAISON change — un poll toutes
//     les 5 s ne doit ni ré-annoncer le message ni effacer « Texte copié » (le
//     retour des boutons vit dans une région polie séparée) ;
//   • `masquer()` quand le suivi redevient sain ;
//   • session expirée : « Finaliser » est masqué ET désactivé — le POST
//     renverrait vers la page de connexion et quitterait la seule page qui
//     tient le texte.
// Chargé de façon synchrone avant le script inline de la page.
(function () {
  'use strict';
  function rien() {}

  window.segWaitExit = function (o) {
    var root = document.getElementById('seg-exit');
    if (!root) return { proposer: rien, masquer: rien, authentifie: rien };
    var textEl = document.getElementById('seg-exit-text');
    var statusEl = document.getElementById('seg-exit-status');
    var finalizeBtn = document.getElementById('seg-exit-finalize');
    var cle = null;
    // Session expirée COLLANTE : un 401 vu, « Finaliser » reste masqué/désactivé quelle
    // que soit la raison suivante (coupure réseau, borne de temps…) jusqu'à ce qu'un
    // poll authentifié réussisse (`authentifie()`). Sinon un 401 puis une coupure
    // réoffraient un POST qui renverrait vers la connexion (perte de la page).
    var sessionExpiree = false;
    function majFinaliser() {
      finalizeBtn.hidden = sessionExpiree;
      finalizeBtn.disabled = sessionExpiree;
    }

    function texte() {
      var champ = o.form.querySelector('input[name="transcript"]');
      return champ ? champ.value : '';
    }
    function dire(msg) { if (statusEl) statusEl.textContent = msg; }

    function proposer(raison, session) {
      if (o.inactif && o.inactif()) return;
      if (session) sessionExpiree = true;
      var nouvelle = raison + (sessionExpiree ? '|session' : '');
      if (nouvelle === cle) return;
      cle = nouvelle;
      textEl.textContent = raison + (sessionExpiree && !session
        ? ' « Finaliser » reste désactivé : la session a expiré (reconnecte-toi dans un autre onglet).' : '');
      majFinaliser();
      root.hidden = false;
    }
    function masquer() {
      if (cle === null) return;
      cle = null;
      root.hidden = true;
      majFinaliser();
    }
    // Poll authentifié réussi (2xx) : la session est rétablie, règles normales.
    function authentifie() {
      if (!sessionExpiree) return;
      sessionExpiree = false;
      majFinaliser();
      cle = cle === null ? null : '';  // la raison suivante réécrit le message sans la mention
    }

    document.getElementById('seg-exit-copy').addEventListener('click', function () {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(texte()).then(function () {
          dire('Transcription brute copiée dans le presse-papiers.');
        }, function () { dire('Copie refusée : utilise « Télécharger la transcription brute ».'); });
      } else {
        dire('Copie indisponible : utilise « Télécharger la transcription brute ».');
      }
    });
    document.getElementById('seg-exit-download').addEventListener('click', function () {
      var url = URL.createObjectURL(new Blob([texte()], { type: 'text/plain;charset=utf-8' }));
      var a = document.createElement('a');
      a.href = url; a.download = 'entretien-' + o.token + '.txt';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      dire('Transcription brute téléchargée.');
    });
    finalizeBtn.addEventListener('click', function () {
      if (finalizeBtn.disabled || sessionExpiree) return;
      if (!confirm('Finaliser avec le texte disponible ? Les tranches encore en cours ne seront pas attendues.')) return;
      o.onFinalize();
    });
    return { proposer: proposer, masquer: masquer, authentifie: authentifie };
  };
})();
