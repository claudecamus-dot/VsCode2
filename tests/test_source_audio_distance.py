"""Tests de l'incrément « garantir l'audio quelle que soit la situation »
(2026-07-31), suite directe du diagnostic mission 16.

RAPPEL DU DÉFAUT RÉEL : les deux écrans d'enregistrement appelaient
`getUserMedia({audio: true})` — le navigateur ne capte alors que le micro
PHYSIQUE. Pour un entretien Google Meet, la voix des participants distants sort
dans le casque et n'entre jamais dans le périphérique capturé : ~100 min
enregistrées à moitié muettes, matière non récupérable. Le correctif livré le
2026-07-30 (bannière no-speech) ne faisait que CONSTATER la perte, à la 2e
minute, au moment précis où l'utilisateur est en entretien et ne peut plus rien
faire. Trois changements testés ici :

1. **Acquisition** — un mode « entretien à distance » capte AUSSI le son de
   l'onglet (module partagé `/static/rec_audio_source.js`, mélange WebAudio).
2. **Pré-vol bloquant** — le démarrage est interdit tant que la piste audio
   d'onglet est absente, avec une dérogation explicite.
3. **Réparation en place** — le bandeau d'alerte PORTE le geste qui corrige
   (demande utilisateur : « proposer dans le message l'action pour résoudre le
   problème »), utilisable SANS interrompre l'enregistrement.

Plus le repli documenté : accepter le `.mp4` produit par Meet/Teams à l'import.

LIMITE ASSUMÉE, identique à celle des gates `lostSegments` (2026-07-29) et
`rec-nospeech-banner` (2026-07-30) : ce projet n'a pas de harnais Node pour
exécuter le JS de `record*.html`. Les tests de comportement JS sont donc des
assertions de PRÉSENCE dans le HTML rendu — plus faibles qu'une exécution, mais
ils échouent bel et bien sur le code d'avant (R1) et attrapent la régression la
plus probable : un correctif appliqué à un seul des deux écrans, qui est
l'ornière documentée de ce projet.
"""
from __future__ import annotations

import io
import re

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, engine, init_db
from app.main import app
from app.services import audio_transcribe


def setup_module() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def teardown_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _mission_id(client: TestClient) -> int:
    response = client.post(
        "/missions", data={"name": "Mission entretien à distance"}, follow_redirects=False
    )
    assert response.status_code in (200, 303)
    return int(response.headers.get("location", "").rstrip("/").split("/")[-1])


def _ecrans(client: TestClient) -> dict[str, str]:
    """Les DEUX écrans d'enregistrement, rendus. Toute assertion de cet incrément
    porte sur les deux : l'historique du projet montre que les correctifs
    n'étaient appliqués qu'à un seul, et l'asymétrie repoussait."""
    mission = _mission_id(client)
    libre = client.get(f"/missions/{mission}/interviews/record-libre")
    structure = client.get(f"/missions/{mission}/interviews/record")
    assert libre.status_code == 200, libre.status_code
    assert structure.status_code == 200, structure.status_code
    return {"record_libre.html": libre.text, "record.html": structure.text}


# --------------------------------------------------------------------------- #
# 1. Acquisition : la capture du son de l'onglet existe, sur les deux écrans
# --------------------------------------------------------------------------- #
def test_les_deux_ecrans_proposent_le_mode_entretien_a_distance(client: TestClient) -> None:
    for nom, html in _ecrans(client).items():
        assert 'id="rec-remote-mode"' in html, nom
        assert "Entretien à distance" in html, nom
        assert 'id="rec-share-tab"' in html, nom


def test_les_deux_ecrans_rappellent_le_consentement_des_participants_distants(
    client: TestClient,
) -> None:
    """Point §5 de docs/reflexions/enregistrement-visio-meet-teams.md resté sans
    réponse à la livraison du mode à distance (2026-07-31, arbitré ensuite : simple
    rappel non bloquant plutôt qu'une case à cocher bloquante). Affiché dans le
    panneau qui apparaît quand l'utilisateur active ce mode — l'endroit naturel
    identifié par la réflexion, pas une mention perdue ailleurs sur l'écran."""
    # "participants distants" seul existe déjà ailleurs sur l'écran (messages
    # d'erreur/dérogation JS) : l'assertion doit cibler le rappel lui-même, pas
    # une occurrence incidente, sans quoi elle ne romprait pas sur le code d'avant.
    for nom, html in _ecrans(client).items():
        assert "Informe les participants distants" in html, nom
        assert "obtiens leur accord avant de démarrer" in html, nom


