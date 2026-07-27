"""Affichage de l'écran « tours de parole » (revue du wizard libre et édition
d'un entretien enregistré) — refonte du 2026-07-27.

Règle demandée : ne montrer que ce qui porte du contenu (pas de titre de
section vide, pas de question vide, un interlocuteur qui enchaîne n'est nommé
qu'une fois), identité dans un onglet dédié seulement si l'IA a relevé quelque
chose, et « Remarque » renommé « Réponse / commentaire ».

L'INVARIANT critique testé ici : masquer n'est pas omettre. Le serveur lit
`turn_interlocuteur`/`turn_question`/`turn_remarque`/`turn_section_title` comme
quatre listes PARALLÈLES (`_parse_turns_from_form`, `save_libre_detail`) —
un champ non posté décalerait tous les tours suivants, mélangeant les propos
d'un interlocuteur avec ceux d'un autre. Chaque tour doit donc poster ses 4
champs, visibles ou non.
"""
from __future__ import annotations

import re

import fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import Interview, InterviewTurn, Mission
from app.routers import interviews as interviews_router


def setup_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
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


# Un cas volontairement « troué » : titres de section absents, questions
# absentes, et deux tours consécutifs du même interlocuteur.
_TURNS = [
    {"interlocuteur": "Consultant·e", "question": "Comment ça se passe ?",
     "remarque": "", "section_title": "Ouverture"},
    {"interlocuteur": "Marc Dupont", "question": "", "remarque": "On travaille en silo.",
     "section_title": ""},
    {"interlocuteur": "marc dupont", "question": "", "remarque": "Et personne ne se parle.",
     "section_title": ""},
    {"interlocuteur": "Consultant·e", "question": "Depuis quand ?",
     "remarque": "", "section_title": ""},
]


def _mission_id() -> int:
    db = SessionLocal()
    try:
        mission = Mission(name="Mission tours", is_draft=True)
        db.add(mission)
        db.commit()
        return mission.id
    finally:
        db.close()


def _review_html(client: TestClient, monkeypatch, identity: dict | None = None) -> str:
    """Rend l'écran de revue des tours (étape 2 du wizard libre)."""
    monkeypatch.setattr(
        interviews_router, "extract_turns_from_text",
        lambda text: {
            "turns": _TURNS,
            "identity": identity or {
                "interviewee_name": "", "interviewee_role": "", "interviewee_entity": "",
            },
        },
    )
    response = client.post(
        f"/missions/{_mission_id()}/interviews/record-libre",
        data={"transcript": "transcription de test"},
    )
    assert response.status_code == 200, response.text
    return response.text


def _count_fields(html: str, name: str) -> int:
    return len(re.findall(r'name="%s"' % re.escape(name), html))


# --------------------------------------------------------------------------- #
# L'invariant : masquer n'est pas omettre
# --------------------------------------------------------------------------- #
def test_chaque_tour_poste_ses_quatre_champs_meme_masques(client, monkeypatch):
    """Régression dure : si un champ masqué cessait d'être rendu, les listes
    parallèles se décaleraient et les propos changeraient d'interlocuteur."""
    html = _review_html(client, monkeypatch)
    for champ in ("turn_interlocuteur", "turn_question", "turn_remarque", "turn_section_title"):
        assert _count_fields(html, champ) == len(_TURNS), champ


def test_le_formulaire_rendu_fait_un_aller_retour_fidele(client, monkeypatch):
    """Bout en bout : on renvoie les champs DANS L'ORDRE DU DOM, comme le
    ferait le navigateur, et les tours doivent revenir identiques."""
    html = _review_html(client, monkeypatch)

    def _valeurs(name: str) -> list[str]:
        # `value="…"` pour les input, contenu balise pour les textarea.
        inputs = re.findall(r'name="%s"[^>]*value="([^"]*)"' % name, html)
        areas = re.findall(r'name="%s"[^>]*>([^<]*)</textarea>' % name, html)
        return inputs or areas

    turns = interviews_router._parse_turns_from_form(
        _valeurs("turn_interlocuteur"),
        _valeurs("turn_question"),
        _valeurs("turn_remarque"),
        _valeurs("turn_section_title"),
    )
    assert [t["interlocuteur"] for t in turns] == [t["interlocuteur"] for t in _TURNS]
    assert [(t["remarque"] or "") for t in turns] == [t["remarque"] for t in _TURNS]
    assert [(t["question"] or "") for t in turns] == [t["question"] for t in _TURNS]
    assert [(t["section_title"] or "") for t in turns] == [t["section_title"] for t in _TURNS]


