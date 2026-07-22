"""Seed d'un jeu de démonstration (P5a-2) : une mission `is_demo=True` réaliste
— 10 entretiens structurés (interlocuteurs variés, réponses, verbatims) + tout le
contenu de restitution (synthèse globale, SWOT, executive summary, difficultés,
verbatims retenus, axes & recommandations) — de quoi montrer TOUT le parcours et
générer le deck sans IA ni saisie.

Cloisonné du réel par le flag `Mission.is_demo` (modèle VSCode1, cf.
docs/reflexions/espace-demo-reel.md) : ne touche JAMAIS une mission réelle. Rejouable
— supprime d'abord la mission démo homonyme existante (données démo reproductibles).

Usage (depuis la racine du dépôt, venv activé) :
    python scripts/seed_demo.py
    APP_DB_PATH=data/demo.db python scripts/seed_demo.py   # cible une base précise
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Answer, GlobalSynthesis, Interview, Mission, MissionDifficulty,
    MissionExecutiveSummary, MissionSwot, Question, Recommendation,
    RecommendationAxis, Theme, Trame, Verbatim,
)

DEMO_NAME = "DÉMO — Audit Data & Organisation"

# 3 thèmes × 2 questions.
TRAME = [
    ("Gouvernance de la donnée", [
        "Comment décririez-vous la gouvernance de la donnée aujourd'hui ?",
        "Qui décide des priorités data, et comment ?",
    ]),
    ("Organisation & collaboration", [
        "Comment collaborent les métiers et la DSI au quotidien ?",
        "Où se situent les principaux points de friction ?",
    ]),
    ("Culture & compétences", [
        "Quelles compétences data vous manquent le plus ?",
        "Comment la donnée est-elle utilisée dans les décisions ?",
    ]),
]

# 10 interlocuteurs (nom, rôle, entité).
PERSONNES = [
    ("Camille Laurent", "Chief Data Officer", "DSI Groupe"),
    ("Samir Benali", "Directeur des Systèmes d'Information", "DSI Groupe"),
    ("Aurélie Nguyen", "Responsable Data Engineering", "DSI Groupe"),
    ("Thomas Meyer", "Product Owner", "Métier Marketing"),
    ("Fatou Diallo", "Responsable Conformité", "Direction Juridique"),
    ("Julien Roche", "Analyste BI", "Direction Financière"),
    ("Marie Lefevre", "Directrice Marketing", "Métier Marketing"),
    ("Pierre Garnier", "RSSI", "DSI Groupe"),
    ("Nadia Haddad", "Responsable RH SIRH", "Direction RH"),
    ("Antoine Dubois", "Architecte Data", "DSI Groupe"),
]

# Réponses par thème (pool varié, choisi par modulo sur l'index d'entretien).
REPONSES = {
    0: [  # Gouvernance
        "La gouvernance est encore informelle : chaque équipe gère ses données dans son coin.",
        "On a un début de catalogue mais personne n'en est vraiment responsable.",
        "Les règles existent sur le papier mais ne sont pas appliquées faute de sponsor.",
        "Les décisions data remontent au COMEX sans instance dédiée, donc c'est lent.",
        "Les priorités changent au gré des projets, pas d'arbitrage clair.",
    ],
    1: [  # Organisation
        "Métiers et DSI travaillent en silos : on se parle surtout quand ça casse.",
        "Les demandes passent par des tickets, avec des délais qui découragent.",
        "Quand une squad mixte est montée, ça marche bien — mais c'est rare.",
        "La friction principale, c'est la priorisation : tout le monde est prioritaire.",
        "On manque d'un langage commun entre data engineers et métiers.",
    ],
    2: [  # Culture
        "Il nous manque des profils data engineering et de la culture produit.",
        "La donnée sert surtout au reporting, peu à la décision en amont.",
        "Les équipes sont solides techniquement mais peu outillées côté self-service.",
        "On décide encore beaucoup à l'intuition, la donnée arrive après coup.",
        "L'appétence est là, il manque la formation et le temps.",
    ],
}

# Pool de verbatims percutants (assignés 1-2 par entretien).
VERBATIMS = [
    "On a des données partout, mais personne ne sait qui en est responsable.",
    "Les équipes sont solides, c'est l'organisation qui coince.",
    "Quand on monte une squad mixte, tout va plus vite — mais c'est l'exception.",
    "On décide à l'intuition, la donnée arrive pour justifier après coup.",
    "Le catalogue existe, mais c'est un cimetière : personne ne le met à jour.",
    "Entre le métier et la DSI, on n'a pas le même vocabulaire.",
    "Notre vrai frein, ce n'est pas la techno, c'est la priorisation.",
    "On passe plus de temps à extraire la donnée qu'à l'analyser.",
]


def _reset_demo(db) -> None:
    """Supprime la/les mission(s) démo homonyme(s) — rejouable, jamais le réel."""
    for m in db.query(Mission).filter(
        Mission.is_demo.is_(True), Mission.name == DEMO_NAME
    ).all():
        db.delete(m)
    db.commit()


def seed() -> int:
    init_db()
    db = SessionLocal()
    try:
        _reset_demo(db)

        mission = Mission(name=DEMO_NAME, is_demo=True, is_draft=False,
                          description="Jeu de démonstration — 10 entretiens simulés.")
        db.add(mission)
        db.flush()

        trame = Trame(mission_id=mission.id, name="Trame d'audit Data & Organisation")
        db.add(trame)
        db.flush()
        questions: list[Question] = []
        q_theme: list[int] = []  # index de thème de chaque question (parallèle à `questions`)
        for ti, (theme_title, qs) in enumerate(TRAME):
            theme = Theme(trame_id=trame.id, title=theme_title, position=ti)
            db.add(theme)
            db.flush()
            for qi, label in enumerate(qs):
                q = Question(theme_id=theme.id, label=label, position=qi)
                db.add(q)
                db.flush()
                questions.append(q)
                q_theme.append(ti)

        verbatims: list[Verbatim] = []
        for pi, (nom, role, entite) in enumerate(PERSONNES):
            iv = Interview(
                mission_id=mission.id, interviewee_name=nom, interviewee_role=role,
                interviewee_entity=entite, interview_date=date(2026, 7, 1 + pi % 20),
                status="done", mode="parametre",
            )
            db.add(iv)
            db.flush()
            for qi, q in enumerate(questions):
                pool = REPONSES[q_theme[qi]]
                db.add(Answer(interview_id=iv.id, question_id=q.id,
                              text=pool[(pi + qi) % len(pool)], status="answered"))
            # 1 à 2 verbatims par entretien
            for j in range((pi % 2) + 1):
                v = Verbatim(interview_id=iv.id, question_id=questions[(pi + j) % len(questions)].id,
                             quote=VERBATIMS[(pi + j) % len(VERBATIMS)])
                db.add(v)
                db.flush()
                verbatims.append(v)

        # --- Contenu de restitution (niveau mission) ---
        db.add(GlobalSynthesis(
            mission_id=mission.id, status="generated",
            contexte="- DSI de 120 personnes sur 3 sites\n- Transformation data lancée il y a 18 mois\n- 10 entretiens menés sur 3 directions",
            culture_adn="- Forte culture d'ingénierie et d'autonomie\n- Attachement à la qualité technique\n- Appétence data réelle mais peu outillée",
            forces_succes="- Expertise technique reconnue\n- Équipes engagées\n- Premiers succès sur les squads mixtes",
            points_amelioration="- Gouvernance data informelle, sans responsable\n- Silos métiers / DSI\n- Priorisation illisible\n- Dette sur le socle historique",
            aspirations="- Une DSI partenaire du métier\n- Décider sur la donnée, pas à l'intuition\n- Un catalogue vivant et des data owners clairs",
        ))
        db.add(MissionSwot(
            mission_id=mission.id, status="generated",
            forces="- Expertise technique reconnue\n- Équipes engagées et autonomes",
            faiblesses="- Gouvernance data immature\n- Silos métiers / DSI\n- Priorisation illisible",
            opportunites="- Marché data en forte croissance\n- Sponsor exécutif mobilisé\n- Squads mixtes qui font leurs preuves",
            menaces="- Turn-over des profils rares\n- Pression réglementaire (RGPD)\n- Dette technique croissante",
        ))
        db.add(MissionExecutiveSummary(
            mission_id=mission.id, status="generated",
            headline="Une DSI experte mais freinée par son organisation",
            points="- Gouvernance data encore informelle (10/10 entretiens)\n- Silos métiers / DSI relevés sur les 3 directions\n- Un socle humain solide sur lequel bâtir",
            key_message="Clarifier la gouvernance avant d'outiller",
        ))
        diffs = [
            MissionDifficulty(mission_id=mission.id, position=0,
                              label="Les responsabilités sur la donnée ne sont pas clairement attribuées",
                              verbatim_id=verbatims[0].id if verbatims else None),
            MissionDifficulty(mission_id=mission.id, position=1,
                              label="Les silos entre métiers et DSI ralentissent chaque décision",
                              verbatim_id=verbatims[1].id if len(verbatims) > 1 else None),
            MissionDifficulty(mission_id=mission.id, position=2,
                              label="La priorisation illisible démobilise les équipes"),
        ]
        db.add_all(diffs)

        mission.restitution_verbatim_ids = [v.id for v in verbatims[:4]]

        ax1 = RecommendationAxis(mission_id=mission.id, title="Clarifier la gouvernance de la donnée", position=0)
        ax2 = RecommendationAxis(mission_id=mission.id, title="Rapprocher métiers et DSI", position=1)
        db.add_all([ax1, ax2])
        db.flush()
        db.add_all([
            Recommendation(axis_id=ax1.id, title="Nommer des data owners par domaine",
                           objectif="Attribuer une responsabilité claire sur chaque jeu de données",
                           acteurs="DSI, métiers", valeur=5, complexite=2, position=0),
            Recommendation(axis_id=ax1.id, title="Instaurer un comité data mensuel",
                           objectif="Arbitrer les priorités data au bon niveau",
                           acteurs="COMEX", valeur=4, complexite=2, position=1),
            Recommendation(axis_id=ax2.id, title="Généraliser les squads produit mixtes",
                           objectif="Casser les silos par des équipes pluridisciplinaires",
                           acteurs="DSI, métiers", valeur=4, complexite=4, position=0),
            Recommendation(axis_id=ax2.id, title="Mettre en place un langage data commun",
                           objectif="Un glossaire et des indicateurs partagés métiers/DSI",
                           acteurs="CDO", valeur=3, complexite=2, position=1),
        ])

        db.commit()
        print(f"OK — mission démo #{mission.id} « {mission.name} » : "
              f"{len(PERSONNES)} entretiens, {len(verbatims)} verbatims, is_demo=True")
        return mission.id
    finally:
        db.close()


if __name__ == "__main__":
    seed()
