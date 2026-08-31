"""Tests de la skill pdf-quality.

Chaque test rejoue un défaut **mesuré** lors de l'audit par exécution réelle du
2026-08-31 (6 PDF produits, 22 pages rastérisées et regardées, chaîne VSCode2 /
reportlab). Les nombres cités dans les noms et les commentaires viennent de cet
audit, pas d'une estimation : le verbatim qui cassait faisait 12 690 caractères,
celui qui passait 5 016 ; les trois bords gauche relevés sur une même page
étaient 20,0 / 22,1 / 25,0 mm ; la page creuse était à 40 % avec 154 mm de blanc
et l'orpheline à 4 %.

Lancement :
    py -m pytest .claude/skills/pdf-quality/tests/test_pdf_quality.py -q \
        --basetemp=C:/tmp/pdfquality/pt

(``--basetemp`` court et hors %TEMP% : sur cette machine, une jonction morte
dans %TEMP% fait planter le teardown de pytest et rend un exit 1 sur une suite
pourtant verte.)
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from dataclasses import replace

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RACINE, "scripts"))

import pdf_report as PR  # noqa: E402
import pdf_verify as PV  # noqa: E402

from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfgen.canvas import Canvas  # noqa: E402
from reportlab.platypus import KeepTogether, Paragraph, Table  # noqa: E402

pytestmark = pytest.mark.skipif(
    not PV.FITZ_DISPONIBLE,
    reason=f"PyMuPDF absent, aucune mesure possible : {PV.FITZ_ERREUR}",
)

# --------------------------------------------------------------------------- #
# Matière première : les chaînes exactes qui ont cassé l'audit.
# --------------------------------------------------------------------------- #

VERBATIM_QUI_CASSAIT = 12690  # caractères — LayoutError + HTTP 500 chez VSCode2
VERBATIM_QUI_PASSAIT = 5016  # caractères — la limite basse constatée

NOM_VIETNAMIEN = "Nguyễn Thị Mai"  # devenait « NguyIn ThI Mai » en base-14
NOM_CYRILLIQUE = "Иванов Пётр Сергеевич"  # devenait des pavés noirs
FORMULE_GRECQUE = "Δ de maturité : α → β (σ = 0,42)"  # Δ remplacé par ∆ math
EMOJI = "🙂"  # aucune fonte monochrome ne le porte : doit être SIGNALÉ

GABARIT_TEST = replace(
    PR.GABARIT_REFERENCE,
    titre="Compte rendu d'entretien",
    auteur="Cabinet Exemple",
    sujet="Restitution — audit de la chaîne PDF",
    mots_cles="pdf-quality, audit, verbatim",
    entete_gauche="Compte rendu d'entretien",
    entete_droite="Cabinet Exemple",
    pied_gauche="Confidentiel",
    pied_droite="Page {page} / {total}",
)


def _verbatim(longueur: int) -> str:
    """Un verbatim d'entretien de longueur exacte, avec de vrais paragraphes.

    On refuse ``"a" * n`` : un texte sans espaces ni paragraphes ne teste pas la
    coupe de ligne. Ici on répète des phrases plausibles puis on tronque au
    caractère près pour retomber sur la longueur mesurée par l'audit.
    """
    phrases = [
        "Le sujet revient à chaque comité de pilotage sans que personne ne tranche.",
        "On nous demande un chiffrage, mais les données de production ne sont pas "
        "accessibles depuis l'environnement de recette.",
        "L'équipe a livré, la validation métier a pris six semaines.",
        "Ce que je retiens, c'est qu'on documente après coup, jamais avant.",
        "Il faudrait une instance de décision, pas une réunion d'information de plus.",
    ]
    morceaux = []
    total = 0
    i = 0
    while total < longueur:
        phrase = phrases[i % len(phrases)]
        separateur = "\n\n" if i % 4 == 3 else " "
        morceaux.append(phrase + separateur)
        total += len(phrase) + len(separateur)
        i += 1
    return "".join(morceaux)[:longueur]


def _contenu_entretien(verbatim: str) -> list:
    return [
        {"type": "titre", "texte": "Compte rendu d'entretien"},
        {"type": "champ", "libelle": "Interlocuteur", "valeur": NOM_VIETNAMIEN},
        {"type": "champ", "libelle": "Date", "valeur": "2026-08-31"},
        {"type": "filet"},
        {"type": "soustitre", "texte": "Verbatim intégral"},
        {"type": "encadre", "titre": "Propos recueillis", "texte": verbatim},
        {"type": "soustitre", "texte": "Points saillants"},
        {"type": "liste", "items": ["Pas d'instance de décision", "Recette bloquante"]},
    ]


def _generer(chemin, contenu, gabarit=GABARIT_TEST, **kw):
    return PR.construire_pdf(str(chemin), contenu, gabarit, **kw)


def _mesurer(chemin, **kw):
    kw.setdefault("grille_mm", GABARIT_TEST.marges.gauche_mm)
    return PV.analyser(str(chemin), **kw)


def _codes(rapport, gravite=None):
    return {
        d["code"]
        for d in rapport["defauts"]
        if gravite is None or d["gravite"] == gravite
    }


# =========================================================================== #
# Défaut 1 — verbatim insécable : LayoutError puis HTTP 500
# =========================================================================== #


def test_verbatim_12690_caracteres_ne_leve_pas(tmp_path):
    """Le verbatim EXACT qui faisait tomber la route en 500 doit passer.

    Chez VSCode2 il était posé dans un ``Table`` mono-cellule : platypus ne sait
    pas couper une cellule, donc ``LayoutError`` dès que le contenu dépasse la
    hauteur d'un cadre. Ici il traverse plusieurs pages sans exception.
    """
    verbatim = _verbatim(VERBATIM_QUI_CASSAIT)
    assert len(verbatim) == 12690
    cible = tmp_path / "verbatim_12690.pdf"
    rapport = _generer(cible, _contenu_entretien(verbatim))
    assert cible.exists()
    assert rapport.pages >= 3, f"attendu au moins 3 pages, obtenu {rapport.pages}"

    mesure = _mesurer(cible)
    # preuve que l'encadré a bien été COUPÉ : son fond teinté est présent sur
    # plusieurs pages, ce qu'un Table mono-cellule ne peut pas produire.
    pages_avec_trace = [p["numero"] for p in mesure["detail_pages"] if p["traces"] >= 3]
    assert len(pages_avec_trace) >= 3, mesure["detail_pages"]


def test_seuil_5016_passe_aussi(tmp_path):
    """La borne basse mesurée (5 016 caractères) reste évidemment valide."""
    cible = tmp_path / "verbatim_5016.pdf"
    rapport = _generer(cible, _contenu_entretien(_verbatim(VERBATIM_QUI_PASSAIT)))
    assert rapport.pages >= 2
    assert _mesurer(cible)["bloquants"] == 0


@pytest.mark.parametrize("longueur", [12_690, 60_000, 200_000])
def test_aucune_longueur_ne_leve_layouterror(tmp_path, longueur):
    """Aucune longueur ne doit lever : la garantie est « jamais », pas « souvent »."""
    cible = tmp_path / f"verbatim_{longueur}.pdf"
    rapport = _generer(cible, [{"type": "encadre", "texte": _verbatim(longueur)}])
    assert rapport.pages >= 2


def test_mot_de_12000_caracteres_sans_espace(tmp_path):
    """Un log collé sans espace : le pire cas de mise en page, il doit passer.

    C'est la variante réelle du verbatim Teams (URL, trace, base64 collée) qui
    fait échouer les moteurs de mise en page mot à mot.
    """
    cible = tmp_path / "token_long.pdf"
    rapport = _generer(cible, [{"type": "encadre", "texte": "X" * 12_000}])
    assert rapport.pages >= 1
    mesure = _mesurer(cible)
    assert "hors_zone_imprimable" not in _codes(mesure), mesure["detail_pages"]


def test_encadre_ne_produit_ni_table_ni_keeptogether(tmp_path):
    """Garde-fou structurel : les deux seules façons de rendre du texte insécable.

    Un test de rendu peut passer par chance sur un contenu court ; celui-ci
    interdit la construction fautive elle-même.
    """
    police = PR.enregistrer_police(GABARIT_TEST)
    styles = PR.construire_styles(GABARIT_TEST, police)
    # un seul paragraphe de 12 690 caractères : c'est CE bloc qui doit se couper,
    # pas la simple succession de paragraphes courts.
    monobloc = _verbatim(VERBATIM_QUI_CASSAIT).replace("\n\n", " ")
    flowables = PR.encadre_secable(monobloc, GABARIT_TEST, styles, police, titre="Verbatim")
    assert flowables, "encadre_secable n'a rien produit"
    for f in flowables:
        assert not isinstance(f, (Table, KeepTogether)), type(f)
        assert isinstance(f, Paragraph)
        # un Paragraph reportlab est sécable ; on le prouve au lieu de le croire
        assert hasattr(f, "split")
    largeur = GABARIT_TEST.largeur_utile
    long_para = max(flowables, key=lambda f: len(f.getPlainText()))
    long_para.wrap(largeur, 10_000)
    morceaux = long_para.split(largeur, 200)  # 200 pt : bien moins que sa hauteur
    assert len(morceaux) == 2, "le paragraphe de l'encadré ne s'est pas coupé"


# =========================================================================== #
# Défaut 2 — pages à 40 % avec 154 mm de blanc, orphelines à 4 %
# =========================================================================== #


def test_pas_de_page_creuse_ni_de_blanc_de_154_mm(tmp_path):
    """Mesuré page par page, pas déclaré : remplissage et plus grand blanc."""
    cible = tmp_path / "remplissage.pdf"
    _generer(cible, _contenu_entretien(_verbatim(VERBATIM_QUI_CASSAIT)))
    mesure = _mesurer(cible)
    intermediaires = mesure["detail_pages"][:-1]
    assert intermediaires, "il faut au moins deux pages pour que le test ait un sens"
    for page in intermediaires:
        assert page["remplissage_pct"] >= 55.0, page
        assert page["blanc_max_mm"] <= 60.0, page
    assert "page_creuse" not in _codes(mesure)
    assert "blanc_excessif" not in _codes(mesure)


def test_pas_de_page_orpheline(tmp_path):
    """La dernière page ne doit pas être une orpheline à 4 %.

    On calibre le contenu pour finir juste après un saut de page — le cas qui
    produisait l'orpheline dans l'audit.
    """
    cible = tmp_path / "orpheline.pdf"
    _generer(cible, _contenu_entretien(_verbatim(9_000)))
    mesure = _mesurer(cible)
    derniere = mesure["detail_pages"][-1]
    assert derniere["remplissage_pct"] >= 8.0, derniere
    assert "page_orpheline" not in _codes(mesure)


# =========================================================================== #
# Défaut 3 — tout caractère hors Latin-1 perdu sans avertissement
# =========================================================================== #


def test_vietnamien_cyrillique_grec_reellement_rendus(tmp_path):
    """Les trois chaînes de l'audit doivent se relire TELLES QUELLES dans le PDF.

    C'est la seule preuve qui compte : un PDF qui ne lève pas peut très bien
    contenir « NguyIn ThI Mai ». On réextrait le texte et on compare.
    """
    cible = tmp_path / "unicode.pdf"
    rapport = _generer(
        cible,
        [
            {"type": "titre", "texte": "Entretiens internationaux"},
            {"type": "champ", "libelle": "Interlocuteur 1", "valeur": NOM_VIETNAMIEN},
            {"type": "champ", "libelle": "Interlocuteur 2", "valeur": NOM_CYRILLIQUE},
            {"type": "corps", "texte": FORMULE_GRECQUE},
            {"type": "encadre", "titre": "Verbatim", "texte": NOM_CYRILLIQUE * 40},
        ],
    )
    assert not rapport.repli_police, rapport.raison_repli
    mesure = _mesurer(
        cible, attendus=[NOM_VIETNAMIEN, NOM_CYRILLIQUE, FORMULE_GRECQUE, "Δ", "ễ", "ё"]
    )
    assert mesure["attendus_absents"] == [], mesure["attendus_absents"]
    assert "texte_attendu_absent" not in _codes(mesure)
    # et le Δ grec (U+0394) n'a pas été troqué contre le ∆ mathématique (U+2206)
    import fitz

    doc = fitz.open(str(cible))
    texte = "".join(doc[i].get_text() for i in range(doc.page_count))
    doc.close()
    assert "\u0394" in texte and "\u2206" not in texte


def test_emoji_signale_et_jamais_perdu_en_silence(tmp_path):
    """Un caractère non couvert doit être SIGNALÉ, pas effacé.

    Aucune fonte monochrome ne porte 🙂. Le comportement attendu n'est pas de le
    rendre, c'est de ne pas le perdre : marqueur visible dans la page et entrée
    dans le rapport de rendu.
    """
    cible = tmp_path / "emoji.pdf"
    rapport = _generer(
        cible, [{"type": "corps", "texte": f"Réaction de l'équipe : {EMOJI} bilan mitigé."}]
    )
    points = {m["codepoint"] for m in rapport.caracteres_manquants}
    assert ord(EMOJI) in points, rapport.caracteres_manquants
    mesure = _mesurer(cible)
    assert f"[U+{ord(EMOJI):04X}]" in mesure["marqueurs_glyphes_manquants"], mesure[
        "marqueurs_glyphes_manquants"
    ]
    assert "glyphes_signales" in _codes(mesure, "avertissement")


def test_politique_stricte_leve_au_lieu_de_perdre(tmp_path):
    """Avec ``politique_glyphes='strict'``, on refuse de produire un PDF mutilé."""
    gabarit = replace(GABARIT_TEST, politique_glyphes="strict")
    with pytest.raises(PR.CaracteresNonRendus) as exc:
        _generer(tmp_path / "strict.pdf", [{"type": "corps", "texte": EMOJI}], gabarit)
    assert exc.value.manquants[0]["codepoint"] == ord(EMOJI)


def test_repli_police_documente_et_signale_tout_le_non_latin1(tmp_path):
    """Si aucune fonte Unicode n'est trouvée, le repli est explicite et bruyant.

    C'est le scénario « machine sans DejaVu » : on retombe sur Helvetica base-14
    — exactement la configuration qui a produit « NguyIn ThI Mai » — mais on le
    DIT (``repli=True`` + ``raison``) et on signale chaque caractère perdu.
    """
    typo = replace(
        GABARIT_TEST.typo,
        familles=({"nom": "FonteInexistante", "regulier": "aucune-fonte-ici.ttf"},),
    )
    gabarit = replace(GABARIT_TEST, typo=typo)
    rapport = _generer(tmp_path / "repli.pdf", [{"type": "corps", "texte": NOM_VIETNAMIEN}], gabarit)
    assert rapport.repli_police
    assert "aucune-fonte-ici.ttf" in rapport.raison_repli
    assert "Helvetica" in rapport.raison_repli
    perdus = {m["caractere"] for m in rapport.caracteres_manquants}
    assert "ễ" in perdus and "ị" in perdus, rapport.caracteres_manquants
    # et l'interdiction de replier lève au lieu de dégrader en silence
    with pytest.raises(PR.PoliceIntrouvable):
        PR.enregistrer_police(gabarit, autoriser_repli=False)


def test_police_reellement_embarquee(tmp_path):
    """Défaut 5, volet police : la fonte doit être DANS le fichier."""
    cible = tmp_path / "embarquee.pdf"
    _generer(cible, [{"type": "corps", "texte": NOM_VIETNAMIEN}])
    mesure = _mesurer(cible)
    assert mesure["polices"], "aucune police déclarée"
    assert all(f["embarquee"] for f in mesure["polices"]), mesure["polices"]
    assert "police_non_embarquee" not in _codes(mesure)


# =========================================================================== #
# Défaut 4 — trois bords gauche sur une même page (20,0 / 22,1 / 25,0 mm)
# =========================================================================== #


def test_les_trois_bords_gauche_coincident(tmp_path):
    """En-tête (canvas), texte (Frame) et encadré doivent partager UNE abscisse.

    Les trois sources du défaut sont toutes présentes dans le document produit :
    l'en-tête est tracé au canvas, le corps passe par un ``Frame`` (dont le
    padding de 6 pt par défaut créait le 22,1 mm), l'encadré a un fond dont la
    largeur était figée à ``160*mm``.
    """
    cible = tmp_path / "grille.pdf"
    _generer(cible, _contenu_entretien(_verbatim(VERBATIM_QUI_CASSAIT)))
    mesure = _mesurer(cible)
    bords = mesure["bords_gauche_mm"]
    assert len(bords) == 1, f"bords gauche multiples : {bords}"
    assert abs(bords[0]["x_mm"] - GABARIT_TEST.marges.gauche_mm) <= 0.4, bords
    assert "bords_gauche_multiples" not in _codes(mesure)
    assert "grille_decalee" not in _codes(mesure)
    # les trois familles d'éléments sont bien présentes, sinon le test est creux
    assert mesure["elements_cales_a_droite"] >= 2, "pas d'élément d'en-tête/pied calé à droite"
    assert any(p["traces"] >= 3 for p in mesure["detail_pages"]), "pas de fond d'encadré"


def _pdf_avec_retrait_de_citation(chemin: str, grille_mm: float, retrait_mm: float) -> str:
    """Fabrique le motif exact que l'arbitrage du 2026-08-31 déclare légitime.

    Le FOND de l'encadré commence sur la grille ; seul son TEXTE est en retrait, pour
    laisser la place au filet latéral. Tout le reste du document est sur la grille.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm as MM
    from reportlab.pdfgen import canvas as rl_canvas

    largeur, hauteur = A4
    c = rl_canvas.Canvas(chemin, pagesize=A4)
    c.setFont("Helvetica", 10)
    for i in range(30):
        c.drawString(grille_mm * MM, hauteur - (30 + i * 7) * MM, f"Ligne de corps numero {i}")
    haut = hauteur - 250 * MM
    c.setFillColorRGB(0.98, 0.95, 0.85)
    c.rect(grille_mm * MM, haut, (170 - grille_mm) * MM, 25 * MM, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0)
    for i in range(3):
        c.drawString((grille_mm + retrait_mm) * MM, haut + (18 - i * 7) * MM,
                     "Propos rapporte, en retrait a l'interieur du cadre")
    c.showPage()
    c.save()
    return chemin


