// Brouillon LOCAL d'un entretien libre en cours — module partagé, chargé sans
// `defer` (comme rec_fetch.js : le <script> inline de record_libre.html le
// consomme à l'analyse du <body>).
//
// Le problème (incident du 2026-09-08, entretien réel de 2h) : la transcription
// accumulée par l'écran d'enregistrement ne vivait QUE dans la mémoire JS de
// l'onglet — ni localStorage, ni POST périodique du texte, ni colonne serveur.
// Le jeton de session n'était pas non plus restauré à la réouverture de la
// page, et aucune route ne permettait de reprendre une session orpheline.
// Résultat : 2h de propos n'ont jamais donné d'entretien en base ; ce qui en
// restait était un résidu technique (les tranches d'extraction), promis à la
// purge sept jours plus tard.
//
// Ce module tient, par mission, un brouillon dans localStorage : le texte
// transcrit, le jeton de session (pour retrouver les tranches déjà calculées
// côté serveur), les curseurs de découpage, les tranches audio déjà rangées, et
// un petit journal d'évènements (sauvegarde audio, tranches non transmises) qui
// survit à l'onglet — c'est ce qui manquait pour diagnostiquer l'absence totale
// de sauvegarde audio du 2026-09-08 (journal serveur tronqué par un
// redémarrage, état client mort avec la page).
//
// Ce qu'il N'EST PAS : une sauvegarde serveur. localStorage est propre au
// navigateur et au profil ; il peut être vidé (navigation privée, nettoyage).
// C'est un filet de plus, pas le dernier — d'où le `try/catch` sur chaque accès
// et l'absence totale d'effet quand il est indisponible : l'écran doit se
// comporter exactement comme avant.
//
// Exposé en global `recDraft` pour le navigateur, et en `module.exports` pour
// que tests/test_session_libre_reprise.py l'exécute réellement sous node.
var recDraft = (function () {
  'use strict';

  var PREFIXE = 'i2d:brouillon-libre:';
  // Aligné sur la purge serveur des tranches (7 jours) : au-delà, le jeton de
  // session ne retrouve plus rien côté serveur, et un texte plus vieux n'a
  // presque jamais de raison d'être proposé en reprise.
  var AGE_MAX_MS = 7 * 24 * 60 * 60 * 1000;
  var JOURNAL_MAX = 60;

  function cle(missionId) {
    return PREFIXE + String(missionId);
  }

  function stockage() {
    // L'accès lui-même peut lever (SecurityError en navigation privée sur
    // certains navigateurs, stockage bloqué par une politique) : jamais
    // au-dessus d'un `try`.
    try {
      var s = (typeof window !== 'undefined') ? window.localStorage : null;
      return s || null;
    } catch (e) {
      return null;
    }
  }

  function maintenant(now) {
    return (typeof now === 'number') ? now : Date.now();
  }

  // Le brouillon de cette mission, ou null (absent, illisible, périmé). Un
  // brouillon périmé est retiré au passage : il ne sera plus jamais proposé.
  function charger(missionId, now) {
    var s = stockage();
    if (!s) return null;
    var brut;
    try {
      brut = s.getItem(cle(missionId));
    } catch (e) {
      return null;
    }
    if (!brut) return null;
    var etat;
    try {
      etat = JSON.parse(brut);
    } catch (e) {
      effacer(missionId);  // JSON corrompu : inutile de le garder
      return null;
    }
    if (!etat || typeof etat !== 'object') return null;
    if (typeof etat.savedAt === 'number' && maintenant(now) - etat.savedAt > AGE_MAX_MS) {
      effacer(missionId);
      return null;
    }
    return etat;
  }

  // Écrit l'état complet (remplace). Rend false si rien n'a pu être écrit —
  // stockage indisponible ou plein (QuotaExceededError) — sans jamais lever :
  // un échec de brouillon ne doit pas casser l'enregistrement en cours.
  function sauver(missionId, etat, now) {
    var s = stockage();
    if (!s) return false;
    var copie = {};
    for (var k in etat) {
      if (Object.prototype.hasOwnProperty.call(etat, k)) copie[k] = etat[k];
    }
    copie.savedAt = maintenant(now);
    try {
      s.setItem(cle(missionId), JSON.stringify(copie));
      return true;
    } catch (e) {
      return false;
    }
  }

  function effacer(missionId) {
    var s = stockage();
    if (!s) return;
    try {
      s.removeItem(cle(missionId));
    } catch (e) {
      // rien à faire : au pire le brouillon reste, il sera proposé puis ignoré
    }
  }

  // Ajoute un évènement horodaté au journal du brouillon SANS toucher au reste
  // (texte, jeton…). Le journal est borné en gardant le DÉBUT et la FIN :
  // ce sont les premiers évènements qui datent une panne (la rotation qui n'a
  // jamais eu lieu, le premier envoi refusé) — évincer les plus anciens en
  // premier effaçait précisément ceux-là (revue du 2026-09-08, F16).
  var JOURNAL_DEBUT = 20;
  function noter(missionId, evenement, now) {
    var etat = charger(missionId, now) || {};
    var journal = Array.isArray(etat.journal) ? etat.journal : [];
    var entree = { t: maintenant(now) };
    for (var k in evenement) {
      if (Object.prototype.hasOwnProperty.call(evenement, k)) entree[k] = evenement[k];
    }
    journal.push(entree);
    if (journal.length > JOURNAL_MAX) {
      journal = journal.slice(0, JOURNAL_DEBUT)
        .concat(journal.slice(journal.length - (JOURNAL_MAX - JOURNAL_DEBUT)));
    }
    etat.journal = journal;
    return sauver(missionId, etat, now);
  }

  // Marque le brouillon comme envoyé au serveur (clic « Enregistrer »). On ne
  // l'efface PAS : si la requête échoue après la redirection (serveur tombé,
  // 500), le texte serait perdu une seconde fois. Il reste disponible sept
  // jours, proposé de façon discrète (cf. `decision`).
  function marquerEnvoye(missionId, now) {
    var etat = charger(missionId, now);
    if (!etat) return false;
    etat.envoyeLe = maintenant(now);
    return sauver(missionId, etat, now);
  }

  function formaterDate(ts) {
    try {
      var d = new Date(ts);
      var jj = ('0' + d.getDate()).slice(-2);
      var mm = ('0' + (d.getMonth() + 1)).slice(-2);
      var hh = ('0' + d.getHours()).slice(-2);
      var mi = ('0' + d.getMinutes()).slice(-2);
      return jj + '/' + mm + '/' + d.getFullYear() + ' à ' + hh + ':' + mi;
    } catch (e) {
      return '?';
    }
  }

  // Faut-il proposer une reprise, et comment ? Rend null (rien à proposer) ou
  // { mode: 'reprise' | 'discret', libelle }. Pure : aucune lecture du DOM ni
  // du stockage, c'est ce qui la rend testable sous node.
  //   - 'reprise' : un texte jamais envoyé attend — bandeau visible.
  //   - 'discret' : le texte a été envoyé (« Enregistrer » cliqué) ; il est
  //     normalement en base. On le garde atteignable sans insister : une ligne
  //     discrète, pour le cas où l'envoi aurait échoué après coup.
  function decision(brouillon, now) {
    if (!brouillon || typeof brouillon !== 'object') return null;
    var texte = (typeof brouillon.transcript === 'string') ? brouillon.transcript.trim() : '';
    if (!texte) return null;
    if (typeof brouillon.savedAt === 'number' && maintenant(now) - brouillon.savedAt > AGE_MAX_MS) return null;
    var quand = formaterDate(brouillon.savedAt);
    var taille = texte.length + ' caractères';
    if (typeof brouillon.envoyeLe === 'number') {
      return {
        mode: 'discret',
        libelle: 'Une transcription envoyée le ' + formaterDate(brouillon.envoyeLe)
          + ' (' + taille + ') est encore en mémoire locale — vérifie qu\'elle figure '
          + 'bien dans la mission, sinon tu peux la restaurer ici.'
      };
    }
    return {
      mode: 'reprise',
      libelle: 'Une session non enregistrée a été retrouvée (dernière sauvegarde locale le '
        + quand + ', ' + taille + ').'
    };
  }

  return {
    charger: charger,
    sauver: sauver,
    effacer: effacer,
    noter: noter,
    marquerEnvoye: marquerEnvoye,
    decision: decision,
    AGE_MAX_MS: AGE_MAX_MS,
    JOURNAL_MAX: JOURNAL_MAX
  };
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = recDraft;
}
