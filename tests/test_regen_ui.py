"""Bug « Régénérer IA » sans effet (2026-07-22) — verrous de régression.

Deux causes racines corrigées, chacune verrouillée ici :
1. htmx chargé depuis le CDN unpkg : réseau coupé/proxy → TOUS les hx-* inertes
   en silence — autosave ET le bouton « Régénérer (IA) » de la synthèse globale
   (hx-post sur type=button : sans htmx un clic ne fait strictement rien).
   → htmx est désormais VENDORÉ dans app/static et référencé en local.
2. Générations synchrones longues (30 s à minutes avec Ollama) sans aucun retour
   visuel sur les forms classiques → busy.js gèle le bouton dès la soumission
   (data-busy-label sur chaque form de génération).
Calqué sur test_difficultes.py : DB jetable, TestClient.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import GlobalSynthesis, Interview, Mission

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


def setup_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def teardown_module() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    if DB_PATH.exists():
        DB_PATH.unlink()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _mission_id() -> int:
    db = SessionLocal()
    try:
        m = Mission(name="Mission Regen UI")
        db.add(m)
        db.flush()
        db.add(Interview(mission_id=m.id, interviewee_name="Témoin", status="done"))
        db.add(GlobalSynthesis(
            mission_id=m.id, status="generated", contexte="- C", culture_adn="- C",
            forces_succes="- F", points_amelioration="- P", aspirations="- A"))
        db.commit()
        return m.id
    finally:
        db.close()


def test_htmx_est_vendore_en_local_pas_de_cdn(client: TestClient) -> None:
    """base.html référence /static/htmx.min.js — plus JAMAIS unpkg (une coupure
    CDN rendait tous les hx-* inertes sans aucune erreur visible)."""
    page = client.get("/missions")
    assert page.status_code == 200
    assert "/static/htmx.min.js" in page.text, "htmx local non référencé par base.html"
    assert "unpkg.com" not in page.text, "htmx encore chargé depuis le CDN unpkg"
    # Le fichier vendoré existe, est servi, et est bien htmx (pas un stub vide).
    fichier = STATIC / "htmx.min.js"
    assert fichier.exists(), "app/static/htmx.min.js absent — vendorer htmx (npm pack htmx.org)"
    assert fichier.stat().st_size > 10_000, "htmx.min.js suspicieusement petit"
    served = client.get("/static/htmx.min.js")
    assert served.status_code == 200 and "htmx" in served.text[:2000].lower()
    # Verrou de VERSION (revue adversariale) : un remplacement/downgrade silencieux
    # du fichier vendoré passait tous les tests — la version attendue est épinglée.
    assert "2.0.3" in served.text, "htmx vendoré n'est plus la 2.0.3 épinglée"


def test_busy_js_reference_et_servi(client: TestClient) -> None:
    """busy.js (feedback « génération en cours ») est référencé et servi."""
    page = client.get("/missions")
    assert "/static/busy.js" in page.text
    served = client.get("/static/busy.js")
    assert served.status_code == 200 and "data-busy-label" in served.text
    # Le contrat « générique » couvre aussi le <button> sans attribut type
    # (submit implicite en HTML — revue adversariale 2026-07-22).
    assert "button:not([type])" in served.text
    # Et le retour bfcache réarme la page (sinon bouton gelé à vie sur Back).
    assert "pageshow" in served.text


def test_forms_generation_portent_busy_label(client: TestClient) -> None:
    """Chaque form de génération IA synchrone porte data-busy-label : sans lui,
    un POST de 30 s à plusieurs minutes ne donne AUCUN signe de vie (bug
    « rien ne se déclenche »). Rendu réel pour recommandations ; au niveau
    source pour les templates qui exigent une mission plus riche. L'inventaire
    est EXHAUSTIF par grep des action="…/generate" (revue adversariale : la 1re
    version listait les templates à la main et ratait export_import.html)."""
    mid = _mission_id()
    page = client.get(f"/missions/{mid}/recommandations")
    assert page.status_code == 200
    assert 'data-busy-label="⏳' in page.text, "form recommandations sans busy-label"
    # Inventaire exhaustif : TOUT form POST classique vers une route *,/generate
    # doit porter data-busy-label sur sa balise <form …> (les chemins htmx
    # hx-post ont leur propre indicateur, hors périmètre).
    import re
    for tpl in TEMPLATES.rglob("*.html"):
        src = tpl.read_text(encoding="utf-8")
        for m in re.finditer(r"<form\b[^>]*action=\"[^\"]*/generate\"[^>]*>", src, re.S):
            assert "data-busy-label=" in m.group(0), (
                f"{tpl.name} : form de génération sans data-busy-label — {m.group(0)[:90]}…"
            )
    # export_import.html régénère aussi les axes : même confirm destructif que
    # recommandations.html (trou trouvé par la revue adversariale).
    exp = (TEMPLATES / "synthese" / "export_import.html").read_text(encoding="utf-8")
    assert "onsubmit" in exp and "confirm(" in exp, "export_import.html : régénération sans confirm"
    # libre_analyse.html : le form « Régénérer l'analyse (IA) » (route non-/generate).
    analyse = (TEMPLATES / "interviews" / "libre_analyse.html").read_text(encoding="utf-8")
    assert "data-busy-label=" in analyse, "form régénérer de libre_analyse.html sans busy-label"


def test_forms_extraction_ia_portent_busy_label(client: TestClient) -> None:
    """Revue UX 2026-07-23 (P1-3) : les extractions IA hors synthèse — les POST les
    PLUS longs de l'app, ceux-là même qui ont motivé busy.js — n'avaient aucun état
    busy. Liste EXPLICITE des couples (template, action) vérifiée contre les
    appelants réels d'extract_*/generate_* dans app/routers (l'inventaire
    automatique par route n'est pas faisable en test de template : l'action est
    une URL, pas un nom de fonction). À tenir à jour : tout NOUVEAU form qui
    poste vers une route appelant l'IA s'ajoute ici — le garde-fou global reste
    la revue (le trou notes/dispatch a été trouvé par revue, pas par ce test)."""
    import re

    # (template, fragment de l'attribut action= du form d'extraction — le
    # guillemet fermant discrimine record de record-libre)
    attendus = [
        ("interviews/import.html", "/interviews/import\""),
        ("interviews/record.html", "/interviews/record\""),
        ("interviews/record_libre.html", "/interviews/record-libre\""),
        ("interviews/libre_turns_review.html", "/record-libre/synthese\""),
        ("interviews/capture.html", "/notes/dispatch\""),
        ("trames/import.html", "/trame/import\""),
    ]
    for rel, action_frag in attendus:
        src = (TEMPLATES / rel).read_text(encoding="utf-8")
        forms = [
            m.group(0)
            for m in re.finditer(r"<form\b[^>]*>", src)
            if action_frag in m.group(0)
        ]
        assert forms, f"{rel} : form d'action {action_frag} introuvable (template remanié ?)"
        for form_tag in forms:
            assert "data-busy-label=" in form_tag, (
                f"{rel} : form d'extraction IA sans data-busy-label — {form_tag[:90]}…"
            )
    # Contrat busy.js (revue adversariale 2026-07-23) : gel PAR FORMULAIRE (les
    # jumeaux et boutons form= externes aussi), jamais les boutons formaction
    # (export PDF de secours — gel à vie sinon), et dégel ciblé au retour bfcache
    # (un location.reload() perdrait la transcription en vol des écrans record).
    busy = client.get("/static/busy.js")
    assert ":not([formaction])" in busy.text, "fallback busy.js : boutons formaction non exclus"
    assert 'button[form=' in busy.text, "boutons externes form= non gelés (double export possible)"
    assert "busyFrozen" in busy.text and "location.reload" not in busy.text, (
        "pageshow doit dégeler les boutons marqués, pas recharger la page"
    )


def test_autosave_question_trame_hx_et_post_classique(client: TestClient) -> None:
    """Défer P2-6 soldé (revue UX 2026-07-23) : la ligne de question de la trame
    s'enregistre en autosave HTMX (fragment ✓/⚠, pas de redirection) tandis
    qu'un POST classique garde la redirection 303 historique."""
    from app.models import Question, Theme, Trame

    db = SessionLocal()
    try:
        m = Mission(name="Mission Autosave Trame", trame=Trame(name="T"))
        db.add(m)
        db.flush()
        theme = Theme(trame_id=m.trame.id, title="Thème", position=0)
        db.add(theme)
        db.flush()
        q = Question(theme_id=theme.id, label="Avant", qtype="open", position=0)
        db.add(q)
        db.commit()
        mid, qid = m.id, q.id
    finally:
        db.close()

    url = f"/missions/{mid}/trame/questions/{qid}/edit"
    # Autosave HTMX : fragment ET persistance réelle.
    rep = client.post(url, data={"label": "Après", "qtype": "open"},
                      headers={"HX-Request": "true"})
    assert rep.status_code == 200 and "✓ enregistré" in rep.text
    db = SessionLocal()
    try:
        assert db.get(Question, qid).label == "Après"
    finally:
        db.close()
    # Label espaces-seulement en HTMX : erreur visible, valeur PAS écrasée.
    rep_vide = client.post(url, data={"label": "  ", "qtype": "open"},
                           headers={"HX-Request": "true"})
    assert rep_vide.status_code == 200 and "⚠" in rep_vide.text
    # Bornes d'échelle vidées pendant la frappe : fragment 200, jamais un 422
    # (invisible pour htmx — l'édit serait perdu en silence).
    rep_scale = client.post(
        url, data={"label": "Après", "qtype": "scale", "scale_min": "", "scale_max": ""},
        headers={"HX-Request": "true"})
    assert rep_scale.status_code == 200 and "✓" in rep_scale.text
    # Question supprimée dans un autre onglet : fragment d'erreur, pas un 404 JSON muet.
    rep_404 = client.post(f"/missions/{mid}/trame/questions/999999/edit",
                          data={"label": "X", "qtype": "open"},
                          headers={"HX-Request": "true"})
    assert rep_404.status_code == 200 and "introuvable" in rep_404.text
    db = SessionLocal()
    try:
        assert db.get(Question, qid).label == "Après", "un chemin d'erreur a écrasé la valeur"
    finally:
        db.close()
    # POST classique (sans htmx) : redirection historique conservée.
    rep_classique = client.post(url, data={"label": "Après 2", "qtype": "open"},
                                follow_redirects=False)
    assert rep_classique.status_code == 303
    db = SessionLocal()
    try:
        assert db.get(Question, qid).label == "Après 2"
    finally:
        db.close()
    # Template : autosave en place, bouton par ligne remplacé par le repli masqué
    # (un form multi-champs SANS bouton ne se soumet pas à Entrée sans JS) + le
    # halt de validation htmx est rendu visible par autosave.js.
    src = (TEMPLATES / "trames" / "edit.html").read_text(encoding="utf-8")
    assert 'class="edit-question"' in src and "hx-post" in src and "hx-sync" in src
    assert 'class="visually-hidden">Enregistrer</button>' in src
    autosave = (STATIC / "autosave.js").read_text(encoding="utf-8")
    assert "htmx:validation:halted" in autosave


