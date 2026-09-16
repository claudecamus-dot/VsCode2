"""`statutEstEchec` (record.html / record_libre.html) — EXÉCUTION RÉELLE.

Salles atelier-idées du 2026-09-16 (2e retour utilisateur : « quelque chose de
plus visible ») : `#rec-status` reçoit ~35 messages de nature très différente
(progression normale, relance en cours, échec terminal) tous rendus dans le
même gris `.muted` — trouvé par Sally dès la 1re salle, laissé de côté dans le
premier lot (scope trop large : toucher chacun des ~35 points d'écriture aurait
été un risque élevé sur un fichier au lourd historique de bugs data-loss).

Plutôt que de toucher les ~35 sites d'écriture, `statutEstEchec` est LE point
de classification, et un `MutationObserver` (câblage réel testé ici aussi,
cf. `test_le_cablage_mutationobserver_bascule_reellement_la_classe` — revue
bmad-code-review 2026-09-16 qui a réfuté l'idée que ce câblage ne serait pas
testable en Node : le bac à sable de ce dépôt fournit déjà des stubs DOM,
`MutationObserver` s'y ajoute pareil que `recFetch`/`setTimeout` ailleurs)
l'applique à chaque changement de `#rec-status`.

Aucun accès base : ce module ne touche ni `DB_PATH` ni `SessionLocal`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

try:  # `tests/` sur sys.path (mode d'import par défaut de pytest)
    from test_repartition_live import _fonction, _node, _sans_commentaires
except ImportError:  # `tests` importable comme paquet
    from tests.test_repartition_live import _fonction, _node, _sans_commentaires

RACINE = Path(__file__).resolve().parent.parent
LIBRE = RACINE / "app" / "templates" / "interviews" / "record_libre.html"
STRUCTURE = RACINE / "app" / "templates" / "interviews" / "record.html"
LES_DEUX = [LIBRE, STRUCTURE]

# Échantillon des VRAIS messages du fichier — 6 échecs terminaux (dont
# "Transcription interrompue au bloc N." trouvé manquant par la revue
# bmad-code-review 2026-09-16, finding 2 — le 1er jeu de motifs en oubliait
# un) + un échantillon large de messages neutres/en-cours, extraits en
# relisant les deux templates (pas inventés). Un message "nouvelle tentative"
# reste neutre : encore en cours, pas encore un échec.
_ECHECS_ATTENDUS = [
    "Enregistrement non supporté par ce navigateur.",
    "Relance impossible : erreur réseau",
    "Échec de la transcription du fichier.",
    "Rattachement impossible (timeout)",
    "Micro inaccessible : Permission denied",
    "Transcription interrompue au bloc 3.",
]
_NEUTRES_ATTENDUS = [
    "Enregistrement en cours…",
    "Enregistrement en cours — son de la réunion capté.",
    "Transcription en cours…",
    "Relance de 3 segments…",
    "Segment non transmis (timeout) — nouvelle tentative (2/3)…",
    "Transcription momentanément occupée — nouvelle tentative dans 5 s…",
    "Reprise de la transcription au bloc 4…",
    "Fichier transcrit (5 blocs)",
    "Un rattachement est déjà en cours — attends sa fin.",
    "Rattachement de « entretien.webm »…",
    "Fichier rattaché à l'entretien — il n'a PAS été transcrit.",
    "Transcription précédente conservée suite à une erreur — prêt à réessayer.",
    "Session restaurée — tu peux l'enregistrer telle quelle avec « Enregistrer l'entretien ».",
    "",
]

_SCRIPT_CLASSIFICATION = r"""
%(fonction)s
const echecs = %(echecs)s;
const neutres = %(neutres)s;
const out = {
  echecs: echecs.map(statutEstEchec),
  neutres: neutres.map(statutEstEchec),
};
console.log(JSON.stringify(out));
"""

# Bac à sable minimal pour le câblage MutationObserver -> classList : un stub
# `Element` (textContent + classList.toggle/contains, comme les stubs `el()`
# des fichiers frères de ce dossier) et un stub `MutationObserver` qui imite
# le comportement réel utile ici (callback rappelé au prochain tick après
# mutation, PAS synchrone — comme le vrai navigateur, cf. la spec DOM). Le
# bloc `%(cablage)s` collé plus bas est le VRAI code des templates
# (`var STATUT_ECHEC_MOTIFS ... .observe(statusEl, {...});`), pas une
# réécriture.
_SCRIPT_CABLAGE = r"""
function Element() {
  this._texte = '';
  this._classes = new Set();
  this._observateurs = [];
}
Object.defineProperty(Element.prototype, 'textContent', {
  get: function () { return this._texte; },
  set: function (v) {
    // `textContent = ...` REMPLACE les nœuds enfants (spec DOM) : seuls les
    // observateurs enregistrés avec `childList: true` sont notifiés. Sans
    // cette distinction, le stub ne prouverait rien sur l'option réellement
    // passée à `.observe()` — trouvé en preuve P1 (retirer `childList: true`
    // du vrai template laissait ce test vert avant cette correction).
    this._texte = v;
    this._observateurs.forEach(function (o) {
      if (o.options.childList) Promise.resolve().then(o.cb);
    });
  }
});
Object.defineProperty(Element.prototype, 'classList', {
  get: function () {
    var self = this;
    return {
      toggle: function (nom, on) { if (on) self._classes.add(nom); else self._classes.delete(nom); },
      contains: function (nom) { return self._classes.has(nom); }
    };
  }
});
function MutationObserver(cb) { this._cb = cb; }
MutationObserver.prototype.observe = function (cible, options) {
  cible._observateurs.push({ cb: this._cb, options: options || {} });
};

