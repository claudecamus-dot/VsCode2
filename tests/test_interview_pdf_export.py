"""Tests de l'export PDF d'un entretien (`interview_pdf_export.py` +
`GET /interviews/{id}/export/pdf`) — jusqu'ici non couvert (constat de
l'audit du 2026-07-19). Le texte est extrait du PDF généré via PyMuPDF
(`fitz`, déjà présent dans l'environnement) pour vérifier le contenu réel
rendu, pas seulement l'absence d'exception ou un status 200.

Couvre aussi le fix du rendu multiligne (2026-07-19) : `Paragraph` reportlab
traite le texte comme du mini-HTML et collapse les `\\n` bruts en un seul
espace — une réponse/note/remarque saisie sur plusieurs lignes s'affichait
comme un bloc continu avant la conversion en `<br/>` (`_text()` dans
`interview_pdf_export.py`).
"""
from __future__ import annotations

import logging

import fitz
import pytest
from fastapi.testclient import TestClient
from reportlab.platypus.doctemplate import LayoutError

from app.main import app
from app.db import DB_PATH, SessionLocal, engine, init_db
from app.models import Answer, Interview, InterviewTurn, Mission, Question, Theme, Trame, Verbatim
from app.services.interview_pdf_export import (
    build_interview_pdf,
    build_synthese_only_pdf,
    build_transcript_only_pdf,
    build_turns_only_pdf,
)


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


def _pdf_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


MULTILINE_ANSWER = "Ligne 1 de la réponse.\nLigne 2 de la réponse.\nLigne 3, dernière."


def _build_parametre_interview(multiline: bool = True) -> int:
    """Mission + trame + thème + question + entretien paramétré, avec
    réponse, verbatim et notes libres — retourne l'id de l'entretien."""
    session = SessionLocal()
    try:
        mission = Mission(name="Mission PDF", trame=Trame(name="Trame PDF"))
        session.add(mission)
        session.flush()
        theme = Theme(trame_id=mission.trame.id, title="Organisation", position=0)
        session.add(theme)
        session.flush()
        question = Question(theme_id=theme.id, label="Comment ça se passe ?", qtype="open", position=0)
        session.add(question)
        session.flush()
        interview = Interview(
            mission_id=mission.id, mode="parametre",
            interviewee_name="Jean Dupont", interviewee_role="DSI",
            free_notes="Note ligne A.\nNote ligne B." if multiline else "Note simple.",
        )
        session.add(interview)
        session.flush()
        session.add(Answer(
            interview_id=interview.id, question_id=question.id,
            text=MULTILINE_ANSWER if multiline else "Réponse sur une seule ligne.",
        ))
        session.add(Verbatim(
            interview_id=interview.id, question_id=question.id,
            quote="On adapte tout en continu.",
        ))
        session.commit()
        return interview.id
    finally:
        session.close()


def test_build_interview_pdf_parametre_content_and_multiline_preserved() -> None:
    interview_id = _build_parametre_interview(multiline=True)
    session = SessionLocal()
    try:
        interview = session.get(Interview, interview_id)
        pdf_bytes = build_interview_pdf(interview)
    finally:
        session.close()

    assert pdf_bytes[:4] == b"%PDF"
    text = _pdf_text(pdf_bytes)

    assert "Jean Dupont" in text
    assert "DSI" in text
    assert "Organisation" in text
    assert "Comment ça se passe ?" in text
    assert "On adapte tout en continu." in text

    # Rendu multiligne (2026-07-19) : chaque ligne de la réponse et des notes
    # apparaît comme une ligne distincte de texte extrait, pas fusionnée en un
    # seul bloc continu séparé par des espaces.
    for line in ("Ligne 1 de la réponse.", "Ligne 2 de la réponse.", "Ligne 3, dernière."):
        assert line in text.split("\n")
    assert "Note ligne A." in text.split("\n")
    assert "Note ligne B." in text.split("\n")
    # Preuve négative : si le bug de collapse réapparaît, les 3 lignes de la
    # réponse se retrouvent concaténées avec un simple espace sur une ligne.
    assert "Ligne 1 de la réponse. Ligne 2 de la réponse." not in text


