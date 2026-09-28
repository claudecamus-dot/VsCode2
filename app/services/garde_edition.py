"""Garde de régénération : refuser d'écraser en silence ce qu'un consultant a
édité à la main (ADR `docs/adr/0001-garde-regeneration-lignes-editees.md`).

Les HUIT surfaces régénérables de la mission passent par ce registre — c'est la
couture unique de la garde, et elle n'a pas de mode « sans garde ». Une neuvième
surface branchée dessus sans s'inscrire dans `SURFACES` lève un `KeyError` au
lieu d'être servie sans protection en silence.

Trois FORMES de marqueur, parce que les surfaces n'ont pas la même forme — et
non par goût de l'uniformité :

1. **Listes de lignes** (`mission_kpis`, `mission_risks`, `mission_maturites`,
   `mission_difficulties`) : un drapeau `edite` par ligne. `apply_*_result`
   réaffecte la collection, donc les lignes sont RECRÉÉES et le drapeau retombe
   à faux sans une ligne de code.
2. **Arbre axes / recommandations** : le même drapeau `edite`, sur les DEUX
   niveaux (l'intitulé d'un axe et les champs d'une recommandation sont deux
   autosaves distincts). `apply_recommendations_result` supprime puis recrée :
   même remise à zéro gratuite. Le compte est agrégé sur l'arbre — ce que le
   consultant voit comme « ses recommandations ».
3. **Enregistrements uniques** (synthèse globale, SWOT, executive summary) :
   AUCUNE colonne nouvelle. Ces trois modèles portent déjà
   `status ∈ {empty, generated, edited}`, posé à `edited` par l'autosave et remis
   à `generated` par `apply_*_result` — le marqueur existe, il se remet à zéro
   seul, en ajouter un second aurait dupliqué un état. Effet de bord utile : un
   contenu SAISI à la main sans génération préalable (`empty` -> `edited`) est
   protégé lui aussi, ce qu'un drapeau posé à la génération n'aurait pas couvert.

Couplage web assumé : le module porte aussi le parseur HTTP du champ de
formulaire `confirmer` (`_lire_confirmer` / `CONFIRMER`, d'où l'import de
`fastapi.Depends` et `Form`) — un seul parseur pour les huit routes gardées.
"""
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Form


@dataclass(frozen=True)
class Surface:
    """Une surface régénérable : comment compter ce qui est édité à la main, quoi
    dire au consultant, et le libellé du bouton qui repose la demande confirmée."""

    libelle: str
    compte: Callable[[object], int]
    message: Callable[[int], str]


def _compte_lignes(nom: str) -> Callable[[object], int]:
    """Lignes `edite` d'une collection de la mission. Accès DIRECT à `.edite` :
    un `getattr` avec défaut déguiserait une faute de frappe en « aucune ligne
    éditée » — le mode de défaillance de la garde ne doit pas être « éteinte »."""
    def compte(mission) -> int:
        return sum(1 for ligne in getattr(mission, nom) if ligne.edite)
    return compte


def _compte_arbre_recommandations(mission) -> int:
    n = 0
    for axe in mission.recommendation_axes:
        if axe.edite:
            n += 1
        n += sum(1 for reco in axe.recommendations if reco.edite)
    return n


def _compte_enregistrement(nom: str) -> Callable[[object], int]:
    """1 si l'enregistrement unique `nom` de la mission est à l'état `edited`.
    Absent (jamais créé) = rien à protéger."""
    def compte(mission) -> int:
        obj = getattr(mission, nom)
        return 1 if obj is not None and obj.status == "edited" else 0
    return compte


def _message_lignes(n: int) -> str:
    """Message HISTORIQUE des listes (inchangé depuis G) : au FÉMININ (« ligne »)."""
    return (f"{n} ligne{'s' if n > 1 else ''} éditée{'s' if n > 1 else ''} à la main "
            f"{'seront' if n > 1 else 'sera'} remplacée{'s' if n > 1 else ''} par la "
            "génération. Rien n'a été généré : confirmez pour remplacer.")


