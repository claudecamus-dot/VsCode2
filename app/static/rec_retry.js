// Plan de reprise d'un segment de transcription du micro direct — UNE seule
// source, partagée par record.html et record_libre.html (lot « transcription
// fiable », commit 4).
//
// Le problème : le mode guidé DUPLIQUAIT la reprise du mode libre (même
// constante `[2000, 6000]`, même arbre de décision sur le statut). Deux copies
// d'un budget de retry divergent au premier correctif — et ce correctif était
// justement nécessaire : le serveur répond 422 pour TOUT `TranscriptionError`,
// y compris un worker Whisper tué par la pression mémoire (audio intact,
// traitement raté), mais le client ne rejouait automatiquement QUE les statuts
// >= 500 et les erreurs réseau. Un tel 422 partait donc direct dans le bandeau
// « segments perdus » bloquant, alors qu'un simple second essai aurait abouti
// — le critère utilisateur est une transcription complète « du 1er coup », sans
// segment perdu. Le serveur qualifie désormais ces 422 `code: "transient"`
// (commit 3) ; ce module décide quoi en faire.
//
// Politique (inchangée sauf la ligne « transient ») :
//   • réseau / statut >= 500        -> 2 relances, après 2 s puis 6 s ;
//   • 422 `code: "transient"`       -> 3 relances, 2 s, 6 s puis 15 s (plafond
//                                      ≈ 23 s hors durée des requêtes) ;
//   • 503 `code: "busy"`            -> HORS budget : file d'attente normale du
//                                      verrou Whisper, bornée par `busyUntil`
//                                      (900 s) côté appelant ;
//   • délai maximal (`recTimeout`)  -> JAMAIS rejoué (rejouer les mêmes octets
//                                      aggrave une file déjà noyée) ;
//   • 422 `no_speech`               -> pas de retry, non bloquant ;
//   • 413, tout autre 422 (sans code ou code inconnu) -> définitif.
// BUDGET PARTAGÉ entre natures : `attempt` compte TOUTES les relances d'un même
// blob, et chaque échec lit le tableau de SA nature à cet index. Conséquences,
// voulues et testées par suites mixtes : un segment ne dépasse jamais 3 relances
// (2 pour une suite de 5xx purs) ; « 5xx, 5xx, puis transient » gagne la 3e relance
// (15 s) ; « transient, transient, puis 5xx » s'arrête (2 relances déjà payées).
// Rejouer un 422 ne duplique pas de texte : la route est sans état, rien n'est
// inséré avant ou pendant le refus.
//
// Fonctions PURES (aucun DOM, aucun minuteur) : testées sous node
// (tests/test_micro_direct_retry_execution.py). Les fermetures (`gen`,
// `pendingSegments`, `lostSegments`…) restent dans chaque écran.
//
// `messageCompletude` porte aussi le TEXTE de l'alerte de complétude d'un job
// de transcription de fichier : une seule rédaction pour les trois écrans qui
// l'affichent (record_libre, record, suivi de retranscription).
//
// Chargé dans <head> SANS `defer`, comme rec_fetch.js : les <script> inline
// des écrans le consomment à l'analyse du <body>.
(function () {
  'use strict';

  // Figés : `delaisReseau` est exposé tel quel (uploadBackup le lit) ; un `push`
  // d'un écran changerait silencieusement le budget des autres.
  var DELAIS_RESEAU_MS = Object.freeze([2000, 6000]);
  var DELAIS_TRANSIENT_MS = Object.freeze(DELAIS_RESEAU_MS.concat([15000]));

  // Nature d'une réponse HTTP déjà lue : 'busy' | 'retry' | 'transient' |
  // 'no_speech' | 'definitif'. (Une erreur réseau / un délai passent par
  // `classerErreur`, il n'y a alors pas de réponse.)
  function classer(status, data) {
    status = Number(status);   // « 422 » en chaîne ne doit pas tomber en définitif
    var code = data && data.code;
    if (status === 503 && code === 'busy') return 'busy';
    if (status >= 500) return 'retry';
    if (status === 422 && code === 'transient') return 'transient';
    if (status === 422 && code === 'no_speech') return 'no_speech';
    return 'definitif';
  }

  // Erreur levée par fetch : 'timeout' (jamais rejoué) ou 'retry' (réseau).
  function classerErreur(err) {
    return (err && err.recTimeout) ? 'timeout' : 'retry';
  }

  function delais(nature) {
    return nature === 'transient' ? DELAIS_TRANSIENT_MS : DELAIS_RESEAU_MS;
  }

  // Attente avant la relance n° `attempt + 1` (attempt = nombre de tentatives
  // déjà faites - 1), ou null si le budget de cette nature est épuisé / si la
  // nature n'est pas rejouable.
  function delaiPour(nature, attempt) {
    if (nature !== 'retry' && nature !== 'transient') return null;
    var d = delais(nature);
    return attempt < d.length ? d[attempt] : null;
  }

  // Nombre total de tentatives (initiale comprise) pour le « (2/3) » affiché.
  function nbEssais(nature) {
    return delais(nature).length + 1;
  }

  function duree(s) {
    s = Math.max(0, Math.round(Number(s) || 0));
    var m = Math.floor(s / 60);
    var r = s % 60;
    return m ? m + ' min ' + (r < 10 ? '0' : '') + r + ' s' : r + ' s';
  }

  // Nombres exploitables seulement : un `null`, un « x » ou un objet venus du
  // serveur afficheraient « NaN » ou « [object Object] ».
  function entiers(l) {
    return (Array.isArray(l) ? l : []).filter(function (x) {
      return (typeof x === 'number' || (typeof x === 'string' && x.trim() !== ''))
        && isFinite(Number(x));
    }).map(Number);
  }

  function liste(positions) {
    var vus = positions.slice(0, 10).join(', ');
    return positions.length > 10 ? vus + '…' : vus;
  }

  // Texte de l'alerte de complétude, ou '' s'il n'y a rien à dire. N'affiche
  // QUE `statut === 'incomplet'` : 'complet', 'non_evaluable' (durée du
  // conteneur illisible), null ou absent -> chaîne vide. Positions 1-based pour
  // l'utilisateur (`blocs_vides` est 0-based dans le job ; `tranches_ignorees`
  // est déjà en rangs 1-based). Aucun texte transcrit ni chemin dans le message.
  function messageCompletude(c) {
    if (!c || c.statut !== 'incomplet') return '';
    var morceaux = [];
    var vides = entiers(c.blocs_vides);
    if (vides.length) {
      morceaux.push('bloc' + (vides.length > 1 ? 's' : '') + ' sans texte : '
        + liste(vides.map(function (i) { return Number(i) + 1; })));
    }
    var ignorees = entiers(c.tranches_ignorees);
    if (ignorees.length) {
      morceaux.push('tranche' + (ignorees.length > 1 ? 's' : '') + ' audio ignorée'
        + (ignorees.length > 1 ? 's' : '') + ' : ' + liste(ignorees));
    }
    // Même seuil que `audio_transcribe.bilan_completude` (max(2 s, 2 %)) : un écart
    // DANS la tolérance (6 s sur 67 min pour l'interview 20) n'est pas du texte
    // manquant — l'afficher affirmerait à tort qu'il manque de l'audio alors que
    // l'incomplétude vient d'ailleurs (blocs sans texte).
    var conteneur = isFinite(Number(c.duree_conteneur_s)) ? Number(c.duree_conteneur_s) : 0;
    var tolerance = Math.max(2, 0.02 * conteneur);
    var ecart = Number(c.ecart_s);
    if (isFinite(ecart) && ecart > tolerance) {
      morceaux.push('il manque environ ' + duree(c.ecart_s) + ' d’audio dans le texte'
        + (conteneur ? ' (audio de ' + duree(conteneur) + ')' : ''));
    }
    return 'Transcription incomplète : '
      + (morceaux.length ? morceaux.join(' ; ') + '. ' : '')
      + 'Certains passages de l’audio peuvent manquer dans le texte.';
  }

  window.recRetryPlan = Object.freeze({
    delaisReseau: DELAIS_RESEAU_MS,
    delaisTransient: DELAIS_TRANSIENT_MS,
    classer: classer,
    classerErreur: classerErreur,
    delaiPour: delaiPour,
    nbEssais: nbEssais,
    messageCompletude: messageCompletude,
  });
})();
