"""Modèle de données — incrément 1.

Hiérarchie : Mission -> Trame -> Theme -> Question.
Les entités Interview / Synthèse / Deck seront greffées aux incréments
suivants (une Answer pointera vers Question, etc.) — le modèle est conçu
pour les accueillir sans refonte.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

# Types de questions supportés (US1.2)
QUESTION_TYPES = ("open", "scale", "choice")
QUESTION_TYPE_LABELS = {
    "open": "Ouverte",
    "scale": "Échelle",
    "choice": "Choix",
}

# Statut de couverture d'une reponse pendant l'entretien (US2.3/zap).
# to_review : pre-remplie par extraction IA depuis un document, pas encore
# validee par l'interviewer.euse (import d'entretien).
ANSWER_STATUSES = ("pending", "answered", "skipped", "revisit", "to_review")
ANSWER_STATUS_LABELS = {
    "pending": "À poser",
    "answered": "Répondue",
    "skipped": "Non posée",
    "revisit": "À revoir",
    "to_review": "Extraite du document — à valider",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    # Chemin (relatif à data/pptx_templates/) du template PPT client uploadé,
    # utilisé comme base pour l'export PPT (évol) — hérite thème/masters.
    pptx_template_path: Mapped[str | None] = mapped_column(String(500), default=None)
    # Mission créée implicitement depuis l'écran d'entrée « entretien libre »
    # ou « entretien structuré » (incr.9) avant que son identité réelle ne
    # soit connue — nom provisoire, à compléter ou à rattacher à une mission
    # existante via /missions/{id}/finaliser. Une mission "classique" (choix
    # « nouvelle mission ») ne passe jamais par cet état.
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False)
    # Verbatims (`Verbatim.id`) sélectionnés pour la planche « Paroles d'acteurs »
    # de la restitution (Palier 2, 2026-07-21) — approche légère : on référence
    # des `Verbatim` déjà en base par id plutôt qu'un nouveau modèle de citation.
    # Filtré à l'affichage sur les verbatims encore existants (ids périmés ignorés).
    restitution_verbatim_ids: Mapped[list] = mapped_column(JSON, default=list)

    # Une mission possède au plus une trame (1:1). Absente pour une mission
    # brouillon née d'un entretien libre (incr.9, `is_draft`) tant qu'aucune
    # trame ne lui a été rattachée.
    trame: Mapped["Trame | None"] = relationship(
        back_populates="mission",
        cascade="all, delete-orphan",
        uselist=False,
    )
    interviews: Mapped[list["Interview"]] = relationship(
        back_populates="mission",
        cascade="all, delete-orphan",
        order_by="Interview.created_at",
    )
    agent_results: Mapped[list["AgentResult"]] = relationship(
        back_populates="mission",
        cascade="all, delete-orphan",
        order_by="AgentResult.created_at.desc()",
    )
    global_synthesis: Mapped["GlobalSynthesis | None"] = relationship(
        back_populates="mission",
        cascade="all, delete-orphan",
        uselist=False,
    )
    swot: Mapped["MissionSwot | None"] = relationship(
        back_populates="mission",
        cascade="all, delete-orphan",
        uselist=False,
    )
    recommendation_axes: Mapped[list["RecommendationAxis"]] = relationship(
        back_populates="mission",
        cascade="all, delete-orphan",
        order_by="RecommendationAxis.position",
    )

    @property
    def all_verbatims(self) -> list["Verbatim"]:
        """Tous les verbatims de la mission (tous entretiens confondus) — la
        source de sélection de la planche « Paroles d'acteurs » (Palier 2). Les
        entretiens libres n'ont pas de `Verbatim` (ils ont des `InterviewTurn`),
        donc la liste ne contient que des citations d'entretiens structurés."""
        return [v for iv in self.interviews for v in iv.verbatims]

    @property
    def selected_verbatims(self) -> list["Verbatim"]:
        """Les verbatims retenus pour la restitution, dans l'ordre de sélection
        (`restitution_verbatim_ids`), en ignorant les ids périmés (verbatim
        supprimé depuis) — jamais de KeyError sur un id obsolète."""
        by_id = {v.id: v for v in self.all_verbatims}
        return [by_id[i] for i in (self.restitution_verbatim_ids or []) if i in by_id]


