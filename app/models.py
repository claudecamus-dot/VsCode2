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

# Statut de couverture d'une réponse pendant l'entretien (US2.3/“je zap”).
ANSWER_STATUSES = ("pending", "answered", "skipped", "revisit")
ANSWER_STATUS_LABELS = {
    "pending": "À poser",
    "answered": "Répondue",
    "skipped": "Non posée",
    "revisit": "À revoir",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    client: Mapped[str | None] = mapped_column(String(200), default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # Une mission possède une trame (1:1 au MVP), créée avec la mission.
    trame: Mapped["Trame"] = relationship(
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
    # Protocole / infos de référence à introduire pendant l'entretien (évol).
    reference_text: Mapped[str | None] = mapped_column(Text, default=None)
    free_notes: Mapped[str | None] = mapped_column(Text, default=None)
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