def test_le_retrait_de_citation_assume_est_accepte_sans_rendre_aveugle(tmp_path):
    """Un texte en retrait DANS un encadré dont le fond est sur la grille est légitime.

    Arbitrage utilisateur du 2026-08-31 : la chaîne PDF de VSCode2 garde un retrait de
    citation de 10 pt (3,53 mm) parce qu'il laisse la place au filet latéral ambré du
    .docx modèle client. Le fond de l'encadré, lui, reste sur la grille. Le vérificateur
    doit donc pouvoir l'accepter — mais SEULEMENT s'il est déclaré, et sans cesser de
    voir un vrai désalignement. Les deux moitiés sont testées ici : sans le second
    `assert`, l'option serait un interrupteur pour éteindre le contrôle.
    """
    retrait = 3.53
    grille = 20.0

    # 1. Le motif arbitre : fond sur la grille, texte en retrait.
    motif = tmp_path / "retrait.pdf"
    _pdf_avec_retrait_de_citation(str(motif), grille, retrait)
    sans_option = _codes(PV.analyser(str(motif), grille_mm=grille), gravite="bloquant")
    avec_option = _codes(
        PV.analyser(str(motif), grille_mm=grille, retrait_citation_mm=retrait),
        gravite="bloquant")
    assert "bords_gauche_multiples" in sans_option, (
        "sans declaration, un texte en retrait DOIT etre signale — sinon l'option ne sert a rien")
    assert "bords_gauche_multiples" not in avec_option, (
        f"le retrait declare doit etre accepte, bloquants restants : {avec_option}")

    conforme = tmp_path / "conforme.pdf"
    _generer(conforme, _contenu_entretien(_verbatim(VERBATIM_QUI_CASSAIT)))
    # Un document déjà conforme le reste : l'option n'invente pas de défaut.
    assert "bords_gauche_multiples" not in _codes(_mesurer(conforme, retrait_citation_mm=retrait))

    fautif = tmp_path / "fautif.pdf"
    _fabriquer_pdf_fautif(str(fautif))
    sans = _codes(_mesurer(fautif), gravite="bloquant")
    avec = _codes(_mesurer(fautif, retrait_citation_mm=retrait), gravite="bloquant")
    assert "bords_gauche_multiples" in sans, "le PDF fautif doit être détecté sans l'option"
    assert "bords_gauche_multiples" in avec, (
        "l'option ne doit PAS masquer un vrai désalignement : les bords fautifs "
        "(20,0 / 22,1 / 25,0 / 27,0 mm) ne sont pas un retrait de citation"
    )


