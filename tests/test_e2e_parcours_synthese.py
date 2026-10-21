"""Parcours de SYNTHÈSE et de fiche, côté consultant (comité e2e du 2026-10-14,
dernière section du triage) : sept parcours que ni `test_e2e_premiers_clics.py`
ni `test_e2e_parcours_utilisateur.py` ne rejouaient dans un vrai navigateur.

1. exports PDF : GET de la fiche, de la transcription, et les POST des écrans
   de retranscription (tours, transcription) — le fichier reçu PAR LE NAVIGATEUR ;
2. export Markdown de la fiche (route `/interviews/{id}/export/markdown`) ;
3. résumé exécutif, difficultés, verbatims : édition d'un champ + persistance ;
4. SWOT et synthèse globale nominaux : édition d'un quadrant / d'un axe ;
5. axes d'étude : créer, éditer, supprimer ;
6. mission : renommer, supprimer ;
7. retranscription : appliquer, confirmer, abandonner (job SEMÉ à l'état « done »,
   aucun job long ni Ollama).

Non atteint (dit, pas caché) : `/interviews/synthese/export-pdf` — son bouton
(`libre_review.html`) n'est servi qu'après une génération IA réussie du wizard
libre ; Ollama est coupé (OLLAMA-COUPE-TEMPORAIRE), l'écran est inatteignable
par un clic. Les trois `…/generate` en échec lisible sont déjà couverts ailleurs.

Règles tenues : ce que l'utilisateur VOIT (innerText), clics souris réels,
polling plutôt que `sleep` fixe, une mission semée par test, persistance relue
en base ET après rechargement. Même garde que les voisins : sans navigateur
Chromium le module est sauté (`E2E_OBLIGATOIRE=1` : erreur).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

_OBLIGATOIRE = bool(os.environ.get("E2E_OBLIGATOIRE"))
try:
    import websockets  # noqa: F401 — fourni par uvicorn[standard]
except ModuleNotFoundError:
    if _OBLIGATOIRE:
        raise RuntimeError(
            "E2E_OBLIGATOIRE est posé mais `websockets` (uvicorn[standard]) est absent"
        ) from None
    pytest.skip("websockets (uvicorn[standard]) requis", allow_module_level=True)

from e2e_serveur import serveur_uvicorn  # noqa: E402
from navigateur_cdp import Navigateur, trouver_navigateur  # noqa: E402

_NAVIGATEUR = trouver_navigateur()

if _NAVIGATEUR is None and _OBLIGATOIRE:
    raise RuntimeError(
        "E2E_OBLIGATOIRE est posé mais aucun navigateur Chromium (Edge/Chrome) n'est trouvé"
    )

pytestmark = pytest.mark.skipif(
    _NAVIGATEUR is None,
    reason="aucun navigateur Chromium (Edge/Chrome) trouvé — E2E_NAVIGATEUR pour en forcer un",
)

_RACINE = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Plomberie locale (les modules e2e voisins ne sont pas importables)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def serveur(tmp_path_factory: pytest.TempPathFactory):
    dossier = tmp_path_factory.mktemp("e2e-synthese")
    with serveur_uvicorn(dossier, OLLAMA_HOST="http://127.0.0.1:9") as base:
        yield base, dossier / "e2e.db"


@pytest.fixture
def nav():
    profil = Path(tempfile.mkdtemp(prefix="e2e-nav-"))
    navigateur = None
    try:
        navigateur = Navigateur(_NAVIGATEUR, profil / "profil")
        yield navigateur
    finally:
        if navigateur is not None:
            navigateur.fermer()
        shutil.rmtree(profil, ignore_errors=True)


def _python_sur_base(base_db: Path, code: str, *args: str) -> str:
    env = dict(os.environ, APP_DB_PATH=str(base_db), PYTHONUTF8="1")
    res = subprocess.run([sys.executable, "-c", code, *args], cwd=_RACINE, env=env,
                         capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return res.stdout.strip().splitlines()[-1]


def _lire(base_db: Path, code: str, *args: str):
    """Relit la base par un sous-processus ; `code` imprime UNE ligne JSON."""
    return json.loads(_python_sur_base(base_db, code, *args))


def _sans_erreur(nav: Navigateur, etape: str, *, sauf: tuple[str, ...] = ()) -> None:
    """`sauf` : sous-chaînes d'URL dont l'erreur est ATTENDUE (ici l'audio semé
    sans fichier sur disque)."""
    nav.drainer()
    inattendues = [r for r in nav.erreurs() if not any(s in str(r.get("url", "")) for s in sauf)]
    assert not inattendues, f"{etape} : réponses en erreur {inattendues}"
    assert not nav.exceptions, f"{etape} : exceptions JS {nav.exceptions}"
    assert not nav.erreurs_cdp, f"{etape} : erreur CDP {nav.erreurs_cdp}"
    assert "Origine non autorisée" not in nav.texte(), f"{etape} : 403 CSRF rendu à l'écran"


def _attendre(condition, message: str, delai_s: float = 10.0, pas_s: float = 0.2):
    fin = time.monotonic() + delai_s
    while time.monotonic() < fin:
        valeur = condition()
        if valeur:
            return valeur
        time.sleep(pas_s)
    raise AssertionError(message)


def _attendre_visible(nav: Navigateur, selecteur: str, attendu: str, delai_s: float = 10.0) -> None:
    """`attendu` dans le texte VISIBLE (innerText) de l'élément, insensible à la casse."""
    _attendre(
        lambda: nav.evaluer(
            "(function(){var e=document.querySelector(" + json.dumps(selecteur) + ");"
            " return !!(e && e.innerText.toLowerCase().indexOf(" + json.dumps(attendu.lower()) + ")>=0);})()"
        ),
        f"« {attendu} » jamais visible dans {selecteur}", delai_s,
    )