# --------------------------------------------------------------------------- #
# Les règles d'affichage demandées
# --------------------------------------------------------------------------- #
def test_les_champs_vides_ne_s_affichent_pas_mais_restent_ajoutables(client, monkeypatch):
    html = _review_html(client, monkeypatch)
    # 1 seul titre de section renseigné sur 4 tours -> 3 champs masqués,
    # chacun derrière son lien d'ajout.
    assert html.count("+ Titre de section") == 3
    # 2 questions renseignées sur 4 -> 2 questions masquées.
    assert html.count("+ Question") == 2
    # 2 tours ne portent qu'une question -> leur zone réponse est masquée.
    assert html.count("+ Réponse / commentaire") == 2
    # 1 tour replié -> son interlocuteur est masqué mais détachable.
    assert html.count('class="btn-link turn-add-field turn-detach-speaker"') == 1
    assert html.count('class="turn-optional-field" hidden') == 8


def test_un_tour_n_est_jamais_entierement_masque(client, monkeypatch):
    """Question ET réponse masquées ensemble donnerait un tour vide, illisible.
    L'extraction garantit au moins l'un des deux ; on le verrouille ici."""
    html = _review_html(client, monkeypatch)
    blocs = html.split('<div class="turn-block')[1:]
    assert len(blocs) == len(_TURNS)
    for bloc in blocs:
        masques = bloc.count("+ Question") + bloc.count("+ Réponse / commentaire")
        assert masques <= 1, bloc[:200]


def test_interlocuteur_repete_n_est_nomme_qu_une_fois(client, monkeypatch):
    """« marc dupont » enchaîne « Marc Dupont » (même personne, casse
    différente) : un seul champ nommé visible, le second en écho caché."""
    html = _review_html(client, monkeypatch)
    assert _count_fields(html, "turn_interlocuteur") == 4       # les 4 sont postés
    # `…>` : ne compte que les attributs du balisage, pas leurs occurrences
    # dans le sélecteur du script de synchronisation.
    assert html.count("data-speaker-lead>") == 3                # 3 visibles
    assert html.count("data-speaker-echo>") == 1                # 1 replié
    assert html.count('class="turn-block turn-block-cont"') == 1


def test_remarque_est_renommee_reponse_ou_commentaire(client, monkeypatch):
    html = _review_html(client, monkeypatch)
    assert "Réponse / commentaire" in html
    assert ">Remarque<" not in html


# --------------------------------------------------------------------------- #
# Identité : onglet dédié seulement si relevée
# --------------------------------------------------------------------------- #
def test_identite_non_relevee_reste_atteignable_mais_en_retrait(client, monkeypatch):
    """L'onglet ne s'impose pas quand l'IA n'a rien relevé, mais il doit rester
    ATTEIGNABLE : « Enregistrer sans la synthèse » saute l'étape suivante, seul
    autre écran portant ces champs — sinon l'entretien reste « Sans nom » pour
    toujours (revue adversariale 2026-07-27)."""
    html = _review_html(client, monkeypatch)
    assert "rec-tab-optional" in html
    assert "+ Renseigner l'identité" in html
    assert 'name="interviewee_name"' in html
    assert 'name="interview_date"' in html


def test_identite_relevee_n_est_pas_en_retrait(client, monkeypatch):
    html = _review_html(client, monkeypatch, identity={
        "interviewee_name": "Michel Nakache", "interviewee_role": "",
        "interviewee_entity": "",
    })
    assert "rec-tab-optional" not in html
    assert "+ Renseigner l'identité" not in html


def test_identite_relevee_affiche_un_onglet_dedie(client, monkeypatch):
    html = _review_html(client, monkeypatch, identity={
        "interviewee_name": "Marc Dupont", "interviewee_role": "DSI",
        "interviewee_entity": "",
    })
    assert 'data-turntab="identite"' in html
    assert 'data-turntab="tours"' in html
    assert "Marc Dupont" in html
    # L'onglet Tours reste celui affiché par défaut.
    assert '<div id="turntab-identite" class="rec-tab-panel" hidden>' in html


