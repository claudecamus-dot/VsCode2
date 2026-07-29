"""Test d'intégration RÉEL contre Ollama (opt-in) — le garde-fou anti-récidive du
bug du 2026-07-22.

Contexte : le modèle défaut `llama3.1:8b` rendait 0 tour ou timeoutait (>5min) sur
le poste CPU cible — l'extraction « tours de parole » d'un entretien libre ne
fonctionnait donc PAS (rien en automatique dans l'onglet Répartition, timeout au
clic « Continuer »). **Invisible pour tous les autres tests**, qui monkeypatchent
`call_ai_json`/`extract_turns_from_text` : ils prouvent la plomberie, jamais que le
modèle réellement configuré produit quelque chose. Ce test exerce le VRAI appel
Ollama avec le modèle réellement configuré par défaut (`active_model()`), sur un
échange Q/R réaliste.

Auto-skippé si Ollama n'est pas joignable (CI, poste sans Ollama) ou si le
fournisseur actif n'est pas ollama : il n'échoue jamais faute d'infra. Il échoue
si le modèle défaut est inutilisable pour cette tâche — ce qui est précisément la
régression à empêcher. **À lancer explicitement dès qu'on touche au modèle, au
prompt ou au chunking d'extraction** (cf. revue-increment §2) :

    pytest tests/test_ollama_integration.py -v -s
"""
from __future__ import annotations

import time
import urllib.request

import pytest

from app.services import ai_common
from app.services.synthese_ai import generate_global_synthesis
from app.services.interview_libre_extract_ai import (
    extract_turns_from_text,
    generate_repartition_from_turns,
)


def _ollama_reachable() -> bool:
    if ai_common.active_provider() != "ollama":
        return False
    try:
        with urllib.request.urlopen(f"{ai_common.ollama_host()}/api/tags", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _ollama_reachable(),
    reason="Ollama non joignable (ou provider != ollama) — intégration réelle skippée.",
)

# Un vrai échange consultant / interviewé, avec identité en début (comme un vrai
# début d'entretien libre) — la matière minimale qu'un modèle correct doit savoir
# structurer. Volontairement court : on teste la CORRECTION du modèle défaut, pas
# la mise à l'échelle (couverte par le chunking, testé à part en mocké).
_REALISTIC_TRANSCRIPT = """Consultant : Bonjour, merci de nous recevoir. Pouvez-vous vous présenter ?
Marc Dubois : Bien sûr, je m'appelle Marc Dubois, je suis responsable du service informatique depuis huit ans et j'encadre douze personnes.
Consultant : Quels sont selon vous les principaux défis de votre organisation ?
Marc Dubois : Le principal problème, c'est la communication entre les équipes métier et la DSI. On refait souvent deux fois le même travail et les décisions traînent.
Consultant : Pouvez-vous me donner un exemple concret ?
Marc Dubois : Oui, l'an dernier la refonte du site client nous a fait perdre trois mois rien que sur le périmètre."""


def test_extract_turns_real_ollama_is_nonempty_and_not_catastrophically_slow() -> None:
    """Le modèle défaut RÉELLEMENT configuré doit extraire des tours (> 0) sur un
    échange Q/R réaliste, en un temps raisonnable. Le bug du 2026-07-22 (défaut
    llama3.1:8b -> 0 tour / >5min sur CPU) aurait fait échouer CE test — c'est son
    unique raison d'être."""
    print(f"\n[integration] provider={ai_common.active_provider()} model={ai_common.active_model()}")
    t0 = time.time()
    result = extract_turns_from_text(_REALISTIC_TRANSCRIPT)
    dt = time.time() - t0
    turns = result["turns"]
    print(f"[integration] extract_turns : {dt:.1f}s, {len(turns)} tours, "
          f"identité={result['identity'].get('interviewee_name')!r}")

    assert turns, (
        "0 tour extrait sur un échange Q/R réaliste — le modèle défaut "
        f"({ai_common.active_model()}) est inutilisable pour cette tâche "
        "(régression du bug 2026-07-22)."
    )
    # Chaque tour a un interlocuteur et au moins une question ou une remarque.
    assert all(t["interlocuteur"] and (t["question"] or t["remarque"]) for t in turns)
    # Garde-fou lenteur catastrophique : un si court échange ne doit pas approcher
    # OLLAMA_TIMEOUT. qwen2.5:3b ≈ 25s ; llama3.1:8b dépassait largement.
    assert dt < 120, (
        f"Extraction anormalement lente ({dt:.0f}s) pour un échange court — "
        f"le modèle défaut ({ai_common.active_model()}) est trop lourd pour ce poste."
    )


def test_repartition_real_ollama_is_nonempty() -> None:
    """Étape 2 (répartition dans les 5 catégories + résumé) : le modèle défaut doit
    produire un résumé non vide à partir de quelques tours — même garde-fou."""
    turns = [
        {"interlocuteur": "Marc Dubois", "question": None,
         "remarque": "La communication entre le métier et la DSI est notre principal problème.",
         "section_title": "Organisation"},
        {"interlocuteur": "Marc Dubois", "question": None,
         "remarque": "L'entraide entre collègues est une vraie force quand il y a un incident.",
         "section_title": None},
    ]
    t0 = time.time()
    result = generate_repartition_from_turns(turns)
    dt = time.time() - t0
    print(f"\n[integration] repartition : {dt:.1f}s, resume={result['resume'][:60]!r}")
    assert result["resume"].strip(), "Résumé vide — modèle défaut inutilisable pour la répartition."
    assert dt < 120