def _attendre_cliquable(nav: Navigateur, selecteur: str, delai_s: float = 15.0) -> None:
    """Attend qu'un bouton soit affiché (taille non nulle) — l'écran de suivi
    de retranscription le révèle quand le poll rend « done »."""
    _attendre(
        lambda: nav.evaluer(
            "(function(){var e=document.querySelector(" + json.dumps(selecteur) + ");"
            " if(!e) return false; var r=e.getBoundingClientRect();"
            " return r.width>0 && r.height>0;})()"),
        f"{selecteur} jamais affiché", delai_s,
    )


def _saisir_au_clavier(nav: Navigateur, selecteur: str, valeur: str) -> None:
    """Clic réel dans le champ (focus), puis saisie terminée par `keyup` : les
    autosaves de l'aperçu écoutent `keyup changed delay:700ms, blur` SANS `change`."""
    nav.cliquer(selecteur)
    nav.remplir(selecteur, valeur)
    ok = nav.evaluer(
        "(function(){var e=document.querySelector(" + json.dumps(selecteur) + ");"
        " if(!e) return false;"
        " e.dispatchEvent(new KeyboardEvent('keyup',{bubbles:true,key:'a'}));"
        " return true;})()"
    )
    assert ok, f"champ introuvable : {selecteur}"


def _valeur(nav: Navigateur, selecteur: str) -> str:
    return nav.evaluer(
        "(function(){var e=document.querySelector(" + json.dumps(selecteur) + ");"
        " return e ? e.value : null;})()")


def _telecharger(nav: Navigateur, selecteur: str, dossier: Path, motif: str) -> Path:
    """Clic réel sur `selecteur` ; rend le fichier REÇU par le navigateur
    (`motif` : glob du nom), une fois sa taille stabilisée."""
    dossier.mkdir(exist_ok=True)
    nav.autoriser_telechargements(dossier)
    nav.cliquer(selecteur)
    fichier = _attendre(
        lambda: next((p for p in dossier.glob(motif)
                      if not p.with_name(p.name + ".crdownload").exists()), None),
        f"aucun fichier {motif} reçu dans {dossier} : {list(dossier.iterdir())}",
        delai_s=60.0,
    )
    tailles: list[int] = []

    def _stable() -> bool:
        tailles.append(fichier.stat().st_size)
        return len(tailles) >= 2 and tailles[-1] == tailles[-2] > 0

    _attendre(_stable, f"{fichier.name} jamais stabilisé", pas_s=0.3)
    return fichier


def _cliquer_onglet(nav: Navigateur, nom: str) -> None:
    nav.cliquer(f".tab[data-tab={json.dumps(nom)}]")