# --------------------------------------------------------------------------- #
# Enregistrer sans la synthèse + arrêt d'une génération trop longue
# --------------------------------------------------------------------------- #
def test_l_ecran_propose_d_enregistrer_sans_la_synthese(client, monkeypatch):
    html = _review_html(client, monkeypatch)
    assert "record-libre/confirm" in html
    assert "Enregistrer sans la synthèse" in html
    # L'arrêt d'une génération lancée est proposé (masqué jusqu'au clic).
    assert 'id="stop-synthese"' in html
    assert "window.stop()" in html


def test_enregistrement_sans_synthese_conserve_la_transcription_brute(client):
    """Le cas d'usage de ce bouton est justement l'échec IA à répétition : la
    transcription est alors l'artefact le plus précieux — elle était postée par
    l'écran mais jamais lue par la route (revue adversariale 2026-07-27)."""
    mission_id = _mission_id()
    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/confirm",
        data={
            "transcript": "Transcription brute de 1h30 à ne surtout pas perdre.",
            "interviewee_name": "Michel Nakache",
            "turn_interlocuteur": [t["interlocuteur"] for t in _TURNS],
            "turn_question": [t["question"] for t in _TURNS],
            "turn_remarque": [t["remarque"] for t in _TURNS],
            "turn_section_title": [t["section_title"] for t in _TURNS],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    try:
        interview = db.scalars(
            select(Interview).where(Interview.mission_id == mission_id)
        ).one()
        assert interview.raw_transcript == "Transcription brute de 1h30 à ne surtout pas perdre."
    finally:
        db.close()


def test_enregistrement_sans_tour_ne_cree_pas_d_entretien_fantome(client):
    """L'écran d'erreur « aucun tour » ne poste aucun champ turn_* : sans
    garde, un clic sur « Enregistrer sans la synthèse » y créait un entretien
    vide « Sans nom », compté dans la mission."""
    mission_id = _mission_id()
    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/confirm",
        data={"interviewee_name": "Personne"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Aucun tour de parole" in response.text
    db = SessionLocal()
    try:
        assert db.scalars(
            select(Interview).where(Interview.mission_id == mission_id)
        ).all() == []
    finally:
        db.close()


def test_un_tour_dont_l_interlocuteur_est_vide_garde_son_contenu(client):
    """Depuis que les tours consécutifs partagent un seul champ visible, vider
    ce champ vidait TOUT le groupe (les échos suivent) et le serveur jetait
    chaque tour sans interlocuteur : plusieurs tours de contenu disparaissaient
    en silence. Le contenu prime désormais sur l'étiquette."""
    mission_id = _mission_id()
    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/confirm",
        data={
            "interviewee_name": "X",
            "turn_interlocuteur": ["", ""],
            "turn_question": ["", ""],
            "turn_remarque": ["Propos un.", "Propos deux."],
            "turn_section_title": ["", ""],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    db = SessionLocal()
    try:
        interview = db.scalars(
            select(Interview).where(Interview.mission_id == mission_id)
        ).one()
        assert [t.remarque for t in interview.turns] == ["Propos un.", "Propos deux."]
        assert [t.interlocuteur for t in interview.turns] == ["Intervenant", "Intervenant"]
    finally:
        db.close()


def test_enregistrement_sans_synthese_cree_l_entretien_avec_ses_tours(client):
    """L'entretien (tours + identité) ne doit pas être retenu en otage par une
    génération IA : la route de confirmation accepte un résumé et une
    répartition vides, et l'entretien est bien enregistré."""
    mission_id = _mission_id()
    response = client.post(
        f"/missions/{mission_id}/interviews/record-libre/confirm",
        data={
            "interviewee_name": "Michel Nakache",
            "turn_interlocuteur": [t["interlocuteur"] for t in _TURNS],
            "turn_question": [t["question"] for t in _TURNS],
            "turn_remarque": [t["remarque"] for t in _TURNS],
            "turn_section_title": [t["section_title"] for t in _TURNS],
            # ni `resume` ni `repartition_*` : la synthèse n'a pas été générée.
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    db = SessionLocal()
    try:
        interview = db.scalars(
            select(Interview).where(Interview.mission_id == mission_id)
        ).one()
        assert interview.mode == "libre"
        assert interview.resume is None
        assert len(interview.turns) == len(_TURNS)
        # La synthèse reste à générer plus tard (aperçu -> « Régénérer »).
        assert all(not v for v in (interview.repartition or {}).values())
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Parité sur l'écran d'édition d'un entretien déjà enregistré
# --------------------------------------------------------------------------- #
def test_ecran_detail_applique_les_memes_regles(client):
    db = SessionLocal()
    try:
        mission = Mission(name="Mission détail tours")
        db.add(mission)
        db.commit()
        interview = Interview(
            mission_id=mission.id, interviewee_name="Marc Dupont", mode="libre",
            raw_transcript="Transcription brute conservée.",
        )
        db.add(interview)
        db.commit()
        for position, turn in enumerate(_TURNS):
            db.add(InterviewTurn(
                interview_id=interview.id, position=position,
                interlocuteur=turn["interlocuteur"], question=turn["question"] or None,
                remarque=turn["remarque"] or None, section_title=turn["section_title"] or None,
            ))
        db.commit()
        interview_id = interview.id
    finally:
        db.close()

    html = client.get(f"/interviews/{interview_id}").text
    assert _count_fields(html, "turn_interlocuteur") == len(_TURNS)
    assert _count_fields(html, "turn_question") == len(_TURNS)
    assert _count_fields(html, "turn_remarque") == len(_TURNS)
    assert _count_fields(html, "turn_section_title") == len(_TURNS)
    assert _count_fields(html, "turn_id") == len(_TURNS)  # l'id reste posté par tour
    assert html.count("data-speaker-echo>") == 1
    assert "Réponse / commentaire" in html
    assert ">Remarque<" not in html

    # Réorganisation en onglets (2026-07-27) : Transcription / Tour de table /
    # Aperçu, chaque téléchargement dans son onglet, enregistrement HORS onglets.
    for tab in ("tours", "transcription", "apercu"):
        assert f'data-turntab="{tab}"' in html
    # Tour de table : export PDF via les champs du formulaire courant.
    assert 'formaction="/interviews/turns/export-pdf"' in html
    # Transcription : uniquement l'export de la transcription.
    assert f"/interviews/{interview_id}/export/transcription/pdf" in html
    # Aperçu : rendu par sections (titre de section présent une fois).
    assert '<div id="turntab-apercu"' in html
    assert "preview-doc" in html
    # Enregistrer, indépendant des onglets : en tête (form=) ET en pied.
    assert 'form="libre-detail-form"' in html


def _entretien_libre(raw_transcript: str | None, avec_tours: bool = True) -> int:
    """Crée un entretien libre enregistré et rend son id."""
    db = SessionLocal()
    try:
        mission = Mission(name="Mission onglets")
        db.add(mission)
        db.commit()
        interview = Interview(
            mission_id=mission.id, interviewee_name="Marc Dupont", mode="libre",
            raw_transcript=raw_transcript,
        )
        db.add(interview)
        db.commit()
        if avec_tours:
            for position, turn in enumerate(_TURNS):
                db.add(InterviewTurn(
                    interview_id=interview.id, position=position,
                    interlocuteur=turn["interlocuteur"], question=turn["question"] or None,
                    remarque=turn["remarque"] or None, section_title=turn["section_title"] or None,
                ))
            db.commit()
        return interview.id
    finally:
        db.close()


def test_ordre_des_onglets_transcription_puis_tour_de_table_puis_apercu(client):
    """Ordre demandé (2026-07-27) : 1. Transcription 2. Tour de table
    3. Aperçu — et c'est bien la Transcription qui s'affiche à l'ouverture."""
    html = client.get(f"/interviews/{_entretien_libre('Texte brut.')}").text
    positions = [html.index(f'data-turntab="{tab}"') for tab in ("transcription", "tours", "apercu")]
    assert positions == sorted(positions)
    assert "Tour de table" in html
    # Panneau visible à l'ouverture = transcription ; les deux autres masqués.
    assert '<div id="turntab-transcription" class="rec-tab-panel">' in html
    assert '<div id="turntab-tours" class="rec-tab-panel" hidden>' in html
    assert '<div id="turntab-apercu" class="rec-tab-panel" hidden>' in html


def test_les_deux_exports_pdf_sont_disponibles(client):
    """Un bouton d'export par onglet : transcription (lien GET) et tour de
    table (submit sur les champs du formulaire courant)."""
    interview_id = _entretien_libre("Texte brut.")
    html = client.get(f"/interviews/{interview_id}").text
    assert f'href="/interviews/{interview_id}/export/transcription/pdf"' in html
    assert "Télécharger la transcription (PDF)" in html
    assert 'formaction="/interviews/turns/export-pdf"' in html
    assert "Télécharger le tour de table (PDF)" in html


def test_tour_de_table_renseigne_implique_transcription_renseignee(client):
    """Point 4 de la demande utilisateur 2026-07-27 : un entretien dont les
    tours sont remplis ne doit JAMAIS afficher un onglet Transcription vide,
    même sans `raw_transcript` (entretiens d'avant le champ, imports .docx)."""
    interview_id = _entretien_libre(None)
    html = client.get(f"/interviews/{interview_id}").text
    assert "Reconstituée à partir du tour de table" in html
    # La matière des tours est bien restituée dans le panneau transcription.
    panneau = html.split('id="turntab-transcription"')[1].split('id="turntab-tours"')[0]
    assert "On travaille en silo." in panneau
    assert "Comment ça se passe ?" in panneau
    # ... et l'export reste proposé (avant, le bouton disparaissait avec le texte).
    assert f"/interviews/{interview_id}/export/transcription/pdf" in panneau


def test_export_transcription_pdf_rend_la_transcription_pas_les_tours(client):
    """La route rendait `build_turns_only_pdf`, soit exactement le même document
    que le bouton « tour de table » de l'onglet voisin — deux boutons, un seul
    contenu (constat utilisateur 2026-07-27)."""
    interview_id = _entretien_libre("Marqueur de transcription brute.")
    response = client.get(f"/interviews/{interview_id}/export/transcription/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    with fitz.open(stream=response.content, filetype="pdf") as doc:
        texte = "\n".join(page.get_text() for page in doc)
    assert "Marqueur de transcription brute." in texte


def test_export_transcription_pdf_se_replie_sur_les_tours(client):
    """Sans transcription brute, l'export suit la même règle que l'écran :
    reconstitution depuis les tours (sinon le bouton affiché téléchargerait un
    document vide, ou un 400)."""
    interview_id = _entretien_libre(None)
    response = client.get(f"/interviews/{interview_id}/export/transcription/pdf")
    assert response.status_code == 200
    with fitz.open(stream=response.content, filetype="pdf") as doc:
        texte = "\n".join(page.get_text() for page in doc)
    assert "On travaille en silo." in texte
    # Le document dit d'où vient le texte, et n'annonce plus un « export de
    # secours après échec IA » (sous-titre par défaut du builder) alors que
    # rien n'a échoué.
    assert "Reconstituée à partir du tour de table" in texte
    assert "Export de secours" not in texte


def test_export_transcription_pdf_400_si_rien_a_exporter(client):
    interview_id = _entretien_libre(None, avec_tours=False)
    response = client.get(f"/interviews/{interview_id}/export/transcription/pdf")
    assert response.status_code == 400


def test_reconstitution_nomme_un_interlocuteur_manquant(client):
    """Un tour peut porter du contenu sans interlocuteur identifié (filtrage
    relâché du 2026-07-22) : la ligne reconstituée ne doit pas s'ouvrir sur un
    « : » orphelin."""
    from app.services.interview_export import transcript_from_turns

    class _Tour:
        section_title, question, interlocuteur = None, None, ""
        remarque = "Propos sans interlocuteur."

    assert transcript_from_turns([_Tour()]) == "Intervenant : Propos sans interlocuteur."


def test_sans_tours_ni_transcription_l_onglet_le_dit(client):
    html = client.get(f"/interviews/{_entretien_libre(None, avec_tours=False)}").text
    assert "Pas de transcription pour cet entretien" in html


def test_entretien_structure_sans_trame_ne_500_pas(client):
    """Trouvé sur données réelles (2026-07-27) : consulter un entretien
    structuré d'une mission SANS trame levait une AttributeError (500) au lieu
    d'afficher le message « la trame est vide » que l'écran porte déjà."""
    db = SessionLocal()
    try:
        mission = Mission(name="Mission sans trame")
        db.add(mission)
        db.commit()
        interview = Interview(mission_id=mission.id, interviewee_name="Sans trame")
        db.add(interview)
        db.commit()
        interview_id = interview.id
    finally:
        db.close()

    response = client.get(f"/interviews/{interview_id}")
    assert response.status_code == 200
    assert "La trame est vide" in response.text
