"""Tests for slide_lint. Run: python tests/test_slide_lint.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import slide_lint as L


def codes(findings):
    return sorted({c for _, c, _ in findings})


def test_clean_slide_passes():
    f = L.lint_slide(
        "Le pipeline tient un export multi-équipes",
        ["Un clic produit le support au format OCTO.",
         "Le radar du PPT reste identique à l'écran."],
    )
    assert f == [], f


def test_long_bullet_flagged():
    long = "x" * (L.MAX_BULLET_CHARS + 1)
    f = L.lint_slide("Un titre qui affirme quelque chose", [long])
    assert "LONG_BULLET" in codes(f)


def test_too_many_bullets():
    f = L.lint_slide("Titre qui dit une idée claire",
                     [f"Puce numéro {i} bien distincte." for i in range(L.MAX_BULLETS + 1)])
    assert "TOO_MANY" in codes(f)


def test_filler_flagged():
    f = L.lint_slide("Ce que produit l'outil",
                     ["Afin de gagner du temps, l'export est direct."])
    assert "FILLER" in codes(f)


def test_mixed_punctuation():
    f = L.lint_slide("L'export fonctionne de bout en bout",
                     ["Première puce terminée par un point.", "Seconde puce sans point"])
    assert "MIXED_PUNCT" in codes(f)


def test_unexplained_abbrev_flagged_once_and_ok_when_expanded():
    bad = L.lint_slide("Le résultat dépend d'un service externe",
                       ["Le radar passe par XYZW puis par XYZW."])
    assert codes(bad) == ["ABBREV"]  # flagged once, not twice
    ok = L.lint_slide("Le résultat s'appuie sur un composant",
                      ["Le radar passe par XYZW (moteur maison) ensuite."])
    assert "ABBREV" not in codes(ok)
    # known abbreviations are never flagged
    assert L.lint_slide("Le support OCTO est généré",
                        ["L'export PPT s'appuie sur l'API et le format PNG."]) == []


def test_weak_title_flagged():
    assert "WEAK_TITLE" in codes(L.lint_slide("Contexte", ["Une vraie phrase de contenu ici."]))
    # a claim-style title is fine
    assert "WEAK_TITLE" not in codes(
        L.lint_slide("Pourquoi un export PowerPoint ?", ["Une phrase."]))


def test_duplicate_lead_word():
    f = L.lint_slide("Trois idées qui se ressemblent trop",
                     ["Skill pptx-deck : composants.", "Skill pptx-verify : rendu.",
                      "Skill restitution-ppt : règles."])
    assert "DUP_LEAD" in codes(f)


def test_lint_deck_aggregates_indices():
    deck = [{"title": "Contexte", "bullets": ["phrase."]},
            {"title": "Un titre correct et parlant", "bullets": ["phrase."]}]
    f = L.lint_deck(deck)
    assert any(i == 0 and c == "WEAK_TITLE" for i, c, _ in f)
    assert all(i != 1 for i, c, _ in f if c == "WEAK_TITLE")


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("ok ", fn.__name__)
    print(f"\nALL {len(fns)} TESTS PASSED")


if __name__ == "__main__":
    main()