# --------------------------------------------------------------------------- #
# Semis
# --------------------------------------------------------------------------- #
_SEED_SYNTHESE = r"""
import json
from app.db import SessionLocal, init_db
from app.models import (GlobalSynthesis, Interview, Mission, MissionDifficulty,
                        MissionExecutiveSummary, MissionSwot, Question, Theme,
                        Trame, Verbatim)
init_db()
db = SessionLocal()
m = Mission(name="E2E synthèse")
db.add(m); db.flush()
tr = Trame(mission_id=m.id); db.add(tr); db.flush()
th = Theme(trame_id=tr.id, title="Gouvernance", position=0); db.add(th); db.flush()
q = Question(theme_id=th.id, label="Comment décidez-vous ?", position=0)
db.add(q); db.flush()
i = Interview(mission_id=m.id, interviewee_name="Témoin synthèse", status="done")
db.add(i); db.flush()
v1 = Verbatim(interview_id=i.id, question_id=q.id, quote="Les arbitrages se font au couloir")
v2 = Verbatim(interview_id=i.id, question_id=q.id, quote="Personne ne ferme les sujets")
db.add_all([v1, v2]); db.flush()
db.add(GlobalSynthesis(mission_id=m.id, status="generated",
                       contexte="Un contexte de départ.",
                       points_amelioration="- Silos entre équipes"))
db.add(MissionSwot(mission_id=m.id, status="generated", forces="- Équipe soudée",
                   faiblesses="- Outils dispersés", opportunites="- Marché porteur",
                   menaces="- Départs en retraite"))
db.add(MissionExecutiveSummary(mission_id=m.id, status="generated",
                               headline="Constat de départ", points="- Point un",
                               key_message="Message de départ"))
d = MissionDifficulty(mission_id=m.id, position=0, label="Difficulté de départ")
db.add(d); db.commit()
print(json.dumps({"mission": m.id, "interview": i.id, "difficulte": d.id,
                  "v1": v1.id, "v2": v2.id}))
"""

_LIRE_SYNTHESE = r"""
import json, sys
from app.db import SessionLocal
from app.models import (GlobalSynthesis, Mission, MissionDifficulty,
                        MissionExecutiveSummary, MissionSwot)
db = SessionLocal()
mid = int(sys.argv[1])
m = db.get(Mission, mid)
es = db.query(MissionExecutiveSummary).filter_by(mission_id=mid).one()
sw = db.query(MissionSwot).filter_by(mission_id=mid).one()
gs = db.query(GlobalSynthesis).filter_by(mission_id=mid).one()
d = db.query(MissionDifficulty).filter_by(mission_id=mid).one()
print(json.dumps({
    "headline": es.headline, "points": es.points, "key_message": es.key_message,
    "es_status": es.status,
    "forces": sw.forces, "menaces": sw.menaces, "swot_status": sw.status,
    "contexte": gs.contexte, "valeurs": gs.valeurs, "gs_status": gs.status,
    "diff_label": d.label, "diff_verbatim": d.verbatim_id, "diff_edite": d.edite,
    "verbatims": m.restitution_verbatim_ids,
}))
"""


def _semer_synthese(base_db: Path) -> dict:
    return _lire(base_db, _SEED_SYNTHESE)


def _lire_synthese(base_db: Path, mission_id: int) -> dict:
    return _lire(base_db, _LIRE_SYNTHESE, str(mission_id))


_SEED_FICHE = r"""
import json
from app.db import SessionLocal, init_db
from app.models import Interview, InterviewTurn, Mission
init_db()
db = SessionLocal()
m = Mission(name="E2E fiche")
db.add(m); db.flush()
i = Interview(mission_id=m.id, mode="libre", interviewee_name="Alice Fiche", status="done",
              raw_transcript="Transcription brute alpha : nous décidons en comité.",
              resume="Un résumé de fiche.")
db.add(i); db.flush()
db.add(InterviewTurn(interview_id=i.id, position=0, interlocuteur="Alice Fiche",
                     question="Qui décide ?", remarque="Le comité tranche toujours.",
                     section_title=None))
db.commit()
print(json.dumps({"mission": m.id, "interview": i.id}))
"""


# --------------------------------------------------------------------------- #
# 1-2. Exports de la fiche : Markdown et PDF
# --------------------------------------------------------------------------- #
def test_telecharger_la_fiche_en_markdown(serveur, nav: Navigateur, tmp_path) -> None:
    """Fiche d'entretien -> clic « Télécharger (.md) » -> le NAVIGATEUR reçoit un
    .md nommé d'après l'interlocuteur et portant ses propos."""
    base, base_db = serveur
    ids = _lire(base_db, _SEED_FICHE)
    nav.naviguer(f"{base}/interviews/{ids['interview']}")
    _sans_erreur(nav, "Ouvrir la fiche")
    fichier = _telecharger(
        nav, "a.btn[href$='/export/markdown']", tmp_path / "dl", "*.md")
    assert fichier.name == "entretien_alice_fiche.md", fichier.name
    texte = fichier.read_text(encoding="utf-8")
    assert "Alice Fiche" in texte and "Le comité tranche toujours." in texte, texte[:400]
    _sans_erreur(nav, "Télécharger le .md")


def _texte_pdf(chemin: Path) -> str:
    import fitz  # PyMuPDF

    with fitz.open(str(chemin)) as doc:
        return " ".join(" ".join(page.get_text().split()) for page in doc)


