// Requête réseau avec DÉLAI MAXIMAL — module partagé par record.html et
// record_libre.html (revue du 2026-08-31).
//
// Le problème : `fetch()` n'a pas de timeout. Sur une connexion à demi ouverte
// — VPN qui tombe, Wi-Fi coupé sans RST, machine mise en veille, serveur figé
// sur le verrou du modèle Whisper — la promesse ne se règle NI en `.then` NI en
// `.catch`. Les compteurs de requêtes en vol (`pendingSegments`,
// `pendingBackups`, `pendingSegmentJobSubmits`) ne redescendent donc jamais et
// `updateSubmitState()` garde le bouton « Enregistrer l'entretien » grisé pour
// la durée de vie de la page, sur une transcription complète, sans autre issue
// que « Recommencer » — qui la détruit. Aucun de ces compteurs n'est remis à
// zéro au démarrage ni au reset : le gel survivait aux sessions suivantes du
// même onglet. Mesuré : 0 occurrence d'`AbortController` dans les deux écrans.
//
// Ce que ce délai N'EST PAS : un moyen de policer la lenteur. La transcription
// est CPU-bound et sérialisée derrière un verrou côté serveur — un segment peut
// légitimement attendre plusieurs minutes derrière la file. Couper trop tôt
// serait pire que le mal : la reprise renverrait le même blob et doublerait la
// charge sur un serveur déjà saturé. Les délais sont donc GÉNÉREUX ; leur seul
// rôle est de transformer un silence INFINI en échec ordinaire, que la
// mécanique de reprise déjà en place (`retryOrGiveUp`, bandeau des segments
// perdus) sait traiter.
//
// Le délai couvre les en-têtes ET la lecture du corps (`res.json()`), parce que
// c'est là que le gel se produisait vraiment. Conséquence assumée : un appelant
// qui ne lit JAMAIS le corps laisse l'abort programmé se déclencher sur une
// requête déjà terminée — sans effet (le minuteur est à un coup, la réponse est
// déjà livrée). Ce cas existe : `pollRepartition` ne lit pas le corps quand la
// réponse n'est pas 2xx.
//
// Chargé dans <head> SANS `defer`, comme rec_audio_source.js : les <script>
// inline des deux écrans le consomment à l'analyse du <body>, un chargement
// différé le rendrait indéfini au moment de l'appel — sans erreur visible.
(function () {
  'use strict';

  // Défaut volontairement court : il ne couvre que les appels qui rendent la
  // main tout de suite côté serveur (création de job, poll de statut). Les
  // appels lourds passent leur propre valeur.
  var DEFAUT_MS = 120000;

  window.recFetch = function (url, options, timeoutMs) {
    var ms = timeoutMs || DEFAUT_MS;
    // Navigateur sans AbortController : on rend le fetch nu plutôt que d'échouer.
    // Le poste de travail visé est Windows 11 + Edge, où il existe depuis
    // longtemps — ce repli couvre un poste exotique, pas le cas nominal.
    if (typeof AbortController === 'undefined') return fetch(url, options);

    var ctrl = new AbortController();
    var opts = {};
    var cle;
    for (cle in (options || {})) {
      if (Object.prototype.hasOwnProperty.call(options, cle)) opts[cle] = options[cle];
    }
    opts.signal = ctrl.signal;

    var timer = setTimeout(function () { ctrl.abort(); }, ms);

    // `AbortError` porte un message par défaut du navigateur, illisible dans le
    // bandeau d'erreur. On le remplace par la cause telle que l'utilisateur la
    // vit — le reste de la chaîne n'affiche que `.message`.
    function traduire(err) {
      if (err && err.name === 'AbortError') {
        // `Math.max(1, …)` : un délai sous la seconde (les tests) afficherait
        // sinon « après 0 s », qui se lit comme un bug plutôt qu'un délai.
        return new Error('pas de réponse après ' + Math.max(1, Math.round(ms / 1000)) + ' s');
      }
      return err;
    }

    return fetch(url, opts).then(
      function (res) {
        // NE PAS annuler le minuteur ici. `fetch` se règle à la réception des
        // EN-TÊTES, pas du corps : un socket qui meurt après les en-têtes (VPN
        // qui tombe pendant le transfert, machine mise en veille) laisserait
        // `res.json()` en suspens pour toujours — le gel exact que ce helper
        // existe pour supprimer, simplement déplacé d'un cran (revue
        // adversariale du 2026-08-31, reproduit : pendingBackups=1,
        // settleCount=0). L'abort reste donc armé jusqu'à la LECTURE DU CORPS.
        // Réponse sans `.json` (jamais en navigateur, mais un test ou un futur
        // appelant peut en fournir une) : rien à attendre, on libère tout de suite
        // plutôt que de lever en enveloppant.
        if (!res || typeof res.json !== 'function') { clearTimeout(timer); return res; }
        var lire = res.json.bind(res);
        res.json = function () {
          return lire().then(
            function (data) { clearTimeout(timer); return data; },
            function (err) { clearTimeout(timer); throw traduire(err); }
          );
        };
        return res;
      },
      function (err) {
        clearTimeout(timer);
        throw traduire(err);
      }
    );
  };
})();