def test_demarrer_propose_la_derniere_mission_consultee(client: TestClient) -> None:
    """Défer item 18 soldé : la fiche mission pose un cookie `derniere_mission`
    (hors brouillon) et /demarrer propose de la reprendre ; id périmé ignoré."""
    mid = _mission_id()
    # Sans cookie : pas d'encart de reprise.
    vierge = client.get("/demarrer")
    assert "Mission en cours" not in vierge.text
    # La visite de la fiche pose le cookie…
    fiche = client.get(f"/missions/{mid}")
    assert fiche.status_code == 200
    assert client.cookies.get("derniere_mission") == str(mid)
    # …et /demarrer propose la reprise.
    rep = client.get("/demarrer")
    assert "Mission en cours" in rep.text and f"/missions/{mid}" in rep.text
    # Id périmé (mission supprimée) : encart absent, pas d'erreur.
    client.cookies.set("derniere_mission", "999999")
    perime = client.get("/demarrer")
    assert perime.status_code == 200 and "Mission en cours" not in perime.text
    # Cookie forgé : valeur géante ou non numérique — jamais un 500 sur l'écran
    # d'entrée (revue adversariale 2026-07-23). Le chiffre Unicode « ² »
    # (isdigit-vrai mais int-faux) n'est pas envoyable via httpx (cookies ASCII),
    # un vrai navigateur le peut : le garde isascii() l'exclut, vérifié à sec.
    assert "²".isdigit() and not "²".isascii()  # ce que le garde doit couvrir
    for forge in ("9" * 20, "abc", "-3", "1e5"):
        client.cookies.set("derniere_mission", forge)
        rep_forge = client.get("/demarrer")
        assert rep_forge.status_code == 200, f"cookie {forge!r} → {rep_forge.status_code}"
        assert "Mission en cours" not in rep_forge.text
    # Invariant démo/réel : une mission RÉELLE n'est jamais proposée en mode
    # démo (fuite d'un nom de mission client en pleine présentation sinon).
    client.cookies.set("derniere_mission", str(mid))
    assert "Mission en cours" in client.get("/demarrer").text  # mode réel : proposée
    client.cookies.set("mode", "demo")
    en_demo = client.get("/demarrer")
    assert en_demo.status_code == 200 and "Mission en cours" not in en_demo.text
    client.cookies.delete("mode")