def test_telecharger_la_fiche_et_la_transcription_en_pdf(
    serveur, nav: Navigateur, tmp_path
) -> None:
    """Deux clics réels : « Télécharger (PDF) » (fiche) puis « Télécharger la
    transcription (PDF) » — deux fichiers distincts, octets %PDF, taille > 0."""
    base, base_db = serveur
    ids = _lire(base_db, _SEED_FICHE)
    nav.naviguer(f"{base}/interviews/{ids['interview']}")
    _sans_erreur(nav, "Ouvrir la fiche")
    fiche = _telecharger(nav, "a.btn[href$='/export/pdf']", tmp_path / "fiche", "*.pdf")
    assert fiche.name == "entretien_alice_fiche.pdf", fiche.name
    assert fiche.read_bytes()[:5] == b"%PDF-" and fiche.stat().st_size > 500, fiche.stat().st_size
    # Le PDF de la fiche est LE vrai (build_interview_pdf), pas le repli de secours
    # de la route (qui rend %PDF- + du texte aussi) : son sous-titre « Export de
    # secours » trahirait un build_interview_pdf cassé.
    texte_fiche = _texte_pdf(fiche)
    assert "Le comité tranche toujours." in texte_fiche, texte_fiche[:400]
    assert "Export de secours" not in texte_fiche, "repli de secours servi : build_interview_pdf casse"
    transcription = _telecharger(
        nav, "a[href$='/export/transcription/pdf']", tmp_path / "transcription", "*.pdf")
    assert transcription.name == "transcription_alice_fiche.pdf", transcription.name
    assert transcription.read_bytes()[:5] == b"%PDF-" and transcription.stat().st_size > 500
    texte_transcription = _texte_pdf(transcription)
    assert "Transcription brute alpha" in texte_transcription, texte_transcription[:400]
    assert "avant structuration par l" in texte_transcription  # sous-titre de la route transcription
    _sans_erreur(nav, "Télécharger les PDF de la fiche")


# --------------------------------------------------------------------------- #
# 3. Résumé exécutif, difficultés, verbatims
# --------------------------------------------------------------------------- #
def test_editer_le_resume_executif_et_le_retrouver(serveur, nav: Navigateur) -> None:
    base, base_db = serveur
    ids = _semer_synthese(base_db)
    url = f"{base}/missions/{ids['mission']}/synthese/apercu"
    nav.naviguer(url)
    _sans_erreur(nav, "Ouvrir l'aperçu")
    _cliquer_onglet(nav, "executive-summary")
    champ = "[data-panel=executive-summary] textarea[hx-vals*=headline]"
    _saisir_au_clavier(nav, champ, "Le pilotage repose sur trois personnes clés")
    _attendre_visible(nav, "#es-saved-headline", "enregistré")
    assert _lire_synthese(base_db, ids["mission"])["headline"] == \
        "Le pilotage repose sur trois personnes clés"
    # La bande « key message » s'édite aussi, et la ligne passe en « édité ».
    _saisir_au_clavier(nav, "[data-panel=executive-summary] textarea[hx-vals*=key_message]",
                       "Sécuriser la connaissance avant les départs")
    _attendre_visible(nav, "#es-saved-key_message", "enregistré")
    en_base = _lire_synthese(base_db, ids["mission"])
    assert en_base["key_message"] == "Sécuriser la connaissance avant les départs"
    assert en_base["es_status"] == "edited", en_base["es_status"]
    # Rechargement : l'utilisateur retrouve SON texte, dans le champ et l'aperçu.
    nav.naviguer(url)
    _cliquer_onglet(nav, "executive-summary")
    assert _valeur(nav, champ) == "Le pilotage repose sur trois personnes clés"
    assert "Sécuriser la connaissance avant les départs" in nav.texte()
    _sans_erreur(nav, "Éditer le résumé exécutif")