def test_build_interview_pdf_parametre_sans_reponse_ni_trame_ne_plante_pas() -> None:
    session = SessionLocal()
    try:
        mission = Mission(name="Mission PDF Vide")
        session.add(mission)
        session.flush()
        interview = Interview(mission_id=mission.id, mode="parametre", interviewee_name="Sans Trame")
        session.add(interview)
        session.commit()
        interview_id = interview.id
    finally:
        session.close()

    session = SessionLocal()
    try:
        pdf_bytes = build_interview_pdf(session.get(Interview, interview_id))
    finally:
        session.close()
    assert pdf_bytes[:4] == b"%PDF"
    assert "Sans Trame" in _pdf_text(pdf_bytes)


def test_build_interview_pdf_parametre_includes_raw_transcript_when_present() -> None:
    """La transcription brute (mode structuré, flux d'enregistrement audio,
    2026-07-19) apparaît comme section finale du PDF quand elle est
    renseignée — absente sinon (interviews plus anciennes, import .docx)."""
    interview_id = _build_parametre_interview(multiline=False)
    session = SessionLocal()
    try:
        interview = session.get(Interview, interview_id)
        interview.raw_transcript = (
            "Premier paragraphe de la transcription brute.\n\n"
            "Second paragraphe, distinct du premier."
        )
        session.commit()
        pdf_bytes = build_interview_pdf(session.get(Interview, interview_id))
    finally:
        session.close()

    text = _pdf_text(pdf_bytes)
    assert "Transcription brute" in text
    assert "Premier paragraphe de la transcription brute." in text.split("\n")
    assert "Second paragraphe, distinct du premier." in text.split("\n")


def test_build_interview_pdf_parametre_sans_raw_transcript_pas_de_section() -> None:
    """Sans transcription brute enregistrée (import .docx, ou entretien créé
    avant ce champ), la section ne doit pas apparaître du tout."""
    interview_id = _build_parametre_interview(multiline=False)
    session = SessionLocal()
    try:
        pdf_bytes = build_interview_pdf(session.get(Interview, interview_id))
    finally:
        session.close()
    assert "Transcription brute" not in _pdf_text(pdf_bytes)


def _build_libre_interview(multiline: bool = True) -> int:
    session = SessionLocal()
    try:
        mission = Mission(name="Mission PDF Libre")
        session.add(mission)
        session.flush()
        interview = Interview(
            mission_id=mission.id, mode="libre",
            interviewee_name="Claire Rousseau",
            resume="Message central de l'entretien.",
            repartition={
                "contexte": "- Contexte détaillé",
                "culture_adn": "",
                "forces_succes": "- Force clé",
                "points_amelioration": "",
                "aspirations": "",
            },
        )
        session.add(interview)
        session.flush()
        session.add(InterviewTurn(
            interview_id=interview.id, position=0,
            interlocuteur="Consultant·e", question="Comment ça se passe ?",
            section_title="Ouverture",
        ))
        session.add(InterviewTurn(
            interview_id=interview.id, position=1,
            interlocuteur="Claire Rousseau",
            remarque=(
                "Première ligne du témoignage.\nDeuxième ligne du témoignage."
                if multiline else "Témoignage sur une seule ligne."
            ),
        ))
        session.commit()
        return interview.id
    finally:
        session.close()


def test_build_interview_pdf_libre_content_and_multiline_preserved() -> None:
    interview_id = _build_libre_interview(multiline=True)
    session = SessionLocal()
    try:
        interview = session.get(Interview, interview_id)
        pdf_bytes = build_interview_pdf(interview)
    finally:
        session.close()

    text = _pdf_text(pdf_bytes)
    assert "Claire Rousseau" in text
    assert "Message central de l'entretien." in text
    assert "Ouverture" in text
    assert "Comment ça se passe ?" in text
    # Les 5 catégories transverses ne s'exportent plus par entretien
    # (2026-07-27) : elles vivent dans la synthèse globale de mission.
    assert "Répartition par catégorie" not in text
    assert "Contexte détaillé" not in text

    # Le préfixe interlocuteur reste sur la 1ère ligne du tour (« Claire
    # Rousseau : Première ligne… ») ; seule la suite du tour continue en
    # lignes nues — c'est cette continuation qui prouve le fix multiligne.
    assert "Claire Rousseau : Première ligne du témoignage." in text.split("\n")
    assert "Deuxième ligne du témoignage." in text.split("\n")
    assert "Première ligne du témoignage. Deuxième ligne du témoignage." not in text


