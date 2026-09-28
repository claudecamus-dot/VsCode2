"""Matière (réponses + verbatims + répartition libre) dérivée des entretiens
d'une mission — pure lecture ORM, aucun appel IA. Service partagé par
`routers/synthese.py` (génération/aperçu), `routers/export.py` (SWOT,
executive summary, difficultés) et `services/mission_export.py` (export
markdown), qui les importaient jusqu'au 2026-09-04 comme symboles PRIVÉS de
`routers/synthese.py` — un couplage inversé service→router relevé par
l'audit technique (categorie risque_technique, finding
"routers/synthese.py = service déguisé") : `mission_export.py` ne pouvait pas
être testé/réutilisé sans charger tout le router. Extrait ici sans
changement de logique.
"""
from __future__ import annotations

from ..models import Mission, Theme


def theme_material(mission: Mission, theme: Theme) -> tuple[dict, list]:
    """Réponses (par question) et verbatims du thème, tous entretiens confondus."""
    qids = {q.id for q in theme.questions}
    by_question: dict[int, list[dict]] = {}
    verbatims: list[dict] = []
    for iv in mission.interviews:
        ans = {a.question_id: a for a in iv.answers}
        for q in theme.questions:
            a = ans.get(q.id)
            content = a and ((a.text or "").strip() or (a.value or "").strip())
            if content:
                by_question.setdefault(q.id, []).append(
                    {
                        "interviewee": iv.interviewee_name,
                        "role": iv.interviewee_role,
                        "text": (a.text or "").strip(),
                        "value": (a.value or "").strip(),
                    }
                )
        for v in iv.verbatims:
            if v.question_id in qids:
                verbatims.append(
                    {"interviewee": iv.interviewee_name, "quote": v.quote}
                )
    return by_question, verbatims


def answer_count(by_question: dict[int, list[dict]]) -> int:
    return sum(len(v) for v in by_question.values())


def all_theme_material(mission: Mission) -> list[tuple[Theme, dict, list]]:
    """Matière (réponses + verbatims) de tous les thèmes de la trame — pour
    la synthèse globale, qui recoupe l'ensemble de la mission plutôt qu'un
    seul thème. Une mission brouillon née d'un entretien libre (incr.9) n'a
    pas de trame du tout.

    MESURÉ le 2026-09-10, et volontairement NON optimisé. Le constat d'audit
    performance du 2026-09-09 relève « 8 sites d'appel dans export.py, sans
    aucun cache applicatif ».

    Ce qui a été mesuré, sur une COPIE de l'installation réelle, plus grosse
    mission (10 entretiens), session neuve : UN appel coûte 26 requêtes et 8 ms.
    Commande : copier `data/app.db`, poser `APP_DB_PATH` dessus, écouter
    `before_cursor_execute` sur l'engine et chronométrer `all_theme_material`.

    Ce qui NE justifie PAS la décision, et qui a d'abord été écrit ici : « huit
    appels d'affilée coûtent 25 requêtes au total ». Huit appels ne peuvent pas
    coûter moins qu'un seul — les deux chiffres venaient de deux états de
    session différents et ne se comparaient pas (revue adversariale du
    2026-09-10, F8).

    La vraie raison de ne pas optimiser est plus simple : les 6 sites d'appel
    réels (`export.py`, `synthese.py` x3, `mission_export.py`,
    `global_synthesis_job.py`) vivent dans des routes DISTINCTES. Une requête
    HTTP en déclenche un, parfois deux — jamais huit. Il n'y a donc quasiment
    pas de répétition à absorber, et 26 requêtes / 8 ms par requête HTTP sont
    négligeables devant l'appel IA qui suit.

    Ce que la mesure ne voit PAS, et qu'il faut savoir avant de la rouvrir : le
    travail réellement répété est côté Python — `theme_material` reconstruit
    son index `{question_id: answer}` pour chaque couple thème x entretien. Un
    comptage de requêtes est aveugle à ce coût-là. Si cette fonction redevient
    un sujet, c'est par là qu'il faudra la mesurer.
    """
    if mission.trame is None:
        return []
    return [
        (theme, *theme_material(mission, theme)) for theme in mission.trame.themes
    ]


def libre_material(mission: Mission) -> list[tuple]:
    """Répartition (5 catégories) de chaque entretien en mode libre (incr.9,
    US9.6) — matière indépendante des thèmes, injectée à côté de
    `material_by_theme` dans `generate_global_synthesis`."""
    return [
        (iv, iv.repartition) for iv in mission.interviews
        if iv.mode == "libre" and iv.repartition
    ]


def total_answer_count(material_by_theme: list[tuple[Theme, dict, list]]) -> int:
    return sum(answer_count(by_question) for _theme, by_question, _v in material_by_theme)


def libres_sans_analyse(mission: Mission) -> list:
    """Entretiens libres SANS répartition : invisibles de la synthèse globale
    (`libre_material` les écarte), alors que l'écran de la mission les affiche
    « Terminé ». Constat du 2026-09-28 sur missions réelles (16, 19, 22) :
    « rien à synthétiser » sans aucun renvoi vers l'étape d'analyse manquante."""
    return [
        iv for iv in mission.interviews
        if iv.mode == "libre" and not iv.repartition
    ]


def brouillons_contributifs(mission: Mission) -> list:
    """Entretiens encore en brouillon dont la matière ENTRE malgré tout dans la
    synthèse — `theme_material`/`libre_material` ne filtrent pas sur `status`
    (comportement conservé : décision produit du 2026-09-28, signaler plutôt
    qu'exclure en silence). Sert au bandeau d'avertissement du panneau."""
    contributifs = []
    for iv in mission.interviews:
        if iv.status == "done":
            continue
        a_des_reponses = any(
            (a.text or "").strip() or (a.value or "").strip() for a in iv.answers
        )
        if a_des_reponses or (iv.mode == "libre" and iv.repartition):
            contributifs.append(iv)
    return contributifs