def test_accueil_et_demarrer_sont_distincts(client: TestClient) -> None:
    """Défer item 10 soldé : « / » (choix du mode) ne se présente plus comme un
    second écran « Démarrer » — titres distincts + lien croisé de changement de
    mode sur /demarrer."""
    accueil = client.get("/")
    assert "Dans quel mode travailler" in accueil.text
    demarrer = client.get("/demarrer")
    assert "<h1>Démarrer</h1>" in demarrer.text
    assert "Mode actuel" in demarrer.text and "en changer" in demarrer.text


def test_filtre_pluriel_bordures() -> None:
    """Filtre Jinja `pluriel` (revue UX 2026-07-23 P2-13, ajouté sans test —
    revue adversariale) : accord correct aux bordures, jamais d'exception —
    une entrée inattendue rend '' (singulier), pas un crash de template."""
    from app.templating import _pluriel

    assert _pluriel(0) == "" and _pluriel(1) == ""
    assert _pluriel(2) == "s" and _pluriel(2, "x") == "x"
    assert _pluriel("3") == "s" and _pluriel("3.0") == "s"  # str et str-float
    assert _pluriel(1.9) == ""  # tronqué, pas arrondi — 1.9 « thème » reste singulier
    assert _pluriel(None) == "" and _pluriel("abc") == "" and _pluriel([1, 2]) == ""


