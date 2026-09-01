"""Garde-fous réseau des écrans d'enregistrement (revue du 2026-08-31).

pytest ne rend pas les templates et n'exécute pas le navigateur : il est
structurellement aveugle au JS de `record.html` / `record_libre.html`, où trois
bugs de concurrence sont déjà passés sous une suite verte. Ce fichier ne
prétend donc pas tester le parcours — il tient deux invariants VÉRIFIABLES à
froid, plus une exécution RÉELLE du helper réseau sous node :

1. aucun `fetch(` nu ne subsiste dans les deux écrans (tout passe par
   `recFetch`, qui borne l'attente) ;
2. `rec_fetch.js` est chargé sans `defer`, comme `rec_audio_source.js` — les
   `<script>` inline le consomment à l'analyse du `<body>`, et un chargement
   différé le rendrait indéfini à l'appel SANS erreur au chargement ;
3. `recFetch` rejette réellement quand la réponse ne vient jamais, et annule
   son minuteur quand elle vient — testé en exécutant le fichier, pas en y
   cherchant des chaînes.

Aucun accès base : ce module ne touche ni `DB_PATH` ni `SessionLocal`.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
# TOUS les écrans du parcours d'enregistrement qui font des requêtes JS — pas
# seulement les deux écrans principaux : les écrans d'attente et de
# retranscription portent un poll en CHAÎNE de setTimeout, où un fetch nu
# jamais réglé tue définitivement le suivi (revue R3-M2 du 2026-08-31), et
# capture.html porte la transcription de notes (même défaut, chemin frère).
ECRANS = [
    RACINE / "app" / "templates" / "interviews" / "record_libre.html",
    RACINE / "app" / "templates" / "interviews" / "record.html",
    RACINE / "app" / "templates" / "interviews" / "libre_segment_wait.html",
    RACINE / "app" / "templates" / "interviews" / "record_segment_wait.html",
    RACINE / "app" / "templates" / "interviews" / "libre_retranscription.html",
    RACINE / "app" / "templates" / "interviews" / "capture.html",
]
REC_FETCH = RACINE / "app" / "static" / "rec_fetch.js"
BASE_HTML = RACINE / "app" / "templates" / "base.html"


@pytest.mark.parametrize("ecran", ECRANS, ids=lambda p: p.name)
def test_aucun_fetch_nu_dans_les_ecrans_d_enregistrement(ecran: Path) -> None:
    """Un `fetch()` nu n'a AUCUN délai maximal : sur une connexion à demi
    ouverte (VPN qui tombe, machine en veille, serveur figé) sa promesse ne se
    règle ni en `.then` ni en `.catch`, les compteurs de requêtes en vol ne
    redescendent jamais et « Enregistrer l'entretien » reste grisé pour la durée
    de vie de la page — sur une transcription complète, avec pour seule issue
    « Recommencer », qui la détruit. Mesuré le 2026-08-31 : 16 appels nus, 0
    AbortController."""
    # `(?<![A-Za-z0-9_.])` écarte `recFetch(` (et tout `x.fetch(`). La version
    # précédente exigeait en plus un littéral de chaîne en 1er argument :
    # `fetch(url, …)`, `fetch(\n '/x')` ou `fetch(base + '/x')` passaient sans
    # être détectés (revue R3-m2 du 2026-08-31). On matche donc tout `fetch(`
    # nu, et on écarte seulement les mentions en commentaire `//` (seule forme
    # sous laquelle `fetch(` apparaît hors code dans ces écrans).
    appel_nu = re.compile(r"(?<![A-Za-z0-9_.])fetch\s*\(")
    lignes = []
    for i, l in enumerate(ecran.read_text(encoding="utf-8").splitlines(), 1):
        m = appel_nu.search(l)
        if not m:
            continue
        commentaire = l.find("//")
        if 0 <= commentaire < m.start():
            continue
        lignes.append((i, l.strip()))
    assert not lignes, (
        f"{ecran.name} : {len(lignes)} appel(s) `fetch(` nu(s) — passer par "
        f"`recFetch(url, options, delai)` : {lignes[:3]}"
    )


def _sans_commentaires(contenu: str) -> str:
    """Retire les commentaires `//` de fin de ligne, en épargnant les `://` des
    URL. Appliqué AVANT tout comptage d'accolades (re-revue F6 du 2026-09-01 :
    l'ordre inverse laissait une accolade EN COMMENTAIRE fausser les bornes,
    donc un test rouge sur du code correct, avec un message désignant autre
    chose)."""
    sorties = []
    for ligne in contenu.splitlines():
        pos, i = None, ligne.find("//")
        while i >= 0:
            if i == 0 or ligne[i - 1] != ":":
                pos = i
                break
            i = ligne.find("//", i + 1)
        sorties.append(ligne if pos is None else ligne[:pos])
    return "\n".join(sorties)


def _corps_de_fonction(contenu_sans_commentaires: str, depart: int) -> str:
    """Corps de la fonction dont l'accolade ouvrante suit `depart`, par comptage
    d'accolades. L'appelant DOIT passer un texte déjà nettoyé de ses
    commentaires — on teste le CODE, pas ce que les commentaires racontent."""
    contenu = contenu_sans_commentaires
    debut = contenu.index("{", depart)
    profondeur, i = 0, debut
    while i < len(contenu):
        if contenu[i] == "{":
            profondeur += 1
        elif contenu[i] == "}":
            profondeur -= 1
            if profondeur == 0:
                break
        i += 1
    return contenu[debut : i + 1]


@pytest.mark.parametrize(
    "ecran",
    [
        RACINE / "app" / "templates" / "interviews" / "record_libre.html",
        RACINE / "app" / "templates" / "interviews" / "record.html",
    ],
    ids=lambda p: p.name,
)
def test_le_delai_maximal_ne_declenche_pas_de_relance_automatique(ecran: Path) -> None:
    """R3-M5/M6 (2026-08-31) : un délai maximal atteint (err.recTimeout) ne doit
    JAMAIS relancer automatiquement les mêmes octets — la fenêtre généreuse a
    déjà été attendue en entier, et l'abort client n'annule pas le travail
    Whisper côté serveur : rejouer double la charge d'un serveur déjà noyé et
    prolonge le gel (mesuré : 3 × 900 s ≈ 45 min bouton grisé).

    Vérifié PAR SITE D'APPEL (revue EC-4 du 2026-09-01) : la version d'avant ne
    cherchait `timedOut` qu'à l'échelle du FICHIER, or chaque écran porte deux
    `retryOrGiveUp` (segment de transcription, sauvegarde audio). Débrancher la
    politique de l'UN des deux la laissait verte — précisément le mode d'échec
    récurrent de ces deux fichiers (un correctif posé sur un chemin, oublié sur
    son frère).

    Deux moitiés à tenir, et la re-revue F2 du 2026-09-01 a montré qu'en
    vérifier une seule ne sert à rien : le CÂBLAGE (chaque `.catch` passe bien
    l'étiquette de délai) et l'APPELÉ (chaque `retryOrGiveUp` la consulte avant
    de programmer sa relance). La version d'avant cherchait `err.recTimeout` à
    l'échelle du fichier — satisfaite par les quatre lignes de COMMENTAIRE qui
    le mentionnent : débrancher les deux câblages la laissait verte."""
    contenu = _sans_commentaires(ecran.read_text(encoding="utf-8"))

    # 1. CÂBLAGE : les deux `.catch` étiquettent le rejet de délai.
    cables = re.findall(r"retryOrGiveUp\([^)]*recTimeout", contenu)
    assert len(cables) == 2, (
        f"{ecran.name} : {len(cables)} site(s) d'appel passent `err.recTimeout` "
        "à `retryOrGiveUp` au lieu de 2 (segment de transcription + sauvegarde "
        "audio). Sans l'étiquette, `timedOut` vaut `undefined` et CHAQUE délai "
        "relance automatiquement — le gel de ~45 min que R3-M5/M6 a corrigé"
    )

    # 2. APPELÉ : chaque `retryOrGiveUp` consulte `timedOut` avant de relancer.
    departs = [
        m.start() for m in re.finditer(r"function retryOrGiveUp\s*\(", contenu)
    ]
    assert len(departs) == 2, (
        f"{ecran.name} porte {len(departs)} `retryOrGiveUp` au lieu de 2 "
        "(segment de transcription + sauvegarde audio) — si un site d'appel a "
        "été ajouté ou retiré, ce test doit être revu, pas contourné"
    )
    for depart in departs:
        signature = contenu[depart : contenu.index("{", depart)]
        assert "timedOut" in signature, (
            f"{ecran.name} : un `retryOrGiveUp` ne prend plus le paramètre "
            "`timedOut` — il ne peut plus distinguer un délai maximal d'un "
            "échec réseau ordinaire"
        )
        corps = _corps_de_fonction(contenu, depart)
        avant_relance = corps.split("setTimeout")[0]
        assert re.search(r"(!timedOut\s*&&|if\s*\(\s*timedOut\s*\))", avant_relance), (
            f"{ecran.name} : un `retryOrGiveUp` programme sa relance sans se "
            "BRANCHER sur `timedOut` (une simple mention ne suffit pas) — sur "
            "délai maximal il rejoue les mêmes octets vers un serveur déjà "
            "noyé, et prolonge le gel du bouton"
        )


@pytest.mark.parametrize(
    "ecran",
    [
        RACINE / "app" / "templates" / "interviews" / "record_libre.html",
        RACINE / "app" / "templates" / "interviews" / "record.html",
    ],
    ids=lambda p: p.name,
)
def test_la_parole_recuperee_dans_la_tranche_en_vol_part_en_job(ecran: Path) -> None:
    """EC-2 (2026-09-01) : `replaceLostMarker` décale bien `flightSliceEnd`
    quand la substitution tombe DANS la tranche en vol (R3-M4), mais ne posait
    de job d'extraction que pour `pos < coveredLen`. Or la résolution du POST
    avance ensuite `coveredLen` jusqu'à cette frontière, alors que le job en vol
    porte le MARQUEUR (texte figé à l'envoi) et non la parole récupérée : elle
    n'entrait NI dans un job NI dans le reliquat — présente dans le textarea et
    le PDF, absente du tour de table.

    Mais la soumettre TOUT DE SUITE produit un DOUBLON quand le POST en vol
    échoue (re-revue F1, reproduit) : son `.catch` n'avance pas `coveredLen`,
    la parole reste donc AUSSI dans le reliquat et repart une seconde fois — et
    c'est le même incident réseau qui a produit le segment perdu, donc le cas
    est fréquent. La couverture n'est connue qu'à la RÉSOLUTION : d'où une file
    d'attente, vidée au succès et jetée à l'échec.

    Ce test est STRUCTUREL — il fige le mécanisme, pas son comportement. Le
    comportement (0 perte, 0 doublon, sur les 2 issues du POST) demande le
    harnais node ; c'est la limite assumée, cf. F7."""
    contenu = _sans_commentaires(ecran.read_text(encoding="utf-8"))

    corps = _corps_de_fonction(contenu, contenu.index("function replaceLostMarker"))
    assert "recuperesEnAttente.push" in corps, (
        f"{ecran.name} : `replaceLostMarker` ne met plus en attente la parole "
        "récupérée pendant le vol d'une tranche. Soit elle n'est extraite par "
        "personne (le job en vol porte le MARQUEUR, pas elle), soit elle l'est "
        "deux fois si on la soumet sans attendre l'issue du POST"
    )
    apres_push = corps.split("recuperesEnAttente.push")[0]
    assert "dansTrancheEnVol" in apres_push.rsplit("else if", 1)[-1], (
        f"{ecran.name} : la mise en attente n'est pas conditionnée à "
        "`dansTrancheEnVol` — le cas `dejaCouvert`, lui, a sa couverture ACQUISE "
        "et doit partir immédiatement"
    )

    envoi = _corps_de_fonction(contenu, contenu.index("function submitSegmentJob"))
    succes = envoi.split(".catch")[0]
    assert "recuperesEnAttente.forEach" in succes, (
        f"{ecran.name} : la file des paroles récupérées n'est pas vidée dans le "
        "chemin de SUCCÈS de `submitSegmentJob`. Au succès `coveredLen` passe "
        "par-dessus elles : sans leur job dédié, plus personne ne les extrait"
    )
    assert succes.index("coveredLen =") < succes.index("recuperesEnAttente.forEach"), (
        f"{ecran.name} : la file est vidée AVANT l'avancée de `coveredLen` — "
        "l'ordre importe, le job dédié n'a de sens qu'une fois la parole sortie "
        "du reliquat"
    )
    assert re.search(r"recuperesEnAttente\s*=\s*\[\]", envoi.rsplit(".finally", 1)[-1]), (
        f"{ecran.name} : la file n'est pas remise à zéro dans le `.finally` — "
        "une parole en attente survivrait au POST suivant et serait soumise "
        "deux fois"
    )


def test_rec_fetch_charge_sans_defer() -> None:
    """Même règle que `rec_audio_source.js`. Avec `defer`, `window.recFetch`
    serait encore indéfini quand le `<script>` inline de l'écran l'appelle : la
    page se chargerait sans erreur et casserait au premier upload."""
    base = BASE_HTML.read_text(encoding="utf-8")
    ligne = next(
        (l for l in base.splitlines() if "rec_fetch.js" in l and "<script" in l), None
    )
    assert ligne is not None, "base.html ne charge pas /static/rec_fetch.js"
    assert "defer" not in ligne and "async" not in ligne, (
        f"rec_fetch.js doit être chargé de façon bloquante : {ligne.strip()}"
    )


# --------------------------------------------------------------------------- #
# Exécution réelle du helper — la seule preuve qui vaille sur du JS.
# --------------------------------------------------------------------------- #

# Le harnais LIT LE CORPS (`res.json()`), comme le font les 16 appelants réels
# des deux écrans. C'est indispensable : `fetch` se règle à la réception des
# en-têtes, donc un harnais qui s'arrête là ne verrait jamais un corps qui
# n'arrive pas — le trou exact que la revue adversariale du 2026-08-31 a trouvé
# dans la première version de `recFetch`.
_HARNAIS = """
globalThis.window = globalThis;
%(stub)s
%(source)s
var t0 = Date.now();
var fini = false;
window.recFetch('http://exemple.invalide/x', { method: 'POST' }, %(delai)d)
  .then(function (res) {
    if (res && typeof res.json === 'function') {
      return res.json().then(function () { return 'RESOLUE'; });
    }
    return 'RESOLUE';
  })
  .then(function (r) { fini = true; console.log(r + ' ' + (Date.now() - t0)); },
        function (e) { fini = true; console.log('REJETEE ' + (Date.now() - t0)
          + ' recTimeout=' + (e.recTimeout === true) + ' ' + e.message); });
process.on('exit', function () { if (!fini) console.log('JAMAIS_REGLEE'); });
"""

# Bouchons FIDÈLES à la spec : un vrai `fetch` observe `options.signal` et rejette
# avec une `AbortError` quand il est déclenché. Un bouchon qui ignore le signal ne
# prouverait rien — il rendrait les tests verts même si `recFetch` oubliait de
# câbler le signal (vérifié : le harnais imprime alors JAMAIS_REGLEE).
_SUR_ABORT = (
    "function surAbort(opts, rejeter) {"
    "  if (opts && opts.signal) opts.signal.addEventListener('abort', function () {"
    "    var e = new Error('The operation was aborted'); e.name = 'AbortError'; rejeter(e);"
    "  });"
    "}"
)

# Le serveur ne répond RIEN : pas même les en-têtes.
_STUB_SANS_REPONSE = _SUR_ABORT + (
    "globalThis.fetch = function (url, opts) {"
    "  return new Promise(function (_, rej) { surAbort(opts, rej); });"
    "};"
)

# Les en-têtes arrivent, le CORPS jamais — socket qui meurt pendant le transfert,
# machine mise en veille. `fetch` se règle, `res.json()` reste en suspens.
_STUB_CORPS_ABSENT = _SUR_ABORT + (
    "globalThis.fetch = function (url, opts) {"
    "  return Promise.resolve({ ok: true, status: 200, json: function () {"
    "    return new Promise(function (_, rej) { surAbort(opts, rej); });"
    "  } });"
    "};"
)

# Réponse complète et immédiate.
_STUB_COMPLET = (
    "globalThis.fetch = function () {"
    "  return Promise.resolve({ ok: true, status: 200,"
    "    json: function () { return Promise.resolve({ path: 'ok.webm' }); } });"
    "};"
)


def _node(stub: str, delai: int) -> tuple[str, float]:
    if shutil.which("node") is None:
        # ÉCHEC, pas skip (contrat 0f8ca53 : « un skipped n'est pas un
        # passed ») : ces trois tests sont la SEULE preuve par exécution du
        # helper réseau — une suite « verte » qui les saute n'a rien vérifié
        # du comportement, seulement des présences de chaînes.
        pytest.fail(
            "node est requis pour exécuter réellement rec_fetch.js "
            "(https://nodejs.org). Un skip rendrait la suite verte sans la "
            "moindre preuve d'exécution du helper réseau."
        )
    script = _HARNAIS % {
        "stub": stub,
        "source": REC_FETCH.read_text(encoding="utf-8"),
        "delai": delai,
    }
    depart = time.monotonic()
    res = subprocess.run(
        ["node", "--input-type=commonjs", "-e", script],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert res.returncode == 0, res.stderr[:2000]
    return res.stdout.strip(), time.monotonic() - depart


def test_rec_fetch_rejette_quand_la_reponse_ne_vient_jamais() -> None:
    """Le serveur ne répond rien, pas même les en-têtes (socket à demi ouverte,
    pas de RST). Sans AbortController la promesse reste en suspens pour
    toujours — ici elle doit être rejetée, avec un message lisible par
    l'utilisateur (le reste de la chaîne n'affiche que `.message`)."""
    sortie, _ = _node(_STUB_SANS_REPONSE, 300)
    assert sortie.startswith("REJETEE"), f"attendu un rejet, obtenu : {sortie!r}"
    assert "pas de réponse après" in sortie, (
        f"le message d'AbortError du navigateur n'a pas été traduit : {sortie!r}"
    )
    # Étiquette structurée du délai (R3-M5/M6) : c'est sur elle que les écrans
    # décident de NE PAS relancer automatiquement les mêmes octets.
    assert "recTimeout=true" in sortie, (
        f"l'erreur de délai ne porte pas `recTimeout` : {sortie!r}"
    )


def test_rec_fetch_rejette_quand_le_CORPS_ne_vient_jamais() -> None:
    """Régression du 2026-08-31 (revue adversariale) : la 1re version annulait
    l'abort dès la réception des EN-TÊTES. Le corps n'était donc plus borné par
    rien, et un socket mourant pendant le transfert laissait `res.json()` en
    suspens — `pendingBackups` jamais décrémenté, « Enregistrer l'entretien »
    grisé pour la durée de vie de la page. Soit exactement le gel que ce helper
    existe pour supprimer, déplacé d'un cran.

    Mesuré avant correctif : `{"pendingBackups": 1, "settleCount": 0}`."""
    sortie, _ = _node(_STUB_CORPS_ABSENT, 300)
    assert sortie.startswith("REJETEE"), (
        f"le corps absent n'est pas borné — attendu un rejet, obtenu : {sortie!r}"
    )
    assert "pas de réponse après" in sortie, sortie
    assert "recTimeout=true" in sortie, (
        f"l'erreur de délai ne porte pas `recTimeout` : {sortie!r}"
    )


def test_rec_fetch_annule_son_minuteur_quand_le_corps_est_lu() -> None:
    """Une réponse complètement lue doit annuler l'abort programmé. Preuve par le
    temps de vie du processus : node ne rend la main que lorsque la boucle
    d'évènements est vide, donc un `setTimeout` de 20 s non annulé le
    retiendrait 20 s."""
    sortie, duree = _node(_STUB_COMPLET, 20000)
    assert sortie.startswith("RESOLUE"), f"attendu une résolution, obtenu : {sortie!r}"
    assert duree < 10, (
        f"le processus a vécu {duree:.1f}s avec un délai de 20s : le minuteur "
        "n'est pas annulé après la lecture du corps"
    )