def _message_elements(n: int) -> str:
    """Variante au MASCULIN (« élément ») : un axe ou une recommandation édités
    ne sont pas des « lignes » à l'écran des recommandations."""
    return (f"{n} élément{'s' if n > 1 else ''} édité{'s' if n > 1 else ''} à la main "
            f"{'seront' if n > 1 else 'sera'} remplacé{'s' if n > 1 else ''} par la "
            "génération. Rien n'a été généré : confirmez pour remplacer.")


def _message_fixe(phrase: str) -> Callable[[int], str]:
    """Enregistrement unique : le compte ne vaut que 0 ou 1, l'annoncer n'apporte
    rien — la phrase nomme ce qui va être écrasé."""
    return lambda _n: (f"{phrase} Rien n'a été généré : confirmez pour remplacer.")


SURFACES: dict[str, Surface] = {
    # --- forme 1 : listes de lignes ---
    "kpis": Surface(
        libelle="Remplacer les indicateurs quand même",
        compte=_compte_lignes("kpis"), message=_message_lignes),
    "risks": Surface(
        libelle="Remplacer la matrice quand même",
        compte=_compte_lignes("risks"), message=_message_lignes),
    "maturites": Surface(
        libelle="Remplacer la grille quand même",
        compte=_compte_lignes("maturites"), message=_message_lignes),
    "difficulties": Surface(
        libelle="Remplacer les difficultés quand même",
        compte=_compte_lignes("difficulties"), message=_message_lignes),
    # --- forme 2 : arbre axes / recommandations ---
    "recommendations": Surface(
        libelle="Remplacer les recommandations quand même",
        compte=_compte_arbre_recommandations, message=_message_elements),
    # --- forme 3 : enregistrements uniques, marqués par `status == "edited"` ---
    "swot": Surface(
        libelle="Remplacer la SWOT quand même",
        compte=_compte_enregistrement("swot"),
        message=_message_fixe("La SWOT porte des modifications faites à la main, "
                              "que la génération remplacerait.")),
    "executive_summary": Surface(
        libelle="Remplacer l'executive summary quand même",
        compte=_compte_enregistrement("executive_summary"),
        message=_message_fixe("L'executive summary porte des modifications faites à "
                              "la main, que la génération remplacerait.")),
    "global_synthesis": Surface(
        libelle="Remplacer la synthèse globale quand même",
        compte=_compte_enregistrement("global_synthesis"),
        message=_message_fixe("La synthèse globale porte des modifications faites à "
                              "la main, que la génération remplacerait.")),
}


def garde_regeneration(mission, *, surface: str, confirmer: bool) -> dict | None:
    """`None` quand la génération peut suivre son cours ; sinon le bloc de refus
    (`message` à afficher, `libelle` du bouton de confirmation) — l'appelant rend
    alors son écran SANS rien générer.

    `surface` est un mot-clé OBLIGATOIRE et doit être une clé de `SURFACES` :
    pas de valeur par défaut, pas de mode dégradé."""
    s = SURFACES[surface]
    n = s.compte(mission)
    if not n or confirmer:
        return None
    return {"message": s.message(n), "libelle": s.libelle}


def _lire_confirmer(confirmer: str = Form("")) -> bool:
    """Seul `confirmer=1` -- la valeur que pose le bouton de confirmation -- vaut
    confirmation. Lu comme CHAÎNE : typé `bool`, le champ laissait FastAPI répondre
    un 422 JSON brut (`bool_parsing`) sur `confirmer=2` ou `confirmer=oui`, page
    montrée telle quelle au consultant. Toute autre valeur (`true` compris) est un
    refus ordinaire : la garde se déclenche et rend son écran, rien n'est généré."""
    return confirmer.strip() == "1"


# Défaut partagé des HUIT routes de génération gardées (`confirmer: bool =
# CONFIRMER`) : un seul parseur, pas huit copies.
CONFIRMER = Depends(_lire_confirmer)