def test_les_deux_ecrans_affichent_un_vumetre_par_source(client: TestClient) -> None:
    """Les deux sources sont montrées séparément : c'est ce qui permet de voir,
    avant de démarrer, laquelle des deux est muette."""
    for nom, html in _ecrans(client).items():
        assert 'id="rec-meter-mic"' in html, nom
        assert 'id="rec-meter-tab"' in html, nom
        assert "Son de la réunion" in html, nom


def test_r1_les_ecrans_n_acquierent_plus_le_micro_en_direct(client: TestClient) -> None:
    """RÉGRESSION R1 — échoue sur le code d'avant.

    Avant le correctif, chaque écran appelait lui-même
    `navigator.mediaDevices.getUserMedia({ audio: true })` : la seule source
    possible était le micro physique, donc l'entretien à distance était
    structurellement condamné à ne capter qu'une voix sur deux. L'acquisition
    passe désormais par le module partagé, seul endroit qui sait mélanger le
    micro et le son de l'onglet."""
    for nom, html in _ecrans(client).items():
        script = html[html.find("<script") :]
        assert "getUserMedia({ audio: true })" not in script, (
            f"{nom} acquiert encore le micro en direct : le mode à distance ne peut pas "
            "fonctionner, la voix des participants ne sera jamais captée."
        )
        assert "i2dAudioSource.acquire(" in script, nom


def test_le_module_de_source_audio_est_charge_et_servi(client: TestClient) -> None:
    """Le module est servi en LOCAL (l'app est offline-first) et chargé par les
    deux écrans via base.html."""
    for nom, html in _ecrans(client).items():
        assert "/static/rec_audio_source.js" in html, nom

    module = client.get("/static/rec_audio_source.js")
    assert module.status_code == 200
    for symbole in (
        "getDisplayMedia",                 # capture du son de l'onglet
        "createMediaStreamDestination",    # mélange micro + onglet
        "suppressLocalAudioPlayback",      # l'utilisateur continue d'ENTENDRE la réunion
        "window.i2dAudioSource",
    ):
        assert symbole in module.text, symbole


def test_le_module_ne_coupe_pas_le_son_de_la_reunion_pour_l_utilisateur(
    client: TestClient,
) -> None:
    """`suppressLocalAudioPlayback: false` explicite : à `true` (ou laissé au
    défaut du navigateur), capter l'onglet peut COUPER le son de la réunion côté
    utilisateur — il n'entendrait plus son interlocuteur. Le correctif rendrait
    l'entretien impossible au lieu de le sauver."""
    module = client.get("/static/rec_audio_source.js").text
    assert re.search(r"suppressLocalAudioPlayback\s*:\s*false", module)


# --------------------------------------------------------------------------- #
# 2. Pré-vol : on ne démarre pas un entretien à distance à moitié muet
# --------------------------------------------------------------------------- #
def test_le_demarrage_est_garde_par_la_presence_de_la_piste_onglet(
    client: TestClient,
) -> None:
    """La garde porte sur `tabActive()` — la PRÉSENCE de la piste audio — et non
    sur un niveau sonore : au moment du réglage, personne n'a encore parlé dans
    la réunion ; un critère de vumètre bloquerait indéfiniment. La piste absente
    est exactement le mode d'échec réel (case « partager l'audio » non cochée)."""
    for nom, html in _ecrans(client).items():
        script = html[html.find("<script") :]
        assert "function sourceReady()" in script, nom
        assert "i2dAudioSource.tabActive()" in script, nom
        # La garde est réellement appliquée au démarrage, pas seulement définie.
        assert "if (!sourceReady())" in script, nom
        assert "startBtn.disabled = !sourceReady();" in script, nom