def test_rien_ne_sort_de_la_zone_imprimable(tmp_path):
    """Le fond de l'encadré ne doit pas déborder à droite non plus."""
    cible = tmp_path / "zone.pdf"
    _generer(cible, _contenu_entretien(_verbatim(VERBATIM_QUI_CASSAIT)))
    mesure = _mesurer(cible)
    for page in mesure["detail_pages"]:
        assert page["hors_zone"] == [], page["hors_zone"]
    assert "hors_zone_imprimable" not in _codes(mesure)


def test_la_grille_suit_le_gabarit(tmp_path):
    """Changer la marge du gabarit déplace TOUT : rien n'est codé en dur.

    Si une seule des trois sources gardait sa valeur figée, on retomberait sur
    deux bords gauche — ce que le test détecte.
    """
    gabarit = replace(GABARIT_TEST, marges=replace(GABARIT_TEST.marges, gauche_mm=32.0))
    cible = tmp_path / "grille32.pdf"
    _generer(cible, _contenu_entretien(_verbatim(6_000)), gabarit)
    mesure = PV.analyser(str(cible), grille_mm=32.0)
    assert len(mesure["bords_gauche_mm"]) == 1, mesure["bords_gauche_mm"]
    assert abs(mesure["bords_gauche_mm"][0]["x_mm"] - 32.0) <= 0.4
    assert "grille_decalee" not in _codes(mesure)