class Trame(Base):
    __tablename__ = "trames"

    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200), default="Trame d'entretien")
    # Introduction « Objectifs et principes » (importée du .docx), reprise en
    # tête de chaque entretien (évol).
    intro_text: Mapped[str | None] = mapped_column(Text, default=None)

    mission: Mapped["Mission"] = relationship(back_populates="trame")
    themes: Mapped[list["Theme"]] = relationship(
        back_populates="trame",
        cascade="all, delete-orphan",
        order_by="Theme.position",
    )


class Theme(Base):
    __tablename__ = "themes"

    id: Mapped[int] = mapped_column(primary_key=True)
    trame_id: Mapped[int] = mapped_column(
        ForeignKey("trames.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(Integer, default=0)

    trame: Mapped["Trame"] = relationship(back_populates="themes")
    questions: Mapped[list["Question"]] = relationship(
        back_populates="theme",
        cascade="all, delete-orphan",
        order_by="Question.position",
    )
    synthesis: Mapped["Synthesis | None"] = relationship(
        back_populates="theme",
        cascade="all, delete-orphan",
        uselist=False,
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    theme_id: Mapped[int] = mapped_column(
        ForeignKey("themes.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(Text)
    # Texte d'aide / contexte qui accompagne la question (exemples, amorce,
    # précisions) — importé du .docx ou saisi à la main.
    help_text: Mapped[str | None] = mapped_column(Text, default=None)
    # open | scale | choice
    qtype: Mapped[str] = mapped_column(String(20), default="open")
    # Paramètres selon le type : scale -> {min, max} ; choice -> {options: [...]}
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    position: Mapped[int] = mapped_column(Integer, default=0)

    theme: Mapped["Theme"] = relationship(back_populates="questions")

    @property
    def type_label(self) -> str:
        return QUESTION_TYPE_LABELS.get(self.qtype, self.qtype)


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE")
    )
    interviewee_name: Mapped[str] = mapped_column(String(200))
    interviewee_role: Mapped[str | None] = mapped_column(String(200), default=None)
    interviewee_entity: Mapped[str | None] = mapped_column(String(200), default=None)
    interview_date: Mapped[date | None] = mapped_column(Date, default=None)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|done
    # parametre : suit la trame de la mission (Answer/Question), comme avant
    # incr.9. libre : pas de trame, structuré en InterviewTurn (incr.9). Fixé
    # à la création, jamais exposé en modification ensuite (verrou serveur —
    # aucune route de mise à jour n'accepte ce champ).
    mode: Mapped[str] = mapped_column(String(20), default="parametre")
    # Répartition IA (mode libre uniquement) dans les 5 catégories de
    # `GlobalSynthesis` — mêmes clés que `synthese_ai.GLOBAL_SCHEMA`, éditée
    # par le consultant avant enregistrement. Consommée par
    # `_build_global_prompt` comme matière supplémentaire (canal
    # `material_libre`, à côté de `material_by_theme`).
    repartition: Mapped[dict] = mapped_column(JSON, default=dict)
    # Résumé court (1-3 phrases, mode libre) produit par la même extraction
    # IA que les tours/la répartition — sert d'intro à l'écran Synthèse.
    resume: Mapped[str | None] = mapped_column(Text, default=None)
    # Protocole / infos de référence à introduire pendant l'entretien (évol).
    reference_text: Mapped[str | None] = mapped_column(Text, default=None)
    free_notes: Mapped[str | None] = mapped_column(Text, default=None)
    # Chemin (relatif à data/recordings/) de la sauvegarde audio complète de
    # l'entretien enregistré — filet de sécurité en cas de souci de
    # transcription/extraction, l'audio brut n'étant sinon jamais conservé.
    audio_backup_path: Mapped[str | None] = mapped_column(String(500), default=None)
    # Liste ordonnée de tranches de 30min de la sauvegarde audio complète
    # ({"filename": str, "position": int}, mode libre uniquement) — au-delà
    # de 30min, `backupRecorder` (côté client) tourne comme le fait déjà le
    # `MediaRecorder` de transcription à 60s, pour ne jamais perdre plus
    # qu'une tranche en cas de crash. `audio_backup_path` ci-dessus continue
    # de pointer vers la DERNIÈRE tranche (rétrocompatible avec le lecteur
    # existant) ; cette liste sert au téléchargement de l'historique complet.
    audio_segments: Mapped[list] = mapped_column(JSON, default=list)
    # Transcription brute telle qu'enregistrée (mode parametre, flux
    # d'enregistrement audio uniquement — jamais rempli par l'import .docx,
    # où l'utilisateur garde déjà son fichier source). Avant ce champ, le
    # texte ne survivait que le temps du formulaire (perdu après extraction
    # IA des réponses) — aucun moyen de le consulter/exporter après coup.
    raw_transcript: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    mission: Mapped["Mission"] = relationship(back_populates="interviews")
    answers: Mapped[list["Answer"]] = relationship(
        back_populates="interview",
        cascade="all, delete-orphan",
    )
    verbatims: Mapped[list["Verbatim"]] = relationship(
        back_populates="interview",
        cascade="all, delete-orphan",
        order_by="Verbatim.created_at",
    )
    turns: Mapped[list["InterviewTurn"]] = relationship(
        back_populates="interview",
        cascade="all, delete-orphan",
        order_by="InterviewTurn.position",
    )


class InterviewTurn(Base):
    """Un tour de parole d'un entretien en mode libre (incr.9, US9.4) —
    interlocuteur/question/remarque, mis en forme par IA depuis la
    transcription puis revu/édité par le consultant. Indépendant de
    Trame/Theme/Question : un entretien libre n'a pas de trame."""

    __tablename__ = "interview_turns"

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    interlocuteur: Mapped[str] = mapped_column(String(200), default="")
    question: Mapped[str | None] = mapped_column(Text, default=None)
    remarque: Mapped[str | None] = mapped_column(Text, default=None)
    # Titre de section (incr.9, écran Analyse) : posé sur le tour qui ouvre
    # un nouveau sujet dans la conversation, vide sur les tours suivants qui
    # continuent la section en cours — reconstitué à l'affichage par
    # regroupement séquentiel (voir
    # interview_export.py::group_turns_into_sections), pas stocké de façon
    # dénormalisée sur chaque tour.
    section_title: Mapped[str | None] = mapped_column(String(300), default=None)

    interview: Mapped["Interview"] = relationship(back_populates="turns")


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint("interview_id", "question_id", name="uq_answer_interview_question"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE")
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE")
    )
    text: Mapped[str] = mapped_column(Text, default="")
    # Valeur structurée pour les questions 'choix'/'échelle' (option ou note).
    value: Mapped[str | None] = mapped_column(String(300), default=None)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    interview: Mapped["Interview"] = relationship(back_populates="answers")
    question: Mapped["Question"] = relationship()

    @property
    def status_label(self) -> str:
        return ANSWER_STATUS_LABELS.get(self.status, self.status)


class Verbatim(Base):
    """Citation mot-pour-mot relevée pendant l'entretien (US2.3).

    Rattachée à une question (donc à un thème) afin d'alimenter la synthèse
    transverse par thème et, plus tard, les encarts « citation » du deck.
    """

    __tablename__ = "verbatims"

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_id: Mapped[int] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE")
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE")
    )
    quote: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    interview: Mapped["Interview"] = relationship(back_populates="verbatims")
    question: Mapped["Question"] = relationship()


# Statut d'une synthèse de thème (incrément 3).
SYNTHESIS_STATUSES = ("empty", "generated", "edited")
SYNTHESIS_STATUS_LABELS = {
    "empty": "À générer",
    "generated": "Générée",
    "edited": "Éditée",
}


class Synthesis(Base):
    """Synthèse transverse d'un thème (incrément 3).

    Une synthèse par thème : agrège les réponses de tous les entretiens puis
    dégage convergences / divergences. Brouillon généré par IA (US4.2) puis
    éditable à la main (US4.3). Alimentera le plan de deck (incrément 4).
    """

    __tablename__ = "syntheses"
    __table_args__ = (
        UniqueConstraint("theme_id", name="uq_synthesis_theme"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    theme_id: Mapped[int] = mapped_column(
        ForeignKey("themes.id", ondelete="CASCADE")
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    convergences: Mapped[str] = mapped_column(Text, default="")
    divergences: Mapped[str] = mapped_column(Text, default="")
    # empty | generated | edited
    status: Mapped[str] = mapped_column(String(20), default="empty")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    theme: Mapped["Theme"] = relationship(back_populates="synthesis")

    @property
    def has_content(self) -> bool:
        return bool((self.summary or "").strip() or (self.convergences or "").strip() or (self.divergences or "").strip())


class GlobalSynthesis(Base):
    """Synthèse transverse à la mission, tous thèmes confondus (évol).

    Contrairement à `Synthesis` (par thème), regroupe les entretiens en 5
    catégories fixes — contexte, culture & ADN, forces/succès, points
    d'amélioration, aspirations — qui recoupent les thèmes de trame plutôt
    que de les suivre un à un. Alimente `Recommendation` (le pipeline
    recommandations part de cette synthèse, pas des réponses brutes).
    """

    __tablename__ = "global_syntheses"
    __table_args__ = (
        UniqueConstraint("mission_id", name="uq_global_synthesis_mission"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE")
    )
    contexte: Mapped[str] = mapped_column(Text, default="")
    culture_adn: Mapped[str] = mapped_column(Text, default="")
    forces_succes: Mapped[str] = mapped_column(Text, default="")
    points_amelioration: Mapped[str] = mapped_column(Text, default="")
    aspirations: Mapped[str] = mapped_column(Text, default="")
    # empty | generated | edited
    status: Mapped[str] = mapped_column(String(20), default="empty")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    mission: Mapped["Mission"] = relationship(back_populates="global_synthesis")

    @property
    def has_content(self) -> bool:
        return bool(
            (self.contexte or "").strip()
            or (self.culture_adn or "").strip()
            or (self.forces_succes or "").strip()
            or (self.points_amelioration or "").strip()
            or (self.aspirations or "").strip()
        )

    @property
    def status_label(self) -> str:
        return SYNTHESIS_STATUS_LABELS.get(self.status, self.status)


class MissionSwot(Base):
    """Matrice SWOT transverse à la mission (Palier 1 restitution, 2026-07-21).

    Dérivée de la synthèse globale déjà générée (comme les recommandations, pas
    des réponses brutes) — 4 quadrants libres : forces/faiblesses (regard
    INTERNE, proches de `forces_succes` / `points_amelioration`) et
    opportunités/menaces (regard EXTERNE : marché, concurrence, risques — non
    déductibles des 5 catégories internes, d'où une génération IA dédiée, cf.
    `docs/reflexions/restitution-mission.md`).
    """

    __tablename__ = "mission_swots"
    __table_args__ = (
        UniqueConstraint("mission_id", name="uq_mission_swot_mission"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE")
    )
    forces: Mapped[str] = mapped_column(Text, default="")
    faiblesses: Mapped[str] = mapped_column(Text, default="")
    opportunites: Mapped[str] = mapped_column(Text, default="")
    menaces: Mapped[str] = mapped_column(Text, default="")
    # empty | generated | edited
    status: Mapped[str] = mapped_column(String(20), default="empty")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    mission: Mapped["Mission"] = relationship(back_populates="swot")

    @property
    def has_content(self) -> bool:
        return bool(
            (self.forces or "").strip()
            or (self.faiblesses or "").strip()
            or (self.opportunites or "").strip()
            or (self.menaces or "").strip()
        )

    @property
    def status_label(self) -> str:
        return SYNTHESIS_STATUS_LABELS.get(self.status, self.status)


class RecommendationAxis(Base):
    """Axe de recommandation transverse à la mission (évol).

    Un petit nombre d'axes (3-4 dans la pratique) qui recoupent plusieurs
    thèmes de trame — pas un axe par thème. Chaque axe porte plusieurs
    `Recommendation`.
    """

    __tablename__ = "recommendation_axes"

    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(300))
    position: Mapped[int] = mapped_column(Integer, default=0)

    mission: Mapped["Mission"] = relationship(back_populates="recommendation_axes")
    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="axis",
        cascade="all, delete-orphan",
        order_by="Recommendation.position",
    )


class Recommendation(Base):
    """Fiche de recommandation — schéma calqué sur un rapport de restitution
    réel (Objectif / Acteurs / Critères de priorisation / Résultats
    attendus / Proposition de valeur / Plan d'actions)."""

    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    axis_id: Mapped[int] = mapped_column(
        ForeignKey("recommendation_axes.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(300))
    objectif: Mapped[str] = mapped_column(Text, default="")
    acteurs: Mapped[str] = mapped_column(String(300), default="")
    # 1 (faible) à 5 (fort)
    valeur: Mapped[int] = mapped_column(Integer, default=3)
    complexite: Mapped[int] = mapped_column(Integer, default=3)
    proposition_valeur: Mapped[str] = mapped_column(Text, default="")
    plan_actions: Mapped[str] = mapped_column(Text, default="")
    resultats_attendus: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)

    axis: Mapped["RecommendationAxis"] = relationship(back_populates="recommendations")

    @property
    def status_label(self) -> str:
        return SYNTHESIS_STATUS_LABELS.get(self.status, self.status)


class AgentResult(Base):
    """Résultat d'une invocation d'agent OpenHub pour une mission."""

    __tablename__ = "agent_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE")
    )
    agent_id: Mapped[str] = mapped_column(String(200))
    agent_label: Mapped[str] = mapped_column(String(200))
    runtime_available: Mapped[bool] = mapped_column(Boolean, default=False)
    output: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    mission: Mapped["Mission"] = relationship(back_populates="agent_results")


# Statuts d'un job de traitement de tranche (Palier 2, segmentation 30min).
SEGMENT_JOB_STATUSES = ("pending", "running", "done", "failed")


class InterviewSegmentJob(Base):
    """Job d'extraction des tours de parole d'UNE tranche de texte (Palier 2 —
    `docs/reflexions/enregistrement-segmente-30min.md` §4).

    Découple soumission et résultat pour un entretien libre long : pendant
    l'enregistrement, chaque tranche de ~30min de texte transcrit est soumise
    ici et traitée en tâche de fond (`extract_turns_from_text`) pendant que la
    tranche suivante s'enregistre — au lieu de tout traiter en une requête
    synchrone bloquée à l'arrêt (le mur des ~2h30 pour 3h, cf.
    `extraction-longue-duree.md`).

    Tant que l'entretien n'existe pas encore en base (le wizard libre ne crée
    l'`Interview` qu'à la confirmation finale), le job est rattaché à un
    `session_token` éphémère généré côté client au démarrage de
    l'enregistrement — même défi que `audio_segments`/`raw_transcript`, qui ne
    vivent en champ caché que le temps du wizard. `interview_id` reste NULL sur
    ce chemin (les jobs sont consommés puis deviennent inutiles dès l'écran de
    revue des tours) ; la colonne est prévue au cas où un rattachement
    a posteriori deviendrait utile.
    """

    __tablename__ = "interview_segment_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_token: Mapped[str] = mapped_column(String(64), index=True)
    interview_id: Mapped[int | None] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), default=None
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # Texte de la tranche, persisté (pas seulement passé en paramètre de la
    # tâche de fond) : un crash/redémarrage serveur entre la création du job
    # et l'exécution de `run_segment_job` ne perd plus le texte, et permet de
    # RE-traiter juste CETTE tranche (pas toute la transcription) si le job
    # échoue ou reste bloqué — voir `interview_segment_jobs.recover_stalled_or_failed_jobs`.
    text: Mapped[str] = mapped_column(Text, default="")
    # {"turns": [...], "identity": {...}} produit par extract_turns_from_text,
    # None tant que le job n'est pas `done`.
    turns_result: Mapped[dict | None] = mapped_column(JSON, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