def test_la_derogation_existe_et_est_explicite(client: TestClient) -> None:
    """Bloquer sans issue serait pire que le mal : si le partage d'onglet échoue,
    il ne faut jamais se retrouver dans l'impossibilité d'enregistrer. La
    dérogation existe, elle passe par une confirmation qui dit ce qu'on perd."""
    for nom, html in _ecrans(client).items():
        assert 'id="rec-remote-bypass"' in html, nom
        script = html[html.find("<script") :]
        assert "remoteBypass = true;" in script, nom
        assert "ne sera PAS " in script, nom  # la confirmation nomme la perte


# --------------------------------------------------------------------------- #
# 3. Réparation : le message porte l'action (demande utilisateur explicite)
# --------------------------------------------------------------------------- #
def test_r1_le_bandeau_no_speech_porte_le_geste_de_reparation(client: TestClient) -> None:
    """RÉGRESSION R1 — échoue sur le code d'avant.

    Le bandeau livré le 2026-07-30 ne contenait qu'un `<span>` de texte. Il
    prévient à la 2e minute, quand l'utilisateur est en entretien avec un client :
    interrompre, expliquer, repartager un onglet et relancer l'enregistrement
    n'arrivera jamais. Une alerte sans geste de réparation ne vaut rien — le
    bandeau porte désormais le bouton qui corrige."""
    for nom, html in _ecrans(client).items():
        assert 'id="rec-nospeech-fix"' in html, nom
        bandeau = html[html.find('id="rec-nospeech-banner"') :][:800]
        assert "rec-nospeech-fix" in bandeau, (
            f"{nom} : le bouton de réparation doit être DANS le bandeau d'alerte, "
            "pas ailleurs sur l'écran."
        )
        script = html[html.find("<script") :]
        assert "noSpeechFixBtn.addEventListener" in script, nom


def test_la_reparation_ne_coupe_pas_l_enregistrement_en_cours(client: TestClient) -> None:
    """Le geste doit être jouable PENDANT l'entretien : `swapSource` fait relire
    la source aux enregistreurs par le mécanisme de rotation déjà éprouvé, et ne
    fait rien du tout quand la source est déjà le mélangeur."""
    for nom, html in _ecrans(client).items():
        script = html[html.find("<script") :]
        assert "function swapSource(" in script, nom
        assert "var inchange = (nouveau === stream);" in script, nom
        assert "if (!recordingActive || inchange) return" in script, nom


def test_record_html_ne_coupe_jamais_sa_sauvegarde_audio_sur_une_bascule(
    client: TestClient,
) -> None:
    """ASYMÉTRIE RÉELLE ENTRE LES DEUX ÉCRANS, à ne pas « uniformiser » par
    inadvertance.

    Sur `record_libre.html`, `backupRecorder` se relance seul (rotation 30 min)
    et `audio_segments` accumule les tranches : le rotationner est sans perte.
    Sur `record.html` il ne se relance PAS (« l'arrêt du backup est toujours
    final ») et le formulaire ne porte qu'UN chemin de sauvegarde — le couper en
    deux y laisserait un fichier orphelin sur le disque. La bascule ne doit donc
    toucher que l'enregistreur de transcription sur cet écran."""
    ecrans = _ecrans(client)
    libre = ecrans["record_libre.html"]
    structure = ecrans["record.html"]

    corps_libre = libre[libre.find("function swapSource(") :][:1400]
    assert "backupRecorder.stop()" in corps_libre

    corps_structure = structure[structure.find("function swapSource(") :][:1400]
    assert "backupRecorder.stop()" not in corps_structure, (
        "record.html couperait définitivement sa sauvegarde audio au milieu de "
        "l'entretien, et laisserait la première tranche orpheline sur le disque."
    )


def test_les_deux_ecrans_liberent_le_partage_a_l_arret(client: TestClient) -> None:
    """En mode à distance, `stream` est la SORTIE du mélangeur : arrêter ses
    pistes ne coupe ni le micro ni le partage d'onglet — le navigateur
    continuerait d'afficher « cet onglet est partagé » après l'entretien."""
    for nom, html in _ecrans(client).items():
        script = html[html.find("<script") :]
        assert "i2dAudioSource.release();" in script, nom