# =========================================================================== #
# Défaut 5 — aucune métadonnée, aucun signet, pas de /Lang
# =========================================================================== #


def test_metadonnees_signets_et_langue(tmp_path):
    """title='(anonymous)' était le constat ; ici tout est renseigné et vérifié."""
    cible = tmp_path / "meta.pdf"
    rapport = _generer(cible, _contenu_entretien(_verbatim(6_000)))
    mesure = _mesurer(cible)
    meta = mesure["metadonnees"]
    assert meta["titre"] == GABARIT_TEST.titre
    assert meta["auteur"] == GABARIT_TEST.auteur
    assert meta["sujet"] == GABARIT_TEST.sujet
    assert meta["langue"] == GABARIT_TEST.langue
    assert meta["titre"].lower() != "(anonymous)"
    assert len(mesure["signets"]) >= 3, mesure["signets"]
    assert mesure["signets"][0]["titre"] == "Compte rendu d'entretien"
    assert {s["niveau"] for s in mesure["signets"]} == {1, 2}
    assert len(rapport.signets) == len(mesure["signets"])
    for code in ("metadonnee_titre", "metadonnee_auteur", "langue_absente", "aucun_signet"):
        assert code not in _codes(mesure)


def test_balisage_absent_est_dit_et_jamais_simule(tmp_path):
    """Ce que la brique NE fait pas doit être remonté, pas caché.

    reportlab ne produit pas de PDF balisé. On refuse d'injecter un
    ``/StructTreeRoot`` vide : le vérificateur le signale en avertissement, et
    en défaut bloquant sur ``--exiger-balisage``.
    """
    cible = tmp_path / "balisage.pdf"
    _generer(cible, _contenu_entretien(_verbatim(3_000)))
    souple = _mesurer(cible)
    assert souple["balisage"]["struct_tree_root"] is False
    assert "pdf_non_balise" in _codes(souple, "avertissement")
    assert souple["verdict"] == "OK"
    strict = _mesurer(cible, exiger_balisage=True)
    assert "pdf_non_balise" in _codes(strict, "bloquant")
    assert strict["verdict"] == "KO"