def test_editer_une_difficulte_et_lui_lier_un_verbatim(serveur, nav: Navigateur) -> None:
    base, base_db = serveur
    ids = _semer_synthese(base_db)
    url = f"{base}/missions/{ids['mission']}/synthese/apercu"
    nav.naviguer(url)
    _cliquer_onglet(nav, "difficultes")
    champ = "[data-panel=difficultes] textarea.difficulty-label"
    _saisir_au_clavier(nav, champ, "Décisions sans propriétaire identifié")
    _attendre_visible(nav, f"#diff-saved-{ids['difficulte']}", "enregistré")
    # Choix de la citation dans la liste déroulante DE CETTE difficulté : touches
    # clavier réelles (le <select> natif ne se déroule pas en headless), le
    # navigateur émet lui-même `change`.
    liste = f"select[hx-post$='/difficultes/{ids['difficulte']}/verbatim']"
    nav.choisir_option(liste, str(ids["v2"]))
    _attendre_visible(nav, f"#diff-saved-{ids['difficulte']}", "verbatim lié")
    en_base = _lire_synthese(base_db, ids["mission"])
    assert en_base["diff_label"] == "Décisions sans propriétaire identifié"
    assert en_base["diff_verbatim"] == ids["v2"] and en_base["diff_edite"] is True
    nav.naviguer(url)
    _cliquer_onglet(nav, "difficultes")
    assert _valeur(nav, champ) == "Décisions sans propriétaire identifié"
    assert "Personne ne ferme les sujets" in nav.texte(), "citation absente de l'aperçu"
    _sans_erreur(nav, "Éditer une difficulté")


def test_retenir_des_verbatims_pour_la_planche(serveur, nav: Navigateur) -> None:
    base, base_db = serveur
    ids = _semer_synthese(base_db)
    url = f"{base}/missions/{ids['mission']}/synthese/apercu"
    nav.naviguer(url)
    _cliquer_onglet(nav, "verbatims")
    _attendre_visible(nav, "#verbatims-count", "0 verbatim retenu")
    case = "input[hx-post$='/verbatims/toggle'][hx-vals*='\"verbatim_id\": %d']"
    nav.cliquer(case % ids["v1"])
    _attendre_visible(nav, "#verbatims-count", "1 verbatim retenu pour la slide")
    nav.cliquer(case % ids["v2"])
    _attendre_visible(nav, "#verbatims-count", "2 verbatims retenus pour la slide")
    assert _lire_synthese(base_db, ids["mission"])["verbatims"] == [ids["v1"], ids["v2"]]
    nav.cliquer(case % ids["v1"])  # on en retire un
    _attendre_visible(nav, "#verbatims-count", "1 verbatim retenu pour la slide")
    assert _lire_synthese(base_db, ids["mission"])["verbatims"] == [ids["v2"]]
    nav.naviguer(url)
    _cliquer_onglet(nav, "verbatims")
    assert nav.evaluer(
        "document.querySelector(" + json.dumps(case % ids["v2"]) + ").checked") is True
    assert nav.evaluer(
        "document.querySelector(" + json.dumps(case % ids["v1"]) + ").checked") is False
    _sans_erreur(nav, "Retenir des verbatims")


# --------------------------------------------------------------------------- #
# 4. SWOT et synthèse globale (nominaux)
# --------------------------------------------------------------------------- #
def test_editer_le_swot_et_la_synthese_globale(serveur, nav: Navigateur) -> None:
    base, base_db = serveur
    ids = _semer_synthese(base_db)
    url = f"{base}/missions/{ids['mission']}/synthese/apercu"
    nav.naviguer(url)
    _sans_erreur(nav, "Ouvrir l'aperçu")
    _cliquer_onglet(nav, "swot")
    menaces = "[data-panel=swot] textarea[hx-vals*=menaces]"
    _saisir_au_clavier(nav, menaces, "- Concurrent qui recrute nos experts")
    _attendre_visible(nav, "#swot-saved-menaces", "enregistré")
    _cliquer_onglet(nav, "synth-contexte")
    contexte = "[data-panel=synth-contexte] textarea"
    _saisir_au_clavier(nav, contexte, "- Fusion de deux directions en 2024")
    _attendre_visible(nav, "#global-saved-contexte", "enregistré")
    en_base = _lire_synthese(base_db, ids["mission"])
    assert en_base["menaces"] == "- Concurrent qui recrute nos experts"
    assert en_base["forces"] == "- Équipe soudée", "un autre quadrant a été écrasé"
    assert en_base["swot_status"] == "edited" and en_base["gs_status"] == "edited"
    # Source de vérité = `valeurs` ; la colonne historique en est le miroir (models.py).
    assert en_base["valeurs"].get("contexte") == "- Fusion de deux directions en 2024", en_base
    assert en_base["contexte"] == "- Fusion de deux directions en 2024", "miroir non tenu"
    nav.naviguer(url)
    _cliquer_onglet(nav, "swot")
    assert _valeur(nav, menaces) == "- Concurrent qui recrute nos experts"
    assert "Concurrent qui recrute nos experts" in nav.texte()
    _cliquer_onglet(nav, "synth-contexte")
    assert _valeur(nav, contexte) == "- Fusion de deux directions en 2024"
    _sans_erreur(nav, "Éditer SWOT et synthèse globale")