# --------------------------------------------------------------------------- #
# 4. Repli : importer l'enregistrement produit par Meet/Teams (.mp4)
# --------------------------------------------------------------------------- #
def test_l_import_accepte_la_video_produite_par_meet_ou_teams(client: TestClient) -> None:
    """R1 — échoue sur le code d'avant (`accept="audio/*"` masquait tout `.mp4`).

    Vérifié en lisant le reste de la chaîne avant d'ouvrir l'attribut : la route
    `/audio/transcribe-file` ne filtre ni l'extension ni le content-type, et
    `_decode_to_pcm16k` passe par PyAV, agnostique du conteneur."""
    for nom, html in _ecrans(client).items():
        champs = re.findall(r'<input type="file"[^>]*>', html)
        assert champs, nom
        assert any("video/*" in c or ".mp4" in c for c in champs), (
            f"{nom} : le sélecteur masque encore les vidéos, donc l'enregistrement "
            "exporté depuis Meet/Teams est inimportable."
        )


def _mp4_sans_piste_audio() -> bytes:
    """Un vrai conteneur mp4 ne portant qu'une piste vidéo — le cas exact qu'on
    vient de rendre atteignable en acceptant les vidéos à l'import."""
    av = pytest.importorskip("av")
    tampon = io.BytesIO()
    container = av.open(tampon, mode="w", format="mp4")
    flux = container.add_stream("mpeg4", rate=5)
    flux.width, flux.height = 32, 32
    flux.pix_fmt = "yuv420p"
    for _ in range(3):
        image = av.VideoFrame(32, 32, "yuv420p")
        for paquet in flux.encode(image):
            container.mux(paquet)
    for paquet in flux.encode():
        container.mux(paquet)
    container.close()
    return tampon.getvalue()


def test_une_video_muette_donne_un_message_lisible_pas_une_trace_de_pile() -> None:
    """R1 — échoue sur le code d'avant (`IndexError` nu sur `streams.audio[0]`).

    Ce cas était inatteignable tant que le sélecteur n'acceptait que de l'audio ;
    ouvrir l'`accept` le rend possible. On invite l'utilisateur à déposer une
    vidéo, on lui doit une phrase compréhensible quand elle est muette."""
    contenu = _mp4_sans_piste_audio()
    with pytest.raises(audio_transcribe.TranscriptionError) as erreur:
        audio_transcribe._decode_to_pcm16k(contenu)
    message = str(erreur.value)
    assert "aucune piste audio" in message.lower()
    assert "Meet" in message or "Teams" in message


def test_la_video_muette_ne_remonte_pas_en_500(client: TestClient) -> None:
    """Bout en bout : le message lisible doit atteindre l'utilisateur par le
    contrat d'erreur habituel (422 `{"error": ...}`), jamais une 500 brute."""
    fichier = {"file": ("reunion.mp4", _mp4_sans_piste_audio(), "video/mp4")}
    reponse = client.post("/audio/transcribe-segment", files=fichier)
    assert reponse.status_code == 422, reponse.status_code
    assert "piste audio" in reponse.json().get("error", "").lower()


