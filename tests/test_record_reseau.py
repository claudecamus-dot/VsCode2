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
ECRANS = [
    RACINE / "app" / "templates" / "interviews" / "record_libre.html",
    RACINE / "app" / "templates" / "interviews" / "record.html",
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
    # `(?<![A-Za-z0-9_.])` écarte `recFetch(` (et tout `x.fetch(`) ; exiger un
    # littéral de chaîne en 1er argument écarte les mentions en commentaire, qui
    # écrivent `fetch()` sans argument. La première version testait
    # `"recFetch(" not in l`, une clause à la fois MORTE (« fetch( » n'est pas
    # sous-chaîne de « recFetch( ») et NUISIBLE : elle exemptait toute ligne
    # portant à la fois un recFetch légitime et un fetch nu — précisément la
    # ligne où le bug reviendrait (revue adversariale 2026-08-31).
    appel_nu = re.compile(r"(?<![A-Za-z0-9_.])fetch\(\s*['\"]")
    lignes = [
        (i, l.strip())
        for i, l in enumerate(ecran.read_text(encoding="utf-8").splitlines(), 1)
        if appel_nu.search(l)
    ]
    assert not lignes, (
        f"{ecran.name} : {len(lignes)} appel(s) `fetch(` nu(s) — passer par "
        f"`recFetch(url, options, delai)` : {lignes[:3]}"
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
        function (e) { fini = true; console.log('REJETEE ' + (Date.now() - t0) + ' ' + e.message); });
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
        pytest.skip("node absent de ce poste")
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