# --------------------------------------------------------------------------- #
# 5. Axes d'étude : créer, éditer, supprimer
# --------------------------------------------------------------------------- #
_SEED_MISSION = r"""
import json
from app.db import SessionLocal, init_db
from app.models import Interview, Mission
init_db()
db = SessionLocal()
m = Mission(name="%s")
db.add(m); db.flush()
# Sans entretien, l'écran « Analyse » ne rend que « Aucun entretien ».
db.add(Interview(mission_id=m.id, interviewee_name="Témoin axes", status="done"))
db.commit()
print(json.dumps({"mission": m.id}))
"""

_LIRE_AXES = r"""
import json, sys
from app.db import SessionLocal
from app.models import MissionSynthesisAxis
db = SessionLocal()
axes = db.query(MissionSynthesisAxis).filter_by(mission_id=int(sys.argv[1])).all()
print(json.dumps([{"id": a.id, "key": a.key, "label": a.label, "hint": a.hint} for a in axes]))
"""


def test_axes_creer_editer_supprimer(serveur, nav: Navigateur) -> None:
    base, base_db = serveur
    mid = _lire(base_db, _SEED_MISSION % "E2E axes")["mission"]
    url = f"{base}/missions/{mid}/synthese/export-import"
    nav.naviguer(url)
    _cliquer_onglet(nav, "axes-etude")
    avant = _lire(base_db, _LIRE_AXES, str(mid))
    assert len(avant) == 5, avant  # les 5 axes par défaut sont semés à la lecture
    # --- créer
    nav.cliquer("form.axe-etude-new input[name=label]")
    nav.remplir("form.axe-etude-new input[name=label]", "Outillage et données")
    nav.remplir("form.axe-etude-new input[name=hint]", "ce qui manque côté outils")
    nav.cliquer_et_attendre("form.axe-etude-new button[type=submit]")
    _cliquer_onglet(nav, "axes-etude")
    assert "Outillage et données" in nav.evaluer(
        "Array.from(document.querySelectorAll('.axes-etude input[name=label]'))"
        ".map(function(i){return i.value;}).join('|')")
    apres = _lire(base_db, _LIRE_AXES, str(mid))
    nouveau = [a for a in apres if a["label"] == "Outillage et données"]
    assert len(apres) == 6 and len(nouveau) == 1 and nouveau[0]["hint"] == "ce qui manque côté outils"
    axe = nouveau[0]
    # --- éditer (renommer) : la clé, adresse du contenu, ne bouge pas
    form = f"form[action='/missions/{mid}/axes/{axe['id']}']"
    nav.cliquer(f"{form} input[name=label]")
    nav.remplir(f"{form} input[name=label]", "Outils et données métier")
    nav.cliquer_et_attendre(f"{form} button[type=submit]")
    _cliquer_onglet(nav, "axes-etude")
    assert _valeur(nav, f"{form} input[name=label]") == "Outils et données métier"
    renomme = next(a for a in _lire(base_db, _LIRE_AXES, str(mid)) if a["id"] == axe["id"])
    assert renomme["label"] == "Outils et données métier" and renomme["key"] == axe["key"]
    # --- supprimer (confirm() accepté par le harnais)
    nav.cliquer_et_attendre(
        f"form[action='/missions/{mid}/axes/{axe['id']}/delete'] button[type=submit]")
    _cliquer_onglet(nav, "axes-etude")
    assert nav.evaluer(
        "!document.querySelector(" + json.dumps(f"form[action='/missions/{mid}/axes/{axe['id']}']")
        + ")"), "l'axe supprimé est encore affiché"
    assert "Outils et données métier" not in nav.evaluer(
        "Array.from(document.querySelectorAll('.axes-etude input[name=label]'))"
        ".map(function(i){return i.value;}).join('|')")
    final = _lire(base_db, _LIRE_AXES, str(mid))
    assert len(final) == 5 and axe["id"] not in [a["id"] for a in final]
    _sans_erreur(nav, "Axes : créer, éditer, supprimer")


# --------------------------------------------------------------------------- #
# 6. Mission : renommer, supprimer
# --------------------------------------------------------------------------- #
_LIRE_MISSION = r"""
import json, sys
from app.db import SessionLocal
from app.models import Mission
m = SessionLocal().get(Mission, int(sys.argv[1]))
print(json.dumps(m.name if m else None))
"""