# --------------------------------------------------------------------------- #
# 5. Correctifs de la revue adversariale (Blind Hunter + Edge Case Hunter,
#    bmad-code-review, 2026-07-31) — les 7 défauts retenus après vérification
#    du code réel. Chaque test cite le défaut exact et pourquoi il tenait.
# --------------------------------------------------------------------------- #
def test_r1_le_script_source_audio_charge_avant_le_script_de_page(
    client: TestClient,
) -> None:
    """RÉGRESSION R1 — le défaut le plus grave trouvé par la revue, confirmé
    INDÉPENDAMMENT par les 2 chasseurs, et vérifié empiriquement par une
    reproduction en Edge headless (pas seulement une lecture de code) : les deux
    écrans sont restés COMPLÈTEMENT INERTES (Démarrer, Arrêter, Envoyer, import
    de fichier — pas seulement la nouveauté) de leur mise en ligne jusqu'à ce
    correctif, sans qu'AUCUN des 573 tests de la suite (dont 14 dédiés à cette
    fonctionnalité) ne le détecte — tous asserted la PRÉSENCE de texte dans le
    HTML rendu, jamais l'exécution.

    Cause : `/static/rec_audio_source.js` était chargé `defer` (base.html) ;
    `record*.html` consomment `window.i2dAudioSource` de façon SYNCHRONE dans
    leur propre `<script>` inline, NON différé. Un script différé ne s'exécute
    qu'après l'analyse complète du document — donc APRÈS un `<script>` inline
    plus bas dans le corps. `i2dAudioSource` y était `undefined`
    (`ReferenceError`), exception qui coupait l'IIFE de configuration avant
    l'inscription du moindre `addEventListener`."""
    for nom, html in _ecrans(client).items():
        avant_body = html[: html.find("<body")]
        assert re.search(
            r'<script src="/static/rec_audio_source\.js"(?!\s+defer)[^>]*></script>',
            avant_body,
        ), f"{nom} : rec_audio_source.js doit être chargé SANS defer, avant le <body>"


def test_r1_alerte_de_reparation_onglet_reservee_a_l_entretien_a_distance(
    client: TestClient,
) -> None:
    """RÉGRESSION R1 — un entretien PRÉSENTIEL silencieux (mauvais placement du
    micro, simple pause) déclenchait la même invite « partage l'onglet de la
    réunion » qu'un vrai décrochage à distance : `reparable` ne testait que
    `i2dAudioSource.supported() && !i2dAudioSource.tabActive()`, jamais l'état
    de la case à cocher. Un utilisateur en présentiel n'a AUCUNE réunion à
    partager — la garde manquait, `remoteModeEl.checked` en tête corrige."""
    for nom, html in _ecrans(client).items():
        script = html[html.find("<script") :]
        assert (
            "var reparable = remoteModeEl.checked && i2dAudioSource.supported()"
            in script
        ), nom


def test_r1_case_entretien_a_distance_verrouillee_pendant_l_enregistrement(
    client: TestClient,
) -> None:
    """RÉGRESSION R1 — la case restait cochable/décochable pendant
    l'enregistrement sans aucun effet sur le flux réellement capturé (le
    mélange en cours ne changeait pas) : décocher donnait l'illusion d'avoir
    arrêté la capture de l'onglet alors qu'elle continuait. Verrouillée au
    démarrage, déverrouillée à l'arrêt — le seul geste utile pendant
    l'enregistrement (partager/réparer) reste accessible via le bandeau
    no-speech, qui n'a pas cette ambiguïté."""
    for nom, html in _ecrans(client).items():
        script = html[html.find("<script") :]
        assert "remoteModeEl.disabled = true;" in script, nom
        assert "remoteModeEl.disabled = false;" in script, nom


def test_r1_plafond_segments_perdus_ne_se_desactive_pas_a_zero_place(
    client: TestClient,
) -> None:
    """RÉGRESSION R1 — `muets.slice(-place)` avec `place = 0` (≥ 8 segments
    bloquants à eux seuls, le pire cas que ce plafond doit couvrir) rendait le
    tableau ENTIER : en JavaScript, `-0 === 0`, donc `slice(-0)` équivaut à
    `slice(0)` et non à « les 0 derniers éléments ». Le plafond se désactivait
    tout seul exactement quand il devait le plus servir (cas réel mission 16 :
    90 segments)."""
    for nom, html in _ecrans(client).items():
        script = html[html.find("<script") :]
        assert "place > 0 ? muets.slice(-place) : []" in script, nom