# =========================================================================== #
# Défaut 6 — aucune notion de template
# =========================================================================== #


def test_gabarit_parametrable_depuis_un_json(tmp_path):
    """Palette, typo, marges et format viennent d'un dict, jamais du code."""
    gabarit = PR.gabarit_depuis_dict(
        {
            "nom": "client-x",
            "format_page": "LETTER",
            "titre": "Rapport client X",
            "auteur": "Client X",
            "langue": "en-US",
            "palette": {"titre": "#7a0026", "fond_encadre": "#fdf1e7"},
            "typo": {"taille_corps": 11.5, "interligne": 1.6},
            "marges": {"gauche_mm": 28.0, "droite_mm": 18.0},
        }
    )
    assert gabarit.palette.titre == "#7a0026"
    assert gabarit.typo.taille_corps == 11.5
    assert gabarit.marges.gauche_mm == 28.0
    assert gabarit.format_page == "LETTER"

    cible = tmp_path / "client_x.pdf"
    _generer(cible, _contenu_entretien(_verbatim(6_000)), gabarit)
    mesure = PV.analyser(str(cible), grille_mm=28.0)
    assert abs(mesure["detail_pages"][0]["largeur_mm"] - 215.9) < 0.5  # LETTER
    assert len(mesure["bords_gauche_mm"]) == 1
    assert abs(mesure["bords_gauche_mm"][0]["x_mm"] - 28.0) <= 0.4
    assert mesure["metadonnees"]["langue"] == "en-US"
    assert mesure["metadonnees"]["titre"] == "Rapport client X"

    with pytest.raises(ValueError):
        PR.gabarit_depuis_dict({"couleur_principale": "#000000"})  # clé inconnue