def test_extract_turns_real_ollama_keeps_content_on_unlabeled_monologue() -> None:
    """Régression 2026-07-28 : contrairement à `_REALISTIC_TRANSCRIPT` ci-dessus
    (qui porte déjà des étiquettes explicites « Consultant : » / « Marc Dubois : »),
    un vrai transcript Whisper n'a JAMAIS d'étiquette de locuteur — c'est
    exactement ce qui a fait échouer le pipeline en conditions réelles : sur un
    extrait audio réel (monologue de présentation, `tests/exemple/split_02_petit_30s.weba`
    transcrit par Whisper), le modèle défaut inventait une question de
    consultant absente du texte et perdait le propos réel dans 2 essais réels
    sur 3. Ce test fige ce texte réel (sans étiquette) en dur pour ne pas
    dépendre de Whisper ici, et vérifie que le contenu réel survit."""
    transcript = (
        "Michel Nakache, je fais partie de l'équipe COCO, Conseil Comex, et "
        "ma majeure, c'est les dynamiques humaines et interactionnelles, "
        "relationnelles. Donc, sur tout ce qui est transformation digitale, "
        "plutôt la dimension humaine, voilà. Et je vous parlerai de quelques "
        "utilisations de l'IA à ma petite échelle. Super, merci Michel."
    )
    t0 = time.time()
    result = extract_turns_from_text(transcript)
    dt = time.time() - t0
    captured = " ".join(
        (t.get("question") or "") + " " + (t.get("remarque") or "")
        for t in result["turns"]
    )
    print(f"\n[integration] monologue non étiqueté : {dt:.1f}s, {len(result['turns'])} tours, "
          f"identité={result['identity'].get('interviewee_name')!r}, "
          f"nom capté={'Nakache' in captured or result['identity'].get('interviewee_name') == 'Michel Nakache'}")
    # Le nom n'est PAS asserté ici : l'extraction d'identité reste best-effort
    # (déjà le cas avant ce correctif) et l'humain la corrige de toute façon à
    # l'écran de revue — la régression réelle du 2026-07-28 portait sur la
    # PERTE DU PROPOS, pas sur la fidélité de l'identité. Voir le print
    # ci-dessus pour un signal de dérive si le nom disparaît systématiquement.
    #
    # Le cœur du propos réel (dynamiques humaines / transformation digitale /
    # IA) doit survivre quelque part dans les tours — pas juste une question
    # de consultant inventée avec le contenu réel jeté.
    assert "humain" in captured.lower() or "digital" in captured.lower() or "IA" in captured, (
        f"Le propos réel de l'interviewé a disparu du résultat capté : {captured!r}"
    )


class _Axe:
    """Axe d'étude minimal — `generate_global_synthesis` n'a besoin que de
    key/label/hint, pas d'une ligne en base (ce test ne touche aucune DB)."""

    def __init__(self, key: str, label: str, hint: str):
        self.key, self.label, self.hint = key, label, hint


def test_synthese_globale_remplit_toutes_les_rubriques() -> None:
    """Chaque rubrique demandée doit revenir REMPLIE quand la matière la
    concerne explicitement.

    Trou trouvé par la revue d'incrément du 2026-07-27 : les tests de la suite
    monkeypatchent `call_ai_json`, donc aucun ne voyait que le modèle défaut ne
    remplit en pratique que la ou les PREMIÈRES rubriques et rend les suivantes
    VIDES (0 caractère) — reproduit 3 fois, y compris sur des axes par défaut,
    avec une matière explicite sur chacune (dernière mesure du 2026-07-28 :
    `contexte=54c, points_amelioration=0c`). Résultat côté produit : l'onglet
    « IA intégrée » livrait une synthèse amputée sans que rien ne le signale.

    Cause réelle trouvée le 2026-07-28 en sondant le modèle : il PRODUISAIT bien
    la rubrique, mais sous forme de LISTE d'objets sous-thème/facteur, et la garde
    `str`-sinon-`""` de `synthese_ai._clean_global` la jetait en silence. Corrigée
    par l'aplatissement (`_coerce_bullets`) ; ce test en est la preuve RÉELLE
    (`points_amelioration` : 0 → 164 caractères sur le même échantillon). Le
    pendant mocké est `test_une_rubrique_rendue_en_liste_est_aplatie_et_non_jetee`,
    qui ne peut pas, lui, voir ce que le vrai modèle renvoie."""
    axes = [
        _Axe("contexte", "Contexte", "faits marquants du contexte"),
        _Axe("points_amelioration", "Points d'amélioration",
             "douleurs, tensions, ce qui bloque"),
    ]

    class _Mission:
        name = "Mission d'intégration"

    class _Interview:
        interviewee_name = "Marc Dubois"

    repartition = {
        "contexte": "- L'équipe est passée de 6 à 14 personnes en deux ans.",
        "points_amelioration": (
            "- Jira et trois tableurs Excel en parallèle ; les chiffres ne "
            "concordent jamais ; une demi-journée par semaine de "
            "réconciliation manuelle ; aucun référentiel commun."
        ),
    }
    t0 = time.time()
    result = generate_global_synthesis(
        _Mission(), [], [(_Interview(), repartition)], axes=axes
    )
    dt = time.time() - t0
    print(f"\n[integration] synthèse globale : {dt:.1f}s, "
          + ", ".join(f"{k}={len(v)}c" for k, v in result.items()))

    assert set(result) == {a.key for a in axes}
    for axe in axes:
        # `strip` des puces : le modèle rend parfois un « - » seul, qui passe
        # une garde `.strip()` naïve tout en n'étant PAS du contenu.
        contenu = (result.get(axe.key) or "").strip(" -\n\t")
        assert len(contenu) >= 20, (
            f"Rubrique « {axe.label} » quasi vide ({result.get(axe.key)!r}) alors "
            f"que la matière la concerne explicitement — le modèle défaut "
            f"({ai_common.active_model()}) ne remplit qu'une partie des rubriques."
        )