def test_r1_partage_onglet_a_un_mutex(client: TestClient) -> None:
    """RÉGRESSION R1 — deux boutons de réparation coexistent à l'écran (le
    panneau de source ET le bandeau no-speech, ce dernier apparaissant PENDANT
    que le premier est déjà ouvert) et appellent tous deux `shareTab()`. Sans
    mutex, un double-clic lance deux `getDisplayMedia()` concurrents ; le
    second à résoudre fait un `detachTab()` qui arrête les pistes du premier —
    celui-ci vient de rendre un flux déjà mort à `swapSource()`, sans que
    personne ne le sache. Trouvé indépendamment par les 2 chasseurs."""
    module = client.get("/static/rec_audio_source.js").text
    assert "var partageEnCours = false;" in module
    assert "if (partageEnCours) {" in module
    assert "partageEnCours = true;" in module
    assert "partageEnCours = false;\n    });" in module or re.search(
        r"\.finally\(function \(\) \{\s*partageEnCours = false;", module
    )


def test_r1_partage_onglet_libere_le_flux_si_le_micro_echoue_apres(
    client: TestClient,
) -> None:
    """RÉGRESSION R1 — si `acquireMic()` rejette APRÈS que `shareTab()` a déjà
    assigné `tabStream` (le partage d'onglet a réussi, mais le micro échoue
    ensuite), `tabStream` restait assigné : `tabActive()` répondait "capté"
    pour un flux jamais réellement mélangé dans l'enregistrement (le
    `swapSource()` correspondant n'a jamais eu lieu, seule la branche `.catch`
    s'exécute). `detachTab()` doit tourner AVANT de propager l'erreur."""
    module = client.get("/static/rec_audio_source.js").text
    debut = module.find("return acquireMic().then(buildMix, function (err) {")
    assert debut >= 0, "callback de rejet introuvable"
    bloc = module[debut : module.find("});", debut) + 3]
    assert "detachTab();" in bloc
    assert "throw err;" in bloc


def test_r1_vumetre_micro_demarre_sans_attendre_le_partage_onglet(
    client: TestClient,
) -> None:
    """RÉGRESSION R1 — cocher « Entretien à distance » appelait
    `i2dAudioSource.acquire(false)`, qui (par construction : `if (!remote ||
    !tabActive()) return mic;`) rend le flux micro SANS jamais appeler
    `buildMix()` — la seule fonction qui démarre les vumètres. Le vumètre
    micro restait donc figé à 0 % tant que l'onglet n'était pas partagé,
    contredisant le commentaire qui l'appelait (« vérifier les deux sources
    maintenant »). `preflightMic()` (nouveau, exporté) acquiert le micro ET
    démarre son vumètre, sans dépendre du partage d'onglet."""
    module = client.get("/static/rec_audio_source.js").text
    assert "function preflightMic()" in module
    assert "return acquireMic().then(buildMix);" in module
    assert "preflightMic: preflightMic," in module

    for nom, html in _ecrans(client).items():
        script = html[html.find("<script") :]
        assert "i2dAudioSource.preflightMic()" in script, nom
        assert "i2dAudioSource.acquire(false)" not in script, (
            f"{nom} utilise encore acquire(false), qui ne démarre aucun vumètre"
        )


def test_r1_decode_leve_explicitement_si_le_garde_fou_ne_leve_pas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RÉGRESSION R1 — fonctionnel, pas une assertion de présence : ce module
    est en Python, donc réellement exécutable.

    `_decode_to_pcm16k` appelait `_exiger_piste_audio(content)` puis retombait
    sur `container.streams.audio[0]` (conteneur déjà FERMÉ) en supposant
    implicitement que l'appel précédent avait forcément levé. `_exiger_piste_
    audio` est volontairement tolérante (elle ne lève que si elle a pu
    ré-ouvrir le contenu ET confirmer l'absence de piste) : si un futur
    changement élargit sa clause `except`, l'appelant retombait silencieusement
    sur un accès à un conteneur fermé au lieu du message lisible. Ce test force
    ce scénario (le garde-fou rend la main sans lever) et exige que
    `_decode_to_pcm16k` lève quand même son propre message explicite."""
    monkeypatch.setattr(audio_transcribe, "_exiger_piste_audio", lambda content: None)
    with pytest.raises(audio_transcribe.TranscriptionError) as erreur:
        audio_transcribe._decode_to_pcm16k(_mp4_sans_piste_audio())
    assert "aucune piste audio" in str(erreur.value).lower()