def test_deux_gabarits_donnent_deux_geometries(tmp_path):
    """Preuve que le gabarit agit vraiment, et pas seulement sur les métadonnées."""
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    contenu = _contenu_entretien(_verbatim(4_000))
    _generer(a, contenu, GABARIT_TEST)
    _generer(
        b,
        contenu,
        replace(
            GABARIT_TEST,
            marges=replace(GABARIT_TEST.marges, gauche_mm=15.0, droite_mm=15.0),
            typo=replace(GABARIT_TEST.typo, taille_corps=8.0),
        ),
    )
    ma, mb = PV.analyser(str(a)), PV.analyser(str(b))
    assert ma["bords_gauche_mm"][0]["x_mm"] != mb["bords_gauche_mm"][0]["x_mm"]
    assert mb["pages"] <= ma["pages"]  # plus petit corps, plus large : moins de pages


# =========================================================================== #
# Défaut 7 — 78 assertions et zéro sur la géométrie : le vérificateur doit
# attraper un PDF fautif. On en fabrique un exprès, calqué sur l'audit.
# =========================================================================== #


def _fabriquer_pdf_fautif(chemin: str) -> str:
    """Reproduit à la main les défauts mesurés chez VSCode2, pour piéger le vérificateur.

    - Helvetica base-14, non embarquée (défaut 3 et 5) ;
    - aucune métadonnée : reportlab écrit alors ``/Title (anonymous)`` (défaut 5) ;
    - trois bords gauche : 20,0 mm (en-tête au canvas), 22,1 mm (texte décalé du
      padding de ``Frame``), 25,0 mm (encadré à ``160*mm`` centré) — défaut 4 ;
    - une page à ~40 % avec un grand blanc, une dernière page quasi vide (défaut 2) ;
    - un encadré qui déborde de la zone imprimable.
    """
    c = Canvas(chemin, pagesize=(210 * mm, 297 * mm))
    c.setFont("Helvetica", 8)

    def entete(numero):
        c.setFont("Helvetica", 8)
        c.drawString(20.0 * mm, 285 * mm, "Compte rendu")  # bord 20,0 mm
        c.drawRightString(190.0 * mm, 285 * mm, f"Page {numero}")

    # page 1 : dense, mais trois bords gauche
    entete(1)
    c.setFont("Helvetica", 10)
    y = 270 * mm
    for i in range(45):
        c.drawString(22.1 * mm, y, f"Ligne de corps numero {i} du compte rendu.")  # 22,1
        y -= 5 * mm
    c.setFillColorRGB(0.93, 0.95, 0.97)
    c.rect(25.0 * mm, 20 * mm, 160 * mm, 20 * mm, fill=1, stroke=0)  # bord 25,0 mm
    c.setFillColorRGB(0, 0, 0)
    c.drawString(27.0 * mm, 30 * mm, "Encadre insecable pose a 25 mm.")
    c.showPage()

    # page 2 : creuse — quelques lignes en haut, puis 200 mm de blanc
    entete(2)
    c.setFont("Helvetica", 10)
    y = 270 * mm
    for i in range(6):
        c.drawString(22.1 * mm, y, f"Suite {i}.")
        y -= 5 * mm
    c.setFillColorRGB(0.93, 0.95, 0.97)
    c.rect(3 * mm, 40 * mm, 204 * mm, 10 * mm, fill=1, stroke=0)  # hors zone de sécurité
    c.setFillColorRGB(0, 0, 0)
    c.showPage()

    # page 3 : orpheline
    entete(3)
    c.setFont("Helvetica", 10)
    c.drawString(22.1 * mm, 270 * mm, "Fin.")
    c.showPage()
    c.save()
    return chemin


