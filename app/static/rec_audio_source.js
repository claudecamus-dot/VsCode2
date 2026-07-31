/* Source audio de l'enregistrement : micro seul (présentiel) ou micro + son de
   l'onglet (entretien à distance Meet/Teams).

   POURQUOI CE FICHIER EXISTE
   Jusqu'ici les deux écrans d'enregistrement appelaient directement
   `getUserMedia({audio: true})` : le navigateur ne capte alors QUE le micro
   physique. La voix des participants distants sort dans le casque et n'entre
   jamais dans le périphérique capturé — cas réel mission 16 (2026-07-30) :
   ~100 min d'entretien Google Meet enregistrées avec une seule voix sur deux,
   matière non récupérable. Aucun correctif en aval ne peut reconstituer un son
   qui n'a jamais existé côté machine : le seul point de réparation est
   l'ACQUISITION.

   Partagé entre `record.html` (mode structuré) et `record_libre.html` (mode
   libre) plutôt que dupliqué : l'historique du projet montre que les correctifs
   n'étaient appliqués qu'à un seul des deux écrans, et l'asymétrie repoussait.

   PRINCIPE
   Les deux sources entrent dans un nœud de mélange WebAudio ; les MediaRecorder
   (transcription 60 s et sauvegarde 30 min) se branchent sur la SORTIE du
   mélange. On peut donc greffer ou remplacer la source « onglet » sans jamais
   toucher aux enregistreurs.

   En micro seul on rend le flux BRUT, sans AudioContext : c'est le cas
   majoritaire (présentiel) et il n'a aucune raison de payer le risque d'un
   contexte audio suspendu par le navigateur. Le mélange n'existe que quand on
   capte réellement un onglet.

   CONTRAINTES NAVIGATEUR (vérifiées, pas supposées)
   • `getDisplayMedia` exige une piste VIDÉO pour accorder l'audio d'onglet :
     on la demande, on ne s'en sert pas, et on ne l'arrête pas (l'arrêter met fin
     au partage entier, audio compris).
   • L'utilisateur doit cocher « Partager aussi l'audio de l'onglet » DANS la
     boîte de dialogue système, à chaque fois. S'il oublie, le flux rendu est
     parfaitement valide mais SANS piste audio — c'est le mode d'échec réel, et
     c'est celui qu'on détecte de façon déterministe (présence de la piste),
     jamais en attendant qu'un vumètre bouge : au moment du réglage, personne
     n'a encore parlé dans la réunion.
   • Chrome/Edge uniquement (Firefox et Safari ne rendent pas l'audio d'onglet).
     `supported()` le dit, l'appelant dégrade.
*/
(function () {
  'use strict';

  var ctx = null;             // AudioContext, créé seulement si on mélange
  var dest = null;            // sortie du mélange : c'est CE flux qu'on enregistre
  var micStream = null;       // flux micro brut (toujours détenu ici)
  var tabStream = null;       // flux getDisplayMedia (audio + vidéo inutilisée)
  var micNode = null, tabNode = null;
  var micAnalyser = null, tabAnalyser = null;
  var meterTimer = null;
  var onTabLost = null, onLevels = null;
  var tabEnded = false;
  var partageEnCours = false;  // mutex : voir shareTab()

  function supported() {
    return !!(navigator.mediaDevices &&
              navigator.mediaDevices.getDisplayMedia &&
              (window.AudioContext || window.webkitAudioContext));
  }

  function ensureCtx() {
    if (!ctx) {
      var C = window.AudioContext || window.webkitAudioContext;
      ctx = new C();
      // Un contexte suspendu produirait un enregistrement SILENCIEUX — soit
      // exactement le bug qu'on corrige, une couche plus bas. On le relance,
      // et l'appelant est prévenu pour pouvoir alerter à l'écran.
      ctx.addEventListener('statechange', function () {
        if (ctx && ctx.state === 'suspended') {
          ctx.resume().catch(function () {});
        }
      });
    }
    if (ctx.state === 'suspended') ctx.resume().catch(function () {});
    return ctx;
  }

  function analyserFor(node) {
    var a = ensureCtx().createAnalyser();
    a.fftSize = 1024;
    node.connect(a);          // dérivation de mesure, en plus du chemin vers dest
    return a;
  }

  /* Niveau instantané 0..1 (RMS sur le domaine temporel). Sert uniquement à
     l'affichage : la DÉCISION de blocage repose sur la présence de la piste,
     pas sur ce niveau (cf. en-tête). */
  function level(analyser) {
    if (!analyser) return 0;
    var buf = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(buf);
    var somme = 0;
    for (var i = 0; i < buf.length; i++) {
      var v = (buf[i] - 128) / 128;
      somme += v * v;
    }
    return Math.min(1, Math.sqrt(somme / buf.length) * 4);
  }

  function startMeters() {
    if (meterTimer || !onLevels) return;
    meterTimer = setInterval(function () {
      onLevels(level(micAnalyser), tabActive() ? level(tabAnalyser) : 0);
    }, 120);
  }

  function stopMeters() {
    if (meterTimer) clearInterval(meterTimer);
    meterTimer = null;
  }

  function tabAudioTrack() {
    if (!tabStream) return null;
    var pistes = tabStream.getAudioTracks();
    return pistes.length ? pistes[0] : null;
  }

  /* Vrai seulement si l'onglet est RÉELLEMENT capté : une piste présente, non
     terminée, non coupée. Les trois cas se produisent (case non cochée, arrêt du
     partage par l'utilisateur, onglet fermé) et donnent tous le même symptôme
     final — un entretien à moitié muet. */
  function tabActive() {
    var piste = tabAudioTrack();
    return !!(piste && piste.readyState === 'live' && !piste.muted && !tabEnded);
  }

  function detachTab() {
    if (tabNode) { try { tabNode.disconnect(); } catch (e) {} }
    tabNode = null;
    tabAnalyser = null;
    if (tabStream) {
      tabStream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
    }
    tabStream = null;
  }

  /* Construit (ou reconstruit) le mélange à partir du micro déjà détenu et,
     s'il y en a un, du flux d'onglet courant. Rend le flux À ENREGISTRER. */
  function buildMix() {
    ensureCtx();
    if (!dest) dest = ctx.createMediaStreamDestination();
    if (!micNode && micStream) {
      micNode = ctx.createMediaStreamSource(micStream);
      micNode.connect(dest);
      micAnalyser = analyserFor(micNode);
    }
    if (tabStream && !tabNode) {
      var piste = tabAudioTrack();
      if (piste) {
        // Un MediaStream ne contenant QUE la piste audio : brancher le flux
        // complet (avec sa vidéo) sur createMediaStreamSource n'apporte rien
        // et n'est pas uniformément supporté.
        tabNode = ctx.createMediaStreamSource(new MediaStream([piste]));
        tabNode.connect(dest);
        tabAnalyser = analyserFor(tabNode);
      }
    }
    startMeters();
    return dest.stream;
  }

  /* Micro seul. Détenu ici même en présentiel : la réparation en cours
     d'enregistrement (« le son de l'onglet n'est pas capté ») a besoin du micro
     d'origine pour reconstruire un mélange sans redemander la permission. */
  function acquireMic() {
    if (micStream) return Promise.resolve(micStream);
    return navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
      micStream = s;
      return s;
    });
  }

  /* Acquiert le micro et démarre SON vumètre tout de suite, sans attendre le
     partage d'onglet — appelée quand l'utilisateur coche « Entretien à
     distance », avant tout clic sur « Partager ». `acquire(false)` ne convient
     PAS ici : il rend le flux micro brut SANS jamais appeler `buildMix()` (donc
     sans jamais démarrer les vumètres), qui n'est atteint que par
     `acquire(true)` — lui-même seulement utilisable une fois la piste d'onglet
     déjà active. Résultat sans cette fonction : le vumètre micro restait figé
     à 0 % tant que l'onglet n'était pas partagé, à l'inverse exact du
     commentaire qui l'appelait (« vérifier les deux sources maintenant » —
     revue adversariale 2026-07-31, trouvé indépendamment par les 2 chasseurs).
     `buildMix()` est idempotente côté onglet (elle ne branche `tabNode` que si
     `tabStream` existe déjà) : l'appeler ici sans onglet encore partagé est
     sans risque, et la reconnexion ultérieure d'un onglet réel ne recrée rien
     côté micro. */
  function preflightMic() {
    return acquireMic().then(buildMix);
  }

  /* Flux à passer aux MediaRecorder au DÉMARRAGE.
     `remote` faux → micro brut, comportement historique inchangé.
     `remote` vrai → le partage d'onglet doit déjà avoir été accordé (l'écran le
     réclame avant d'activer « Démarrer ») ; à défaut on retombe sur le micro
     seul plutôt que d'empêcher tout enregistrement. */
  function acquire(remote) {
    return acquireMic().then(function (mic) {
      if (!remote || !tabActive()) return mic;
      return buildMix();
    });
  }

  /* Partage (ou REpartage) de l'onglet. Utilisable avant ET pendant
     l'enregistrement : c'est le geste de réparation.
     Rend le flux à enregistrer — au démarrage l'appelant l'utilise tel quel,
     en cours d'enregistrement il le substitue et fait tourner les enregistreurs
     (leur handler `stop` les relance sur la nouvelle source, exactement comme
     la rotation 30 min déjà éprouvée). */
  function shareTab() {
    if (!supported()) {
      return Promise.reject(new Error(
        "Ce navigateur ne sait pas capter le son d'un onglet. Utilise Chrome ou Edge, " +
        "ou importe après coup l'enregistrement produit par Meet/Teams."));
    }
    // Mutex : deux boutons de réparation coexistent à l'écran (le panneau de
    // source ET le bandeau no-speech) et peuvent tous deux appeler shareTab().
    // Sans lui, un double-clic (ou un clic pendant qu'une 1re demande est déjà
    // en vol) lance DEUX `getDisplayMedia()` concurrents ; le second à résoudre
    // fait un `detachTab()` qui arrête les pistes du PREMIER — celui-ci vient
    // de rendre un flux mort à `swapSource()` sans que personne ne le sache
    // (revue adversariale 2026-07-31, trouvé indépendamment par les 2 chasseurs).
    if (partageEnCours) {
      return Promise.reject(new Error(
        'Une demande de partage est déjà en cours — patiente le temps de la fenêtre du navigateur.'));
    }
    partageEnCours = true;
    return navigator.mediaDevices.getDisplayMedia({
      video: true,                              // exigée par le navigateur pour accorder l'audio
      audio: {
        // Sans cela le navigateur peut COUPER le son de la réunion côté
        // utilisateur pendant qu'on le capte : il n'entendrait plus son
        // interlocuteur. Explicite, parce que le défaut n'est pas garanti.
        suppressLocalAudioPlayback: false
      }
    }).then(function (s) {
      var pistes = s.getAudioTracks();
      if (!pistes.length) {
        s.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
        throw new Error(
          "Le partage a bien démarré mais SANS le son : dans la fenêtre de partage, " +
          "choisis l'onglet de la réunion puis coche « Partager aussi l'audio de l'onglet ».");
      }
      detachTab();          // un repartage remplace le précédent
      tabStream = s;
      tabEnded = false;
      pistes[0].addEventListener('ended', function () {
        // L'utilisateur a cliqué « Arrêter le partage », ou fermé l'onglet.
        tabEnded = true;
        if (onTabLost) onTabLost();
      });
      return acquireMic().then(buildMix, function (err) {
        // Le micro était déjà acquis à ce stade pour toute session en cours
        // (`acquireMic()` le sert en cache) — ce rejet ne peut donc arriver
        // qu'en pré-vol, avant tout enregistrement. Sans ce `detachTab()`,
        // `tabStream` resterait assigné et `tabActive()` répondrait "capté"
        // pour un flux jamais réellement mélangé (edge case trouvé en revue).
        detachTab();
        throw err;
      });
    }).finally(function () {
      partageEnCours = false;
    });
  }

  function release() {
    stopMeters();
    detachTab();
    if (micNode) { try { micNode.disconnect(); } catch (e) {} }
    micNode = null;
    micAnalyser = null;
    if (micStream) {
      micStream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
    }
    micStream = null;
    dest = null;
    if (ctx) { try { ctx.close(); } catch (e) {} }
    ctx = null;
  }

  window.i2dAudioSource = {
    supported: supported,
    acquire: acquire,
    preflightMic: preflightMic,
    shareTab: shareTab,
    tabActive: tabActive,
    release: release,
    on: function (opts) {
      if (opts.tabLost) onTabLost = opts.tabLost;
      if (opts.levels) onLevels = opts.levels;
    }
  };
})();
