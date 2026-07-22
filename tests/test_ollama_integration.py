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