var statusEl = new Element();

%(cablage)s

async function tick() { for (var i = 0; i < 5; i++) await Promise.resolve(); }

(async () => {
  var out = {};
  statusEl.textContent = %(echec)s;
  await tick();
  out.classe_posee_sur_echec = statusEl.classList.contains('rec-status-danger');
  statusEl.textContent = %(neutre)s;
  await tick();
  out.classe_retiree_sur_neutre = statusEl.classList.contains('rec-status-danger');
  statusEl.textContent = '';
  await tick();
  out.classe_retiree_sur_vide = statusEl.classList.contains('rec-status-danger');
  console.log(JSON.stringify(out));
})();
"""


def _extraire_fonction(ecran: Path) -> str:
    contenu = _sans_commentaires(ecran.read_text(encoding="utf-8"))
    debut = contenu.index("var STATUT_ECHEC_MOTIFS")
    fin_liste = contenu.index(";", debut) + 1
    liste = contenu[debut:fin_liste]
    fonction = _fonction(contenu, "statutEstEchec")
    return liste + "\n" + fonction


def _extraire_cablage(ecran: Path) -> str:
    """Le VRAI bloc `var STATUT_ECHEC_MOTIFS ... .observe(statusEl, {...});`
    tel qu'écrit dans le template — pas juste la fonction pure, le câblage
    complet (revue bmad-code-review 2026-09-16, finding 3 : la fonction seule
    ne prouve pas que l'observateur est branché ni sur le bon nom de classe)."""
    contenu = _sans_commentaires(ecran.read_text(encoding="utf-8"))
    debut = contenu.index("var STATUT_ECHEC_MOTIFS")
    fin = contenu.index(".observe(statusEl,", debut)
    fin = contenu.index(";", fin) + 1
    return contenu[debut:fin]


def _executer_classification(ecran: Path) -> dict:
    script = _SCRIPT_CLASSIFICATION % {
        "fonction": _extraire_fonction(ecran),
        "echecs": json.dumps(_ECHECS_ATTENDUS),
        "neutres": json.dumps(_NEUTRES_ATTENDUS),
    }
    return _node(script)


@pytest.fixture(scope="module", params=LES_DEUX, ids=lambda p: p.name)
def classification(request) -> dict:
    return _executer_classification(request.param)


def test_les_echecs_terminaux_connus_sont_classes_echec(classification: dict) -> None:
    assert classification["echecs"] == [True] * len(_ECHECS_ATTENDUS), (
        f"un des messages d'échec terminal réels du fichier n'est plus "
        f"classé comme échec : {list(zip(_ECHECS_ATTENDUS, classification['echecs']))}"
    )


def test_les_messages_neutres_et_en_cours_ne_sont_pas_classes_echec(classification: dict) -> None:
    faux_positifs = [
        m for m, e in zip(_NEUTRES_ATTENDUS, classification["neutres"]) if e
    ]
    assert not faux_positifs, (
        "un message neutre/en-cours réel du fichier est classé à tort comme "
        f"échec (afficherait le statut en rouge sans raison) : {faux_positifs}"
    )


@pytest.mark.parametrize("ecran", LES_DEUX, ids=lambda p: p.name)
def test_les_vrais_messages_d_echec_du_fichier_sont_couverts(ecran: Path) -> None:
    """Garde-fou contre la dérive : si un message d'échec terminal est ajouté
    au fichier sans mettre à jour `STATUT_ECHEC_MOTIFS`, ce test ne le détecte
    pas automatiquement (il ne connaît que le vocabulaire figé ci-dessus) —
    mais il vérifie au moins que les messages RÉELS actuels du fichier
    matchent bien un motif, pas une chaîne inventée.

    Revue bmad-code-review 2026-09-16, finding 1 : la 1re version de ce test
    cherchait le motif dans TOUT le fichier — y compris dans la déclaration de
    `STATUT_ECHEC_MOTIFS` elle-même, qui contient forcément chaque motif :
    le test passait donc TOUJOURS, quel que soit l'état réel du fichier
    (tautologie prouvée en renommant un message réel sans que la suite ne
    bouge). Corrigé : le motif est cherché dans le fichier PRIVÉ de la
    déclaration du tableau."""
    contenu = _sans_commentaires(ecran.read_text(encoding="utf-8"))
    debut = contenu.index("var STATUT_ECHEC_MOTIFS")
    fin_liste = contenu.index(";", debut) + 1
    contenu_sans_la_liste = contenu[:debut] + contenu[fin_liste:]
    for motif in (
        "non supporté par ce navigateur", "Relance impossible :",
        "Échec de la transcription", "Rattachement impossible", "Micro inaccessible",
        "Transcription interrompue au bloc",
    ):
        assert motif in contenu_sans_la_liste, (
            f"{ecran.name} : le motif {motif!r} de STATUT_ECHEC_MOTIFS ne "
            "correspond plus à aucun message réel du fichier (hors la "
            "déclaration du tableau lui-même)"
        )


@pytest.mark.parametrize("ecran", LES_DEUX, ids=lambda p: p.name)
def test_le_cablage_mutationobserver_bascule_reellement_la_classe(ecran: Path) -> None:
    """Revue bmad-code-review 2026-09-16, finding 3 : `statutEstEchec` seule
    ne prouve pas que le `MutationObserver` est branché sur le bon élément,
    avec les bonnes options (`childList`, sans quoi un remplacement de
    `textContent` ne déclenche jamais rien), ni que la classe pointée
    (`'rec-status-danger'`) correspond à celle déclarée dans `app.css`. Ce
    test EXÉCUTE le vrai bloc de câblage du template, pas une réécriture."""
    script = _SCRIPT_CABLAGE % {
        "cablage": _extraire_cablage(ecran),
        "echec": json.dumps("Micro inaccessible : Permission denied"),
        "neutre": json.dumps("Enregistrement en cours…"),
    }
    out = _node(script)
    assert out["classe_posee_sur_echec"] is True, (
        f"{ecran.name} : un vrai message d'échec ne pose plus "
        "`rec-status-danger` sur `#rec-status`"
    )
    assert out["classe_retiree_sur_neutre"] is False, (
        f"{ecran.name} : la classe reste posée après un message neutre — "
        "le statut resterait rouge indéfiniment après une relance réussie"
    )
    assert out["classe_retiree_sur_vide"] is False, (
        f"{ecran.name} : la classe reste posée quand `#rec-status` est vidé"
    )


def test_rec_status_danger_utilise_le_token_danger_existant() -> None:
    """La classe posée par le MutationObserver doit exister dans app.css et
    utiliser `var(--danger)` — pas une couleur inventée (contrat des 2 salles
    atelier-idées : redessiner sur les tokens EXISTANTS)."""
    css = (RACINE / "app" / "static" / "app.css").read_text(encoding="utf-8")
    m = re.search(r"\.rec-status-danger\s*\{([^}]*)\}", css)
    assert m, "la classe .rec-status-danger n'existe plus dans app.css"
    assert "var(--danger)" in m.group(1), (
        ".rec-status-danger doit utiliser var(--danger), pas une couleur en dur"
    )