def test_pdf_verify_detecte_le_pdf_fautif(tmp_path):
    """Le vérificateur doit attraper CHAQUE défaut de l'audit sur un PDF piégé."""
    fautif = _fabriquer_pdf_fautif(str(tmp_path / "fautif.pdf"))
    mesure = PV.analyser(fautif, grille_mm=20.0)
    codes = _codes(mesure)
    attendus = {
        "bords_gauche_multiples",  # défaut 4
        "police_non_embarquee",  # défauts 3 et 5
        "metadonnee_titre",  # défaut 5
        "metadonnee_auteur",
        "langue_absente",
        "page_creuse",  # défaut 2
        "blanc_excessif",
        "page_orpheline",
        "hors_zone_imprimable",
    }
    manquants = attendus - codes
    assert not manquants, f"défauts non détectés : {sorted(manquants)} (vus : {sorted(codes)})"
    assert mesure["verdict"] == "KO"
    assert mesure["bloquants"] >= len(attendus)
    # les trois bords de l'audit sont retrouvés au dixième de millimètre près
    bords = sorted(b["x_mm"] for b in mesure["bords_gauche_mm"])
    assert len(bords) >= 3, bords
    for attendu in (20.0, 22.1, 25.0):
        assert any(abs(b - attendu) <= 0.4 for b in bords), (attendu, bords)