def test_autosave_erreur_couvre_status_ind(client: TestClient) -> None:
    """Revue UX 2026-07-23 (P1-2) : l'échec d'autosave d'une RÉPONSE de capture
    (cible #status-{id}, classe .status-ind — le formulaire le plus utilisé de
    l'app) était invisible : autosave.js ne remontait l'erreur que sur .saved.
    Le script doit désormais traiter aussi .status-ind, sans écraser le badge."""
    served = client.get("/static/autosave.js")
    assert served.status_code == 200
    assert "status-ind" in served.text, "autosave.js ignore les cibles .status-ind"
    # L'erreur vit dans un slot DÉDIÉ .autosave-err — réutiliser .saved écrasait
    # l'horodatage « enr. HH:MM » du badge (revue adversariale 2026-07-23).
    assert "autosave-err" in served.text, "l'erreur doit avoir son slot dédié, pas écraser .saved"
    # Et la cible réelle du template porte bien cette classe (couplage vérifié).
    capture = (TEMPLATES / "interviews" / "capture.html").read_text(encoding="utf-8")
    assert 'class="status-ind" id="status-' in capture


def test_fraicheur_empreinte_python_servie_et_sensible_au_mtime(client: TestClient) -> None:
    """GET /__fraicheur (diagnostic superviseur 2026-07-23 — le --reload a servi
    plusieurs fois du code périmé) : l'empreinte SERVIE est celle capturée à
    l'import et vaut celle du disque tant que rien n'a changé ; toucher le mtime
    d'un .py d'app/ change l'empreinte DISQUE (c'est l'écart servi≠disque qui
    prouve un serveur périmé). Le mtime est restauré à l'octet près."""
    import os
    from app import main as app_main

    rep = client.get("/__fraicheur")
    assert rep.status_code == 200
    servie = rep.json()["empreinte"]
    assert servie == app_main.EMPREINTE_AU_CHARGEMENT
    assert servie == app_main.empreinte_code(), "disque et import divergent sans modification"
    cible = app_main.BASE_DIR / "models.py"
    st = cible.stat()
    try:
        os.utime(cible, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000))
        assert app_main.empreinte_code() != servie, (
            "l'empreinte disque ignore un mtime modifié — le stale serait indétectable"
        )
    finally:
        os.utime(cible, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_apercu_fiche_parite_chips_et_bandeau_resultats(client: TestClient) -> None:
    """Parité aperçu/PPT de la fiche reco (revue adversariale) : l'aperçu web
    doit refléter la fiche RÉELLE — chips Valeur/Complexité (plus de jauges
    donut) et résultats attendus en bandeau bas pleine largeur (plus dans la
    colonne gauche)."""
    apercu = (TEMPLATES / "synthese" / "apercu.html").read_text(encoding="utf-8")
    assert "ppt-chip" in apercu, "aperçu fiche sans chips Valeur/Complexité"
    assert "ppt-gauge" not in apercu, "l'aperçu montre encore les jauges donut supprimées du PPT"
    assert "ppt-strip" in apercu, "aperçu fiche sans bandeau résultats pleine largeur"
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".ppt-chip" in css and ".ppt-strip" in css, "styles chips/bandeau absents d'app.css"
    assert ".ppt-gauge" not in css, "CSS mort des jauges donut encore présent"
