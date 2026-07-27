"""Axes d'étude d'une mission — les rubriques de la synthèse globale.

Avant le 2026-07-27, ces 5 axes (contexte, culture & ADN, forces & succès,
points d'amélioration, aspirations) étaient figés jusque dans le schéma :
5 colonnes de `GlobalSynthesis`, 5 clés du JSON demandé à l'IA, 5 rubriques du
gabarit d'export, 5 champs de l'écran. Une mission qui étudie autre chose
n'avait aucun moyen de le dire. Ils sont désormais des lignes
(`MissionSynthesisAxis`), modifiables par mission — renommables, ajoutables,
supprimables — et TOUT le reste s'y aligne : le prompt et le schéma JSON de la
synthèse IA, le gabarit d'export Markdown et son réimport, l'aperçu, le PPT, et
la répartition des entretiens libres.

Deux invariants tiennent l'ensemble :

1. `key` est fabriquée à la création et ne bouge JAMAIS. Le contenu est stocké
   par clé (`GlobalSynthesis.valeurs`, `Interview.repartition`) : renommer un
   axe conserve donc sa matière. Seule la suppression retire du contenu.
2. Une mission sans axe est semée avec les 5 valeurs historiques
   (`DEFAUTS`) — les missions existantes ne changent pas de comportement, et
   une base d'avant la migration reste lisible telle quelle.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Mission, MissionSynthesisAxis

__all__ = [
    "DEFAUTS",
    "axes_of",
    "creer_axe",
    "cle_libre",
    "supprimer_axe",
]

# Les 5 axes historiques : mêmes clés que les colonnes de `GlobalSynthesis` et
# que les clés de `Interview.repartition`, pour que rien ne bouge sur une
# mission déjà saisie. Les `hint` reprennent mot pour mot les descriptions qui
# étaient codées en dur dans le prompt système de `synthese_ai.GLOBAL_SYSTEM`.
DEFAUTS = (
    ("contexte", "Contexte",
     "faits marquants du contexte : organisation, historique, évènements "
     "récents qui éclairent la lecture du reste"),
    ("culture_adn", "Culture & ADN",
     "traits de culture observés, pratiques en place, ce qui définit « la "
     "façon de faire » ici"),
    ("forces_succes", "Forces & succès",
     "ce qui marche bien : leviers de succès, pratiques à préserver, sources "
     "de fierté ou de motivation revenues dans plusieurs entretiens"),
    ("points_amelioration", "Points d'amélioration",
     "douleurs, tensions, blocages — les axes d'amélioration qui reviennent le "
     "plus souvent, avec leur impact concret"),
    ("aspirations", "Aspirations (baguette magique)",
     "ce que les personnes espèrent ou changeraient si elles le pouvaient — la "
     "direction souhaitée, pas seulement les problèmes"),
)


def _slug(label: str) -> str:
    """Clé lisible dérivée du libellé — sert de base à `cle_libre`."""
    sans_accent = "".join(
        c for c in unicodedata.normalize("NFD", label)
        if unicodedata.category(c) != "Mn"
    )
    slug = re.sub(r"[^a-z0-9]+", "_", sans_accent.lower()).strip("_")
    return slug[:50]


def cle_libre(db: Session, mission: Mission, label: str) -> str:
    """Clé unique pour la mission, dérivée du libellé.

    Unique et STABLE : c'est l'adresse du contenu (`GlobalSynthesis.valeurs`).
    Une collision (deux axes de même nom, ou un axe recréé après suppression)
    est suffixée plutôt que réutilisée — réutiliser la clé d'un axe supprimé
    ressusciterait silencieusement son ancien contenu dans le nouvel axe.

    Les clés prises sont relues EN BASE, pas depuis `mission.synthesis_axes` :
    la relation chargée en session ne voit pas un axe ajouté juste avant, et
    deux créations successives produisaient alors la même clé (violation de
    contrainte d'unicité, attrapée par `tests/test_axes_etude.py`).
    """
    base = _slug(label) or "axe"
    prises = set(db.scalars(
        select(MissionSynthesisAxis.key).where(
            MissionSynthesisAxis.mission_id == mission.id
        )
    ))
    if base not in prises:
        return base
    n = 2
    while f"{base}_{n}" in prises:
        n += 1
    return f"{base}_{n}"


def axes_of(db: Session, mission: Mission) -> list[MissionSynthesisAxis]:
    """Axes de la mission, semés aux 5 défauts à la première lecture.

    Semis paresseux plutôt qu'à la création de la mission : les missions déjà
    en base (dont celles d'avant cette fonctionnalité) en héritent sans
    migration de données, et un axe supprimé ne repousse pas — une mission
    conserve au moins un axe (cf. `supprimer_axe`), donc la liste n'est jamais
    vide une fois semée.
    """
    if mission.synthesis_axes:
        return list(mission.synthesis_axes)
    for position, (key, label, hint) in enumerate(DEFAUTS):
        db.add(MissionSynthesisAxis(
            mission_id=mission.id, key=key, label=label, hint=hint, position=position,
        ))
    db.commit()
    db.refresh(mission)
    return list(mission.synthesis_axes)


def creer_axe(db: Session, mission: Mission, label: str, hint: str = "") -> MissionSynthesisAxis:
    axes = axes_of(db, mission)
    axe = MissionSynthesisAxis(
        mission_id=mission.id,
        key=cle_libre(db, mission, label),
        label=label.strip() or "Nouvel axe",
        hint=hint.strip(),
        position=(max((a.position for a in axes), default=-1) + 1),
    )
    db.add(axe)
    db.commit()
    # La relation en session est périmée dès qu'on ajoute/supprime une ligne
    # via `mission_id` : sans ce rafraîchissement, l'appel suivant travaille
    # sur une liste d'axes qui ignore celui-ci.
    db.refresh(mission)
    return axe


def supprimer_axe(db: Session, mission: Mission, axe: MissionSynthesisAxis) -> bool:
    """Supprime un axe. Refuse de vider la liste : une mission sans aucun axe
    n'aurait plus de synthèse du tout, et `axes_of` la resèmerait aux 5 défauts
    à la lecture suivante — la suppression du dernier axe se serait annulée
    toute seule, sans rien dire."""
    if len(axes_of(db, mission)) <= 1:
        return False
    db.delete(axe)
    db.commit()
    db.refresh(mission)  # même raison que dans `creer_axe`
    return True