def test_renommer_puis_supprimer_la_mission(serveur, nav: Navigateur) -> None:
    base, base_db = serveur
    mid = _lire(base_db, _SEED_MISSION % "E2E à renommer")["mission"]
    url = f"{base}/missions/{mid}"
    nav.naviguer(url)
    _sans_erreur(nav, "Ouvrir la mission")
    champ = "input.mission-name-input"
    nav.cliquer(champ)
    nav.remplir(champ, "Transformation Orion")
    _attendre_visible(nav, "#mission-name-saved", "enregistré")
    assert _lire(base_db, _LIRE_MISSION, str(mid)) == "Transformation Orion"
    nav.naviguer(url)
    assert _valeur(nav, champ) == "Transformation Orion"
    # Liste des missions : le nouveau nom, plus l'ancien.
    nav.naviguer(f"{base}/missions")
    assert "Transformation Orion" in nav.texte() and "E2E à renommer" not in nav.texte()
    # Suppression directe (confirm() accepté) -> retour à la liste, mission partie.
    nav.naviguer(url)
    nav.cliquer_et_attendre(f"form[action='/missions/{mid}/delete'] button[type=submit]")
    assert nav.url().rstrip("/").endswith("/missions"), nav.url()
    assert "Transformation Orion" not in nav.texte(), "la mission supprimée est encore listée"
    assert _lire(base_db, _LIRE_MISSION, str(mid)) is None
    _sans_erreur(nav, "Renommer puis supprimer la mission")


# --------------------------------------------------------------------------- #
# 7. Retranscription : appliquer, confirmer, abandonner
# --------------------------------------------------------------------------- #
# Le job est SEMÉ à l'état « done » (blocs transcrits + tranche d'extraction
# aboutie) : aucun job long ni Ollama. Les fichiers audio n'ont pas besoin
# d'exister (les routes testées ne lisent que les NOMS des tranches).
_SEED_RETRANSCRIPTION = r"""
import json, sys, uuid
from app.db import SessionLocal, init_db
from app.models import (AudioFileJob, Interview, InterviewSegmentJob,
                        InterviewTurn, Mission)
avec_tours = sys.argv[1] == "tours"
init_db()
db = SessionLocal()
m = Mission(name="E2E retranscription")
db.add(m); db.flush()
nom = f"{m.id}_tranche0.webm"
i = Interview(mission_id=m.id, mode="libre", interviewee_name="Bruno Retranscrit",
              status="done", raw_transcript="Ancienne transcription gamma.",
              audio_segments=[{"filename": nom, "position": 0}], audio_backup_path=nom)
db.add(i); db.flush()
db.add(InterviewTurn(interview_id=i.id, position=0, interlocuteur="Bruno Retranscrit",
                     question=None, remarque="Ancien propos delta.", section_title=None))
jeton = uuid.uuid4().hex
db.add(AudioFileJob(session_token=jeton, filename="", filenames=[nom], interview_id=i.id,
                    status="done", block_seconds=60, total_blocks=1, files_done=1,
                    blocks=["Nouveau bloc retranscrit epsilon."]))
if avec_tours:
    db.add(InterviewSegmentJob(
        session_token=jeton, interview_id=i.id, position=0, status="done",
        text="Nouveau bloc retranscrit epsilon.",
        turns_result={"turns": [{"interlocuteur": "Carla", "question": "",
                                 "remarque": "Propos neuf zêta.", "section_title": ""}],
                      "identity": {}}))
db.commit()
print(json.dumps({"mission": m.id, "interview": i.id}))
"""

_LIRE_RETRANSCRIPTION = r"""
import json, sys
from app.db import SessionLocal
from app.models import AudioFileJob, Interview, InterviewSegmentJob
db = SessionLocal()
iid = int(sys.argv[1])
i = db.get(Interview, iid)
print(json.dumps({
    "transcript": i.raw_transcript,
    "turns": [[t.interlocuteur, t.remarque] for t in sorted(i.turns, key=lambda t: t.position)],
    "jobs": db.query(AudioFileJob).filter_by(interview_id=iid).count(),
    "tranches": db.query(InterviewSegmentJob).filter_by(interview_id=iid).count(),
}))
"""


def _ouvrir_le_resultat(nav: Navigateur, base: str, interview_id: int) -> None:
    """Écran de suivi -> (poll « done ») -> clic « Voir le résultat » -> écran de revue."""
    nav.naviguer(f"{base}/interviews/{interview_id}/retranscrire")
    _attendre_cliquable(nav, "#retr-suite-form button")
    nav.cliquer_et_attendre("#retr-suite-form button")


