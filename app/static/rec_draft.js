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

  // Story 3.7 (A-12) : UN brouillon par ONGLET et par mission. Avant, deux onglets de la
  // même mission écrivaient la même clé et s'écrasaient. L'identifiant d'onglet vit dans
  // sessionStorage (survit au F5, pas à un autre onglet) ; sans lui (stockage bloqué) un
  // identifiant mémoire sert pour la vie de la page.
  var ONGLET_CLE = 'i2d:brouillon-onglet';
  var VIVANT_PREFIXE = 'i2d:onglet-vivant:';
  // Un onglet est « vivant » tant que son battement (`battre`) a moins de 30 s : le
  // brouillon d'un onglet vivant n'est JAMAIS proposé à un autre (même jeton de session,
  // tranches mélangées). Un battement toutes les 10 s laisse deux battements de marge.
  var VIVANT_MS = 30000;
  var BROUILLONS_ETRANGERS_MAX = 5;
  var ongletMemoire = null;
  function typeNavigation() {
    try {
      var nav = window.performance.getEntriesByType('navigation')[0];
      return nav ? nav.type : '';
    } catch (e) { return ''; }
  }
  function idOnglet() {
    if (ongletMemoire) return ongletMemoire;
    var id = null;
    try {
      var ss = (typeof window !== 'undefined') ? window.sessionStorage : null;
      if (ss) {
        id = ss.getItem(ONGLET_CLE);
        // Onglet DUPLIQUÉ : sessionStorage est copié, l'identifiant aussi. Si le
        // battement de cet identifiant est frais alors que la page n'est ni un
        // rechargement ni un retour arrière, un autre onglet le porte : on en prend un neuf.
        if (id && vivant(id, null) && ['reload', 'back_forward'].indexOf(typeNavigation()) < 0) id = null;
        if (!id) { id = nouvelId(); ss.setItem(ONGLET_CLE, id); }
      }
    } catch (e) { id = null; }
    ongletMemoire = id || nouvelId();
    return ongletMemoire;
  }
  function nouvelId() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  // Battement de l'onglet (à appeler toutes les ~10 s tant que l'écran est ouvert).
  function battre(now) {
    var s = stockage();
    if (!s) return false;
    try { s.setItem(VIVANT_PREFIXE + idOnglet(), String(maintenant(now))); return true; } catch (e) { return false; }
  }
  function vivant(id, now) {
    var s = stockage();
    if (!s) return false;
    try {
      var t = Number(s.getItem(VIVANT_PREFIXE + id));
      return isFinite(t) && t > 0 && maintenant(now) - t < VIVANT_MS;
    } catch (e) { return false; }
  }
  function idDeCle(missionId, k) {
    var base = cleHeritee(missionId) + ':';
    return k.indexOf(base) === 0 ? k.slice(base.length) : null;
  }

  function cle(missionId) {
    return PREFIXE + String(missionId) + ':' + idOnglet();
  }
  // Clé d'avant la 3.7 (sans onglet) : lue, jamais réécrite.
  function cleHeritee(missionId) {
    return PREFIXE + String(missionId);
  }
  // Toutes les clés de brouillon de cette mission (onglet courant, autres onglets, héritée).
  function clesMission(missionId) {
    var s = stockage();
    var out = [];
    if (!s) return out;
    var base = cleHeritee(missionId);
    // Stockage sans énumération (doublure, navigateur exotique) : au moins nos deux clés.
    if (typeof s.length !== 'number' || typeof s.key !== 'function') return [cle(missionId), base];
    try {
      for (var i = 0; i < s.length; i++) {
        var k = s.key(i);
        if (k === base || (k && k.indexOf(base + ':') === 0)) out.push(k);
      }
    } catch (e) { /* énumération impossible : on retombe sur la clé de l'onglet */ }
    return out;
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
  function lireCle(k, now) {
    var s = stockage();
    if (!s) return null;
    var brut;
    try {
      brut = s.getItem(k);
    } catch (e) {
      return null;
    }
    if (!brut) return null;
    var etat;
    try {
      etat = JSON.parse(brut);
    } catch (e) {
      effacer(null, k);  // JSON corrompu : inutile de le garder
      return null;
    }
    if (!etat || typeof etat !== 'object') return null;
    if (typeof etat.savedAt === 'number' && maintenant(now) - etat.savedAt > AGE_MAX_MS) {
      effacer(null, k);
      return null;
    }
    // Clé d'origine : non énumérable, donc jamais recopiée par `sauver`.
    Object.defineProperty(etat, '_cle', { value: k, enumerable: false });
    return etat;
  }

  // Le brouillon de l'ONGLET courant seulement (écritures : journal, envoi).
  function chargerPropre(missionId, now) {
    return lireCle(cle(missionId), now);
  }

  // Le brouillon à PROPOSER en reprise : celui de l'onglet, sinon le plus récent
  // d'un autre onglet (session orpheline après fermeture) ou de l'ancienne clé.
  function charger(missionId, now) {
    var propre = chargerPropre(missionId, now);
    if (propre) return propre;
    var meilleur = null;
    clesMission(missionId).forEach(function (k) {
      var autre = idDeCle(missionId, k);
      if (autre && vivant(autre, now)) return;  // onglet encore ouvert : pas à nous d'y toucher
      var e = lireCle(k, now);
      if (e && (!meilleur || (e.savedAt || 0) > (meilleur.savedAt || 0))) meilleur = e;
    });
    return meilleur;
  }

  // Après une reprise : le brouillon d'un AUTRE onglet est désormais celui de cet onglet
  // (réécrit par `sauver`) ; on retire l'ancienne copie pour ne pas la reproposer.
  function adopter(missionId, brouillon) {
    if (brouillon && brouillon._cle && brouillon._cle !== cle(missionId)) effacer(null, brouillon._cle);
  }

  // Ce brouillon est-il celui de CET onglet ? (sinon : copie d'un onglet fermé)
  function estPropre(missionId, brouillon) {
    return !!brouillon && brouillon._cle === cle(missionId);
  }

  // Borne le nombre de brouillons d'AUTRES onglets (fermés) : on garde les plus récents.
  function elaguer(missionId, now) {
    var propre = cle(missionId);
    var autres = [];
    clesMission(missionId).forEach(function (k) {
      if (k === propre) return;
      var e = lireCle(k, now);   // retire au passage un périmé / corrompu
      if (e) autres.push({ k: k, t: e.savedAt || 0 });
    });
    autres.sort(function (a, b) { return b.t - a.t; });
    autres.slice(BROUILLONS_ETRANGERS_MAX).forEach(function (x) { effacer(null, x.k); });
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
      battre(now);
      elaguer(missionId, now);
      return true;
    } catch (e) {
      return false;
    }
  }

  // `cleExacte` : clé d'un brouillon proposé (autre onglet) ; sinon celle de l'onglet courant.
  function effacer(missionId, cleExacte) {
    var s = stockage();
    if (!s) return;
    try {
      s.removeItem(cleExacte || cle(missionId));
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
    var etat = chargerPropre(missionId, now) || {};
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
  // Story 3.7 (A-13) : (missionId, jeton, now), même ordre que `effacerSiEnvoye`.
  function marquerEnvoye(missionId, jeton, now) {
    var etat = chargerPropre(missionId, now);
    if (!etat) return false;
    etat.envoyeLe = maintenant(now);
    if (jeton) etat.envoiJeton = String(jeton);  // identifie l'entretien que ce POST doit créer
    return sauver(missionId, etat, now);
  }

  // Purge le brouillon SEULEMENT s'il est marqué envoyé (la fiche de l'entretien
  // enregistré s'affiche : l'envoi a abouti). Un brouillon non envoyé — autre
  // enregistrement en cours dans un autre onglet — n'est jamais touché.
  function effacerSiEnvoye(missionId, jeton, now) {
    // Tous les brouillons de la mission (l'entretien s'affiche parfois dans un autre
    // onglet que celui qui a envoyé) : seul celui dont le jeton correspond est purgé.
    if (!jeton) return false;
    var purge = false;
    clesMission(missionId).forEach(function (k) {
      var etat = lireCle(k, now);
      if (!etat || typeof etat.envoyeLe !== 'number') return;
      // Le jeton de l'entretien affiché doit être celui du POST : sinon (échec du
      // POST puis ouverture d'un ANCIEN entretien) le texte seul copie survivante.
      if (!etat.envoiJeton || etat.envoiJeton !== String(jeton)) return;
      effacer(null, k);
      purge = true;
    });
    return purge;
  }

  function formaterDate(ts) {
    try {
      // Story 3.3 (A-11) : date absente ou illisible -> libellé explicite, jamais « NaN/NaN ».
      if (typeof ts !== 'number' || !isFinite(ts)) return null;
      var d = new Date(ts);
      if (isNaN(d.getTime())) return null;  // hors plage (> 8,64e15 ms) : date invalide
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
    var date = formaterDate(brouillon.savedAt);
    var quand = date ? 'le ' + date : 'à une date inconnue';
    var taille = texte.length + ' caractères';
    if (typeof brouillon.envoyeLe === 'number') {
      return {
        mode: 'discret',
        libelle: 'Une transcription envoyée '
          + (formaterDate(brouillon.envoyeLe) ? 'le ' + formaterDate(brouillon.envoyeLe) : 'à une date inconnue')
          + ' (' + taille + ') est encore en mémoire locale — vérifie qu\'elle figure '
          + 'bien dans la mission, sinon tu peux la restaurer ici.'
      };
    }
    return {
      mode: 'reprise',
      libelle: 'Une session non enregistrée a été retrouvée (dernière sauvegarde locale '
        + quand + ', ' + taille + ').'
    };
  }

  return {
    charger: charger,
    chargerPropre: chargerPropre,
    adopter: adopter,
    battre: battre,
    estPropre: estPropre,
    VIVANT_MS: VIVANT_MS,
    sauver: sauver,
    effacer: effacer,
    noter: noter,
    marquerEnvoye: marquerEnvoye,
    effacerSiEnvoye: effacerSiEnvoye,
    decision: decision,
    AGE_MAX_MS: AGE_MAX_MS,
    JOURNAL_MAX: JOURNAL_MAX
  };
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = recDraft;
}
