"""Aucun tronçon envoyé à l'IA ne dépasse le budget de mots.

Constat d'audit du hub (performance, 2026-09-09) : `_chunk_blocks` ne borne pas
un bloc surdimensionné — son docstring l'assumait (« un bloc seul plus long que
le budget forme son propre tronçon »). Un bloc de thème agrégeant tous les
entretiens part donc tel quel vers Ollama et peut dépasser `ollama_timeout()`.

Le contre-exemple correct vit dans le même dépôt : `ai_common.chunk_text_by_paragraph`
redécoupe explicitement tout paragraphe trop long, avec la garantie écrite
« AUCUN tronçon ne dépasse max_words » — posée le 2026-07-20 après le fameux
« tronçon trop volumineux » qui ignorait complètement `OLLAMA_CHUNK_MAX_WORDS`.

Ces tests échouent sur le code d'avant : un bloc de 500 mots avec un budget de
100 sortait tel quel.
"""
from __future__ import annotations

from app.services.synthese_ai import _chunk_blocks


def _mots(troncon: list[str]) -> int:
    return sum(len(b.split()) for b in troncon)


def test_un_bloc_plus_long_que_le_budget_est_redecoupe():
    """Le cas que le docstring d'origine ASSUMAIT, et qui coûtait un job."""
    enorme = " ".join(f"mot{i}" for i in range(500))
    troncons = _chunk_blocks([enorme], max_words=100)
    assert all(_mots(t) <= 100 for t in troncons), (
        f"tailles obtenues : {[_mots(t) for t in troncons]} — un troncon depasse "
        "le budget et partira tel quel vers Ollama"
    )
    # Rien n'est perdu au découpage : tous les mots sortent, dans l'ordre.
    recolle = " ".join(b for t in troncons for b in t)
    assert recolle.split() == enorme.split(), "le decoupage a perdu ou reordonne du texte"


def test_le_chemin_court_reste_inchange():
    """Un jeu de blocs qui tient dans le budget doit rendre UN seul tronçon —
    sinon on transformerait un appel IA en map-reduce pour rien."""
    troncons = _chunk_blocks(["un deux trois", "quatre cinq"], max_words=100)
    assert troncons == [["un deux trois", "quatre cinq"]]


def test_les_blocs_normaux_sont_toujours_groupes_sans_etre_coupes():
    """La propriété d'origine ne doit pas être perdue : un bloc qui TIENT dans
    le budget n'est jamais coupé en deux."""
    blocs = [" ".join(f"a{i}" for i in range(60)), " ".join(f"b{i}" for i in range(60))]
    troncons = _chunk_blocks(blocs, max_words=100)
    assert len(troncons) == 2, "deux blocs de 60 mots ne tiennent pas dans 100"
    assert troncons[0] == [blocs[0]] and troncons[1] == [blocs[1]], (
        "un bloc qui tenait dans le budget a ete coupe"
    )


def test_liste_vide():
    assert _chunk_blocks([], max_words=100) == [[]]


def test_chaque_fragment_garde_l_en_tete_de_son_bloc():
    """Découper sans réattribuer, c'est détruire la valeur de la synthèse.

    Trouvé par revue adversariale (2026-09-10, F1) : une première version
    déléguait à `chunk_text_by_paragraph`, mais ces blocs joignent leurs lignes
    par un simple saut de ligne — la fonction n'y voyait QU'UN paragraphe et
    retombait sur un découpage par MOTS. Mesuré alors sur un bloc de thème
    réaliste : l'en-tête ne survivait que dans 1 fragment sur 5, les coupures
    tombaient au milieu d'une phrase, et l'attribution « - Alice (DSI) : » se
    noyait. Le reduce fusionnait ensuite ces morceaux anonymes comme s'ils
    étaient des synthèses de thème."""
    bloc = "\n".join(
        ["=== THEME : Gouvernance ==="]
        + [f"  - Personne{i} (DSI) : une reponse de quelques mots ici" for i in range(12)]
    )
    troncons = _chunk_blocks([bloc], max_words=30)
    fragments = [f for t in troncons for f in t]
    assert len(fragments) > 1, "precondition : le bloc doit bien etre decoupe"
    for fragment in fragments:
        assert fragment.startswith("=== THEME : Gouvernance ==="), (
            f"fragment sans en-tete de theme : {fragment[:60]!r} — le reduce "
            "fusionnera des propos non attribues"
        )
    # Et le découpage tombe sur des frontières de LIGNES : aucune ligne coupée.
    lignes_origine = set(bloc.split("\n")[1:])
    for fragment in fragments:
        for ligne in fragment.split("\n")[1:]:
            assert ligne in lignes_origine, f"ligne coupee au milieu : {ligne!r}"


def test_une_ligne_seule_trop_longue_reste_bornee():
    """Le filet : une réponse fleuve sur UNE ligne ne peut pas être bornée par
    un découpage sur des frontières de lignes — elle passe par le découpage par
    mots, qui porte la garantie."""
    fleuve = " ".join(f"mot{i}" for i in range(200))
    bloc = f"=== THEME : Fleuve ===\n  - Alice : {fleuve}"
    troncons = _chunk_blocks([bloc], max_words=50)
    assert all(_mots(t) <= 50 for t in troncons), (
        f"tailles : {[_mots(t) for t in troncons]} — une ligne fleuve a echappe a la borne"
    )