def test_pdf_verify_code_de_sortie(tmp_path):
    """Chaînable : 0 si propre, 1 si défaut bloquant, 2 si on ne peut pas mesurer."""
    bon = tmp_path / "bon.pdf"
    _generer(bon, _contenu_entretien(_verbatim(6_000)))
    assert PV.main([str(bon), "--grille-mm", "20"]) == 0

    fautif = _fabriquer_pdf_fautif(str(tmp_path / "fautif2.pdf"))
    assert PV.main([fautif, "--grille-mm", "20"]) == 1
    assert PV.main([str(tmp_path / "inexistant.pdf")]) == 2


def test_pdf_verify_en_ligne_de_commande(tmp_path):
    """Le vérificateur s'exécute vraiment en CLI, pas seulement par import."""
    fautif = _fabriquer_pdf_fautif(str(tmp_path / "fautif3.pdf"))
    script = os.path.join(RACINE, "scripts", "pdf_verify.py")
    # PYTHONIOENCODING : sans lui, le processus fils écrit en cp1252 sur Windows
    # et les accents du rapport cassent le décodage UTF-8 côté parent.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, script, fautif, "--grille-mm", "20", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert proc.returncode == 1, proc.stderr
    import json

    rapport = json.loads(proc.stdout)
    assert rapport["verdict"] == "KO"
    assert rapport["pages"] == 3


def test_pdf_verify_detecte_le_texte_perdu_en_latin1(tmp_path):
    """Le contrôle ``--attendu`` attrape la mutilation silencieuse de l'audit.

    On génère volontairement avec le repli base-14 et la politique « ignorer » :
    c'est la chaîne VSCode2 telle qu'elle était. Le PDF se produit sans erreur —
    et le vérificateur, lui, voit que « Nguyễn Thị Mai » n'y est pas.
    """
    typo = replace(
        GABARIT_TEST.typo,
        familles=({"nom": "FonteInexistante", "regulier": "aucune-fonte-ici.ttf"},),
    )
    gabarit = replace(GABARIT_TEST, typo=typo, politique_glyphes="ignorer")
    cible = tmp_path / "mutile.pdf"
    rapport = _generer(cible, [{"type": "corps", "texte": NOM_VIETNAMIEN}], gabarit)
    assert rapport.repli_police
    mesure = PV.analyser(str(cible), attendus=[NOM_VIETNAMIEN])
    assert mesure["attendus_absents"], "le texte mutilé n'a pas été détecté"
    assert "texte_attendu_absent" in _codes(mesure, "bloquant")
    assert "U+1EC5" in mesure["attendus_absents"][0]["codepoints"]  # ễ


def test_pymupdf_absent_est_annonce_pas_simule(tmp_path, monkeypatch):
    """Sans PyMuPDF, on refuse de rendre un verdict : code 2 et message clair."""
    monkeypatch.setattr(PV, "FITZ_DISPONIBLE", False)
    monkeypatch.setattr(PV, "FITZ_ERREUR", "No module named 'fitz'")
    bon = tmp_path / "bon2.pdf"
    _generer(bon, [{"type": "corps", "texte": "un contenu quelconque"}])
    with pytest.raises(PV.PyMuPDFAbsent) as exc:
        PV.analyser(str(bon))
    assert "pymupdf" in str(exc.value).lower()
    assert PV.main([str(bon)]) == 2


# =========================================================================== #
# Rendu réel — la dernière ligne de défense reste l'œil, pas le parseur.
# =========================================================================== #


def test_rasterisation_reelle_des_pages(tmp_path):
    """Les pages se rastérisent et contiennent de l'encre : un PDF n'est pas vide.

    Ce test est le minimum automatisable. Il ne remplace PAS l'ouverture des PNG
    par un humain (ou par un agent qui les affiche) — voir SKILL.md.
    """
    import fitz

    cible = tmp_path / "rendu.pdf"
    _generer(cible, _contenu_entretien(_verbatim(VERBATIM_QUI_CASSAIT)))
    doc = fitz.open(str(cible))
    assert doc.page_count >= 3
    for numero in range(min(3, doc.page_count)):
        pix = doc[numero].get_pixmap(dpi=110)
        assert pix.width > 800 and pix.height > 1100
        echantillon = pix.samples
        sombres = sum(1 for o in echantillon[::37] if o < 200)
        assert sombres > 50, f"page {numero + 1} : presque pas d'encre ({sombres})"
    doc.close()