def test_retranscription_appliquer_telecharger_puis_confirmer(
    serveur, nav: Navigateur, tmp_path
) -> None:
    base, base_db = serveur
    ids = _lire(base_db, _SEED_RETRANSCRIPTION, "tours")
    iid = ids["interview"]
    _ouvrir_le_resultat(nav, base, iid)
    assert "Retranscription proposée" in nav.texte()
    champs = nav.evaluer(
        "Array.from(document.querySelectorAll('textarea,input'))"
        ".map(function(e){return e.value;}).join('|')")
    assert "Nouveau bloc retranscrit epsilon." in champs, champs
    assert "Propos neuf zêta." in champs, champs
    # « Appliquer » n'écrit RIEN : la base garde l'ancien contenu.
    assert _lire(base_db, _LIRE_RETRANSCRIPTION, str(iid))["transcript"] == \
        "Ancienne transcription gamma."
    # PDF des tours proposés (POST /interviews/turns/export-pdf, clic réel).
    pdf = _telecharger(nav, "button[formaction$='/interviews/turns/export-pdf']",
                       tmp_path / "dl", "*.pdf")
    assert pdf.read_bytes()[:5] == b"%PDF-" and pdf.stat().st_size > 500
    assert _lire(base_db, _LIRE_RETRANSCRIPTION, str(iid))["transcript"] == \
        "Ancienne transcription gamma.", "l'export PDF ne doit rien enregistrer"
    # Confirmer : remplace transcription ET tours, consomme le job.
    nav.cliquer_et_attendre(f"form[action$='/interviews/{iid}/retranscrire/confirmer'] button.btn-primary[type=submit]")
    assert nav.url().rstrip("/").endswith(f"/interviews/{iid}"), nav.url()
    en_base = _lire(base_db, _LIRE_RETRANSCRIPTION, str(iid))
    assert en_base["transcript"] == "Nouveau bloc retranscrit epsilon."
    assert en_base["turns"] == [["Carla", "Propos neuf zêta."]], en_base["turns"]
    assert en_base["jobs"] == 0 and en_base["tranches"] == 0, en_base
    assert "Nouveau bloc retranscrit epsilon." in nav.evaluer(
        "document.querySelector('textarea.record-transcript').value")
    _sans_erreur(nav, "Retranscription : appliquer et confirmer", sauf=("/record/backup/",))


def test_retranscription_abandonner_laisse_l_entretien_intact(
    serveur, nav: Navigateur
) -> None:
    base, base_db = serveur
    ids = _lire(base_db, _SEED_RETRANSCRIPTION, "tours")
    iid = ids["interview"]
    nav.naviguer(f"{base}/interviews/{iid}/retranscrire")
    _attendre_cliquable(nav, "#retr-abandon-form button")
    nav.cliquer_et_attendre("#retr-abandon-form button")  # confirm() accepté
    assert nav.url().rstrip("/").endswith(f"/interviews/{iid}"), nav.url()
    en_base = _lire(base_db, _LIRE_RETRANSCRIPTION, str(iid))
    assert en_base["jobs"] == 0 and en_base["tranches"] == 0, en_base
    assert en_base["transcript"] == "Ancienne transcription gamma."
    assert en_base["turns"] == [["Bruno Retranscrit", "Ancien propos delta."]]
    assert "Ancienne transcription gamma." in nav.evaluer(
        "document.querySelector('textarea.record-transcript').value")
    # Plus de job : l'écran de suivi renvoie sur la fiche.
    nav.naviguer(f"{base}/interviews/{iid}/retranscrire")
    assert nav.url().rstrip("/").endswith(f"/interviews/{iid}"), nav.url()
    _sans_erreur(nav, "Retranscription : abandonner", sauf=("/record/backup/",))


def test_retranscription_sans_tour_propose_la_transcription_en_pdf(
    serveur, nav: Navigateur, tmp_path
) -> None:
    """Transcription aboutie mais AUCUNE tranche structurée (extraction IA en
    échec) : l'écran dit pourquoi et propose le PDF de la transcription
    (POST /interviews/transcript/export-pdf) — la matière n'est pas perdue."""
    base, base_db = serveur
    ids = _lire(base_db, _SEED_RETRANSCRIPTION, "sans")
    iid = ids["interview"]
    _ouvrir_le_resultat(nav, base, iid)
    assert "Aucun tour de parole détecté" in nav.texte()
    assert "La transcription relancée (" in nav.texte()
    pdf = _telecharger(
        nav, "form[action='/interviews/transcript/export-pdf'] button[type=submit]",
        tmp_path / "dl", "*.pdf")
    assert pdf.name == "transcription_bruno_retranscrit.pdf", pdf.name
    assert pdf.read_bytes()[:5] == b"%PDF-" and pdf.stat().st_size > 500
    assert _lire(base_db, _LIRE_RETRANSCRIPTION, str(iid))["transcript"] == \
        "Ancienne transcription gamma."
    _sans_erreur(nav, "Retranscription sans tour : PDF de secours", sauf=("/record/backup/",))