def test_build_interview_pdf_libre_sans_tours_ne_plante_pas() -> None:
    session = SessionLocal()
    try:
        mission = Mission(name="Mission PDF Libre Vide")
        session.add(mission)
        session.flush()
        interview = Interview(mission_id=mission.id, mode="libre", interviewee_name="Sans Tours")
        session.add(interview)
        session.commit()
        interview_id = interview.id
    finally:
        session.close()

    session = SessionLocal()
    try:
        pdf_bytes = build_interview_pdf(session.get(Interview, interview_id))
    finally:
        session.close()
    text = _pdf_text(pdf_bytes)
    assert "Sans Tours" in text
    assert "Aucun tour de parole" in text


# --------------------------------------------------------------------------- #
# Route HTTP GET /interviews/{id}/export/pdf
# --------------------------------------------------------------------------- #
def test_export_pdf_route_parametre(client: TestClient) -> None:
    interview_id = _build_parametre_interview(multiline=True)
    response = client.get(f"/interviews/{interview_id}/export/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="entretien_jean_dupont.pdf"'
    assert response.content[:4] == b"%PDF"
    text = _pdf_text(response.content)
    assert "Jean Dupont" in text
    assert "Ligne 2 de la réponse." in text.split("\n")


def test_export_pdf_route_libre(client: TestClient) -> None:
    interview_id = _build_libre_interview(multiline=False)
    response = client.get(f"/interviews/{interview_id}/export/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    text = _pdf_text(response.content)
    assert "Claire Rousseau" in text
    assert "Témoignage sur une seule ligne." in text


def test_export_pdf_route_entretien_introuvable_404(client: TestClient) -> None:
    assert client.get("/interviews/999999/export/pdf").status_code == 404


# --------------------------------------------------------------------------- #
# build_transcript_only_pdf() + POST /interviews/transcript/export-pdf
# (2026-07-19) : export de secours d'une transcription pas encore enregistrée
# (aucun Interview en base) — utilisé quand l'extraction IA en aval (tours,
# réponses, répartition) échoue, pour ne pas perdre le texte transcrit.
# --------------------------------------------------------------------------- #
def test_build_transcript_only_pdf_content_and_multiline() -> None:
    pdf_bytes = build_transcript_only_pdf(
        "Premier paragraphe de la transcription.\n\nSecond paragraphe, distinct.",
        interviewee_name="Marc Dupont",
    )
    assert pdf_bytes[:4] == b"%PDF"
    text = _pdf_text(pdf_bytes)
    assert "Transcription brute — Marc Dupont" in text
    assert "Premier paragraphe de la transcription." in text.split("\n")
    assert "Second paragraphe, distinct." in text.split("\n")


def test_build_transcript_only_pdf_without_name_uses_generic_title() -> None:
    pdf_bytes = build_transcript_only_pdf("Un texte quelconque.")
    text = _pdf_text(pdf_bytes)
    assert "Transcription brute" in text
    assert "Transcription brute — " not in text


def test_build_transcript_only_pdf_empty_shows_placeholder_not_blank() -> None:
    pdf_bytes = build_transcript_only_pdf("   ")
    assert "— Transcription vide —" in _pdf_text(pdf_bytes)


def test_export_transcript_only_pdf_route(client: TestClient) -> None:
    response = client.post(
        "/interviews/transcript/export-pdf",
        data={"transcript": "Texte de secours à exporter.", "interviewee_name": "Alice Martin"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="transcription_alice_martin.pdf"'
    text = _pdf_text(response.content)
    assert "Texte de secours à exporter." in text


def test_export_transcript_only_pdf_route_sans_nom(client: TestClient) -> None:
    response = client.post(
        "/interviews/transcript/export-pdf", data={"transcript": "Texte anonyme."}
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="transcription_brute.pdf"'


def test_export_transcript_only_pdf_route_rejects_empty(client: TestClient) -> None:
    response = client.post("/interviews/transcript/export-pdf", data={"transcript": "   "})
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# build_turns_only_pdf() / build_synthese_only_pdf() (2026-07-19) : export PDF
# des écrans du wizard libre AVANT enregistrement (tours de parole, synthèse)
# — même mise en forme factorisée que build_interview_pdf(), façon
# 01_Transcription_editee…docx / 02_Synthese_session_3…docx.
# --------------------------------------------------------------------------- #
def _span_colors(pdf_bytes: bytes, needle: str) -> list[int]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    colors = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if needle in span["text"]:
                        colors.append(span["color"])
    return colors


def test_build_turns_only_pdf_colors_question_teal_and_remarque_navy() -> None:
    """Motif vérifié sur le document modèle (01_Transcription_editee…docx,
    2026-07-19) : le préfixe interlocuteur est teal (#008A92) pour un tour
    qui porte une question, navy (#17324D) pour un tour qui porte une
    remarque — absent du rendu avant ce correctif (couleur uniforme)."""
    turns = [
        {"interlocuteur": "Consultant", "question": "Comment ça se passe ?", "remarque": None, "section_title": "Ouverture"},
        {"interlocuteur": "Marie Dupont", "question": None, "remarque": "Ça se passe bien.", "section_title": None},
    ]
    pdf_bytes = build_turns_only_pdf(turns, "Vérif Couleur")
    assert pdf_bytes[:4] == b"%PDF"
    text = _pdf_text(pdf_bytes)
    assert "Vérif Couleur" in text  # titre
    assert "Ouverture" in text
    assert "Comment ça se passe ?" in text
    assert "Ça se passe bien." in text

    assert _span_colors(pdf_bytes, "Consultant") == [0x008A92]
    assert _span_colors(pdf_bytes, "Marie Dupont") == [0x17324D]


def test_build_turns_only_pdf_sans_tours_montre_placeholder() -> None:
    text = _pdf_text(build_turns_only_pdf([], "Personne"))
    assert "Aucun tour de parole" in text


def test_build_synthese_only_pdf_content() -> None:
    # Depuis le 2026-07-27, la répartition n'est plus rendue par entretien
    # (les 5 catégories vivent dans la synthèse globale de mission) — le
    # paramètre reste accepté mais ignoré.
    pdf_bytes = build_synthese_only_pdf(
        resume="Message central de synthèse.",
        repartition={"contexte": "- Contexte détaillé"},
        interviewee_name="Vérif Synthèse",
    )
    assert pdf_bytes[:4] == b"%PDF"
    text = _pdf_text(pdf_bytes)
    assert "Synthèse — Vérif Synthèse" in text
    assert "Message central de synthèse." in text
    assert "Contexte détaillé" not in text
    assert "Répartition par catégorie" not in text


def test_build_synthese_only_pdf_sans_nom_titre_generique() -> None:
    text = _pdf_text(build_synthese_only_pdf("Résumé.", {}))
    assert "Synthèse" in text
    assert "Synthèse — " not in text


def test_export_turns_only_pdf_route(client: TestClient) -> None:
    response = client.post(
        "/interviews/turns/export-pdf",
        data={
            "interviewee_name": "Route Testeur",
            "turn_interlocuteur": ["Consultant", "Testeur"],
            "turn_question": ["Une question ?", ""],
            "turn_remarque": ["", "Une remarque."],
            "turn_section_title": ["", ""],
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="tours_route_testeur.pdf"'
    text = _pdf_text(response.content)
    assert "Une question ?" in text
    assert "Une remarque." in text


def test_export_turns_only_pdf_route_rejects_empty(client: TestClient) -> None:
    response = client.post("/interviews/turns/export-pdf", data={})
    assert response.status_code == 400


def test_export_synthese_only_pdf_route(client: TestClient) -> None:
    response = client.post(
        "/interviews/synthese/export-pdf",
        data={
            "interviewee_name": "Route Synthese",
            "resume": "Résumé de test.",
            "repartition_contexte": "- Contexte route",
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    text = _pdf_text(response.content)
    assert "Résumé de test." in text
    # La répartition postée n'est plus rendue (catégories = niveau mission).
    assert "Contexte route" not in text


def test_export_synthese_only_pdf_route_rejects_empty(client: TestClient) -> None:
    response = client.post("/interviews/synthese/export-pdf", data={})
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# GÉOMÉTRIE, UNICODE ET MÉTADONNÉES DU PDF PRODUIT (2026-08-31)
#
# Les tests ci-dessus passaient tous sur un PDF qui, mesuré sur 6 exports et
# 22 pages rastérisées : plantait au-delà d'un verbatim d'une page (500 nu sur
# la route), perdait tout caractère hors Latin-1 sans un mot, alignait trois
# bords gauche différents sur une même page et ne portait ni titre, ni auteur,
# ni langue. Aucune assertion ne regardait la mise en page RÉELLE : celles qui
# suivent la mesurent sur le document produit (PyMuPDF), pas sur l'absence
# d'exception.
# --------------------------------------------------------------------------- #
_MM = 72 / 25.4          # 1 mm en points PDF
_MARGE_MM = 20.0         # la grille unique de la maquette
_MARGE_BASSE_MM = 18.0   # bas du cadre de texte (le pied de page est dessous)

# Un copier-coller Teams ordinaire. 5 016 caractères passaient, 12 690 faisaient
# lever `LayoutError` : l'encadré était un `Table` d'une seule cellule, donc
# insécable et plus haut que le cadre de la page.
VERBATIM_12690 = (
    "On a repris tout le processus depuis le début, sans jamais rien jeter. "
) * 190
VERBATIM_12690 = VERBATIM_12690[:12690]

# Le seuil haut mesuré le 2026-08-31 : 5 016 caractères passaient encore. Trop
# long pour la place restante en page 1, assez court pour que l'ancien tableau
# insécable tienne, lui, sur une page entière — donc le cas exact qui laissait
# derrière lui une page à 40 % de remplissage.
VERBATIM_5016 = VERBATIM_12690[:5016]


def _interview_avec_verbatim(quote: str) -> int:
    """Entretien paramétré standard dont le verbatim (donc l'encadré ambré)
    porte `quote`."""
    interview_id = _build_parametre_interview(multiline=False)
    session = SessionLocal()
    try:
        interview = session.get(Interview, interview_id)
        interview.verbatims[0].quote = quote
        session.commit()
    finally:
        session.close()
    return interview_id


def _pdf_de(interview_id: int) -> bytes:
    session = SessionLocal()
    try:
        return build_interview_pdf(session.get(Interview, interview_id))
    finally:
        session.close()


def _x_gauche_texte_mm(page, amorce: str) -> float:
    """Bord gauche, en mm, du premier fragment de texte commençant par `amorce`."""
    for bloc in page.get_text("dict")["blocks"]:
        for ligne in bloc.get("lines", []):
            for span in ligne["spans"]:
                if span["text"].startswith(amorce):
                    return round(span["bbox"][0] / _MM, 1)
    raise AssertionError(f"texte introuvable sur la page : {amorce!r}")


def _x_gauche_filet_mm(page) -> float:
    """Bord gauche du filet pleine largeur (soulignement d'un titre H1, trait
    d'en-tête) — posé par le frame reportlab, pas par le canvas."""
    filets = [d["rect"].x0 for d in page.get_drawings()
              if d["fill"] is None and d["rect"].width > 100]
    assert filets, "aucun filet pleine largeur sur cette page"
    return round(min(filets) / _MM, 1)


def _x_gauche_encadre_mm(page) -> float:
    """Bord gauche du fond ambré de l'encadré — le seul aplat de couleur du
    document."""
    fonds = [d["rect"].x0 for d in page.get_drawings() if d["fill"] is not None]
    assert fonds, "aucun encadré dessiné sur cette page"
    return round(min(fonds) / _MM, 1)


def _blanc_en_pied_mm(page) -> float:
    """Hauteur de blanc entre le bas du dernier contenu et le bas du cadre de
    texte. Le pied de page, dessiné SOUS le cadre, est exclu du calcul."""
    bas_cadre = page.rect.height - _MARGE_BASSE_MM * _MM
    bas_contenu = 0.0
    for bloc in page.get_text("blocks"):
        if bloc[3] <= bas_cadre + 1:
            bas_contenu = max(bas_contenu, bloc[3])
    for dessin in page.get_drawings():
        if dessin["rect"].y1 <= bas_cadre + 1:
            bas_contenu = max(bas_contenu, dessin["rect"].y1)
    return (bas_cadre - bas_contenu) / _MM


@pytest.fixture
def client_http() -> TestClient:
    """Client qui n'escamote PAS l'erreur serveur en exception Python : c'est
    la réponse reçue par le navigateur du consultant qu'on veut voir — avant
    correctif, un 500 `text/plain` au corps « Internal Server Error »."""
    return TestClient(app, raise_server_exceptions=False)


def test_build_interview_pdf_verbatim_plus_haut_qu_une_page_ne_leve_pas() -> None:
    """Un verbatim de 12 690 caractères (copier-coller Teams ordinaire) faisait
    lever `LayoutError: Flowable <Table 1 rows x 1 cols(tallest row 1589)> too
    large on page 2` — l'encadré était un tableau d'une seule cellule, donc
    insécable. Il doit désormais se répandre sur autant de pages qu'il faut."""
    pdf_bytes = _pdf_de(_interview_avec_verbatim(VERBATIM_12690))
    assert pdf_bytes[:4] == b"%PDF"

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count >= 3, "un verbatim de 12 690 caractères tient sur 3 pages au moins"
    texte = " ".join(_pdf_text(pdf_bytes).split())
    # Début ET fin du verbatim : un encadré tronqué au saut de page passerait
    # la première assertion mais pas la seconde.
    assert " ".join(VERBATIM_12690[:80].split()) in texte
    assert " ".join(VERBATIM_12690[-80:].split()) in texte


def test_export_pdf_route_verbatim_tres_long_ne_rend_pas_500(client_http: TestClient) -> None:
    """La vraie route rendait `HTTP 500`, `content-type: text/plain`, corps
    « Internal Server Error » sur ce même entretien (mesuré le 2026-08-31)."""
    interview_id = _interview_avec_verbatim(VERBATIM_12690)
    response = client_http.get(f"/interviews/{interview_id}/export/pdf")
    assert response.status_code == 200, response.headers.get("content-type")
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:4] == b"%PDF"


def test_export_pdf_route_replie_sur_le_secours_si_la_mise_en_page_echoue(
    client_http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Indépendamment de la cause corrigée : la mise en page reste le maillon
    fragile de la chaîne. Quoi qu'il arrive, le consultant doit repartir avec
    sa matière — en texte simple s'il le faut — et non avec un 500."""
    interview_id = _build_parametre_interview(multiline=False)

    def _echoue(_interview):
        raise LayoutError("mise en page impossible")

    monkeypatch.setattr("app.routers.interviews.build_interview_pdf", _echoue)
    response = client_http.get(f"/interviews/{interview_id}/export/pdf")

    assert response.status_code == 200, response.headers.get("content-type")
    assert response.headers["content-type"] == "application/pdf"
    texte = _pdf_text(response.content)
    assert "Jean Dupont" in texte
    assert "secours" in texte.lower()          # l'export dit qu'il est dégradé
    assert "On adapte tout en continu." in texte  # ... et il porte bien la matière


def test_build_interview_pdf_caracteres_hors_latin1_ne_sont_plus_perdus() -> None:
    """La matière première de ce produit est du texte collé depuis Teams. Les
    polices base-14 de PDF ne codent que du Latin-1 : « Nguyễn Thị Mai »
    s'imprimait « NguyIn ThI Mai », « Иванов » « IIIIII » et « Δημήτρης »
    « ∆ηµIτρης » (glyphes Symbol) — sans exception ni avertissement."""
    interview_id = _build_parametre_interview(multiline=False)
    session = SessionLocal()
    try:
        interview = session.get(Interview, interview_id)
        interview.interviewee_name = "Nguyễn Thị Mai"
        interview.free_notes = "Présents : Иванов (Moscou) et Δημήτρης (Athènes)."
        session.commit()
    finally:
        session.close()

    texte = _pdf_text(_pdf_de(interview_id))
    for fragment in ("Nguyễn Thị Mai", "Иванов", "Δημήτρης"):
        assert fragment in texte, f"{fragment!r} absent du texte extrait du PDF"
    # Preuve négative : les substitutions Latin-1 observées avant correctif.
    assert "NguyIn ThI Mai" not in texte
    assert "IIIIII" not in texte


def test_build_interview_pdf_caracteres_non_rendables_signales_dans_le_log(caplog) -> None:
    """Aucune police candidate ne dessine d'emoji. Ce qui ne peut pas être rendu
    doit au moins être DIT : c'est le seul défaut de la chaîne qu'aucun parseur
    ne rendait visible (le PDF s'ouvrait sans erreur, le texte manquait)."""
    interview_id = _build_parametre_interview(multiline=False)
    session = SessionLocal()
    try:
        interview = session.get(Interview, interview_id)
        interview.free_notes = "Décision actée ✅ objectif 🎯"
        session.commit()
    finally:
        session.close()

    with caplog.at_level(logging.WARNING, logger="app.services.interview_pdf_export"):
        _pdf_de(interview_id)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "non rendable" in messages
    assert "U+2705" in messages and "U+1F3AF" in messages


def test_build_interview_pdf_aucune_police_non_embarquee() -> None:
    """Une police non embarquée, c'est un rendu qui dépend de la machine du
    lecteur — et pour une base-14, un encodage Latin-1 qui reperdrait les
    caractères récupérés plus haut. reportlab en réintroduit une par la porte
    de service : sans `initialFontName`, il écrit un préambule « BT /F1 12 Tf »
    (Helvetica) sur CHAQUE page même si aucun caractère ne l'utilise."""
    doc = fitz.open(stream=_pdf_de(_build_parametre_interview(multiline=False)),
                    filetype="pdf")
    non_embarquees = sorted({
        police[3] for numero in range(doc.page_count)
        for police in doc[numero].get_fonts(full=False)
        if police[1] == "n/a" or police[0] == 0
    })
    assert not non_embarquees, f"police(s) déclarée(s) mais non embarquée(s) : {non_embarquees}"


def test_build_interview_pdf_un_seul_bord_gauche_par_page() -> None:
    """Trois bords gauche coexistaient sur la page 1 d'`itw2_parametre.pdf` :
    en-tête et pied de page à 20,0 mm (dessinés au canvas), texte et filets à
    22,1 mm (les 6 pt de padding du `Frame` reportlab n'étaient compensés nulle
    part), encadré à 25,0 mm (`colWidths=[160 * mm]` codé en dur)."""
    doc = fitz.open(stream=_pdf_de(_build_parametre_interview(multiline=False)),
                    filetype="pdf")
    page = doc[0]
    bords = {
        "pied de page (canvas)": _x_gauche_texte_mm(page, "Export entretien"),
        "titre du document (frame)": _x_gauche_texte_mm(page, "Entretien"),
        "corps de texte (frame)": _x_gauche_texte_mm(page, "Note simple."),
        "filet de titre (frame)": _x_gauche_filet_mm(page),
        "encadré de verbatim (flowable)": _x_gauche_encadre_mm(page),
    }
    assert set(bords.values()) == {_MARGE_MM}, bords


def test_build_interview_pdf_metadonnees_titre_auteur_langue_et_signets() -> None:
    """`title` et `author` valaient « (anonymous) », le document n'annonçait
    aucune langue et n'offrait aucun signet — sur un export qui dépasse
    couramment la dizaine de pages."""
    pdf_bytes = _pdf_de(_build_parametre_interview(multiline=False))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    assert doc.metadata["title"] == "Entretien — Jean Dupont"
    assert doc.metadata["author"] not in ("", None, "(anonymous)")
    assert doc.xref_get_key(doc.pdf_catalog(), "Lang") == ("string", "fr-FR")
    # Un signet par titre de niveau 1 : la trame de l'entretien est navigable.
    assert "Organisation" in [entree[1] for entree in doc.get_toc()]


def test_build_interview_pdf_pages_non_finales_sont_remplies() -> None:
    """Conséquence directe de l'encadré insécable : trop haut pour la place
    restante, il partait ENTIER à la page suivante et laissait derrière lui une
    page remplie à 40 % (154 mm de blanc), voire une page orpheline à 4 %
    (249 mm de blanc). Seule la dernière page a le droit de finir tôt."""
    doc = fitz.open(stream=_pdf_de(_interview_avec_verbatim(VERBATIM_5016)),
                    filetype="pdf")
    assert doc.page_count >= 2, "ce verbatim doit déborder sur une seconde page"
    for numero, page in enumerate(doc, start=1):
        if numero == doc.page_count:
            continue
        blanc = _blanc_en_pied_mm(page)
        assert blanc < 40, f"page {numero}/{doc.page_count} : {blanc:.0f} mm de blanc en pied"
