"""Génération IA des constats qualifiés — tranche 4 de I2
(docs/reflexions/spec-restitution-defendable.md, l.105-115 et l.129-134).

Faux client IA déterministe partout : on fige (1) les identifiants stables
d'entretien exposés au modèle et le REJET de ceux qu'il invente, (2) la garde
`edite` (un constat retouché à la main n'est jamais écrasé), (3) une sortie
vide qui n'écrase rien, (4) l'étape 2 — la reco cite ses constats par id, un
id inconnu est rejeté, et la régénération recrée les liens.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, SessionLocal, engine, init_db
from app.main import app
from app.models import GlobalSynthesis, Interview, Mission, MissionConstat
from app.services import global_synthesis_job as job
from app.services import synthese_ai
from app.services.constats import apply_constats_ia, constats_par_axe
from app.services.synthese_ai import SynthesisAIError
from app.services.synthese_ecriture import apply_recommendations_result


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


def _mission(nom: str, noms: list[str]) -> tuple[int, list[int]]:
    db = SessionLocal()
    try:
        mission = Mission(name=nom)
        db.add(mission)
        db.commit()
        ids = []
        for n in noms:
            iv = Interview(mission_id=mission.id, interviewee_name=n, mode="libre",
                           status="done", repartition={"contexte": f"Propos de {n}."})
            db.add(iv)
            db.commit()
            ids.append(iv.id)
        return mission.id, ids
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Étape 1 — génération
# --------------------------------------------------------------------------- #
def _libre(ids_noms):
    return [(SimpleNamespace(id=i, interviewee_name=n), {"contexte": f"Propos de {n}."})
            for i, n in ids_noms]


def test_le_modele_voit_des_ids_stables_et_un_id_hallucine_est_rejete(monkeypatch) -> None:
    prompts = []

    def faux(system, prompt, schema, hint, max_tokens=0):
        prompts.append(prompt)
        return {"constats": [
            {"axe": "contexte", "type": "consensus", "libelle": "Cap partagé",
             "interviewes": ["E11", "[E12]", "E999"]},
            {"axe": "Contexte", "type": "écart", "libelle": "Rythme contesté",
             "interviewes": ["E12", "Bob"]},
            {"axe": "axe_inconnu", "type": "consensus", "libelle": "Hors rubrique",
             "interviewes": ["E11"]},
        ]}

    monkeypatch.setattr(synthese_ai, "_call_claude", faux)
    mission = SimpleNamespace(name="M", interviews=[SimpleNamespace(id=11), SimpleNamespace(id=12)])
    out = synthese_ai.generate_constats(mission, [], _libre([(11, "Alix"), (12, "Bao")]))

    assert "[E11]" in prompts[0] and "[E12]" in prompts[0]
    assert "[E11] Alix" not in prompts[0] and "[E12] Bao" not in prompts[0]  # T9 : id seul
    assert [(c["axe_key"], c["type"], c["interview_ids"]) for c in out["constats"]] == [
        ("contexte", "consensus", [11, 12]),
        ("contexte", "ecart", [12]),
    ], "l'axe inconnu est écarté, le libellé d'axe est ramené à sa clé"
    assert out["constats"][0]["ids_rejetes"] == [999]
    assert sorted(map(str, out["ids_rejetes"])) == ["999", "Bob"]


def test_la_synthese_globale_ne_voit_toujours_que_les_noms() -> None:
    """Le prompt de synthèse (et l'empreinte de son cache) reste inchangé."""
    blocs = synthese_ai._global_material_blocks([], _libre([(11, "Alix")]))
    assert blocs == ["=== ENTRETIEN LIBRE : Alix ===\nContexte : Propos de Alix."]


def test_constats_de_plusieurs_troncons_fusionnes(monkeypatch) -> None:
    reponses = iter([
        {"constats": [{"axe": "contexte", "type": "consensus", "libelle": "Cap partagé",
                       "interviewes": ["E1"]}]},
        {"constats": [{"axe": "contexte", "type": "consensus", "libelle": "cap  partagé",
                       "interviewes": ["E2"]}]},
    ])
    monkeypatch.setattr(synthese_ai, "_call_claude", lambda *a, **k: next(reponses))
    monkeypatch.setattr(synthese_ai, "ollama_chunk_max_words", lambda: 12)
    mission = SimpleNamespace(name="M", interviews=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
    out = synthese_ai.generate_constats(mission, [], _libre([(1, "Alix"), (2, "Bao")]))
    assert len(out["constats"]) == 1
    assert out["constats"][0]["interview_ids"] == [1, 2]


# --------------------------------------------------------------------------- #
# Écriture — garde `edite`, sortie vide, rejet visible
# --------------------------------------------------------------------------- #
def test_constat_edite_preserve_et_ids_rejetes_visibles_hors_decompte() -> None:
    mid, (a, b, c) = _mission("Garde", ["Alix", "Bao", "Chloé"])
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        mission.constats.append(MissionConstat(axe_key="contexte", type="ecart",
                                               libelle="Cap partagé", edite=True))
        mission.constats.append(MissionConstat(axe_key="contexte", type="consensus",
                                               libelle="Ancien constat IA"))
        db.commit()
        ecrit = apply_constats_ia(db, mission, [
            {"axe_key": "contexte", "type": "consensus", "libelle": "cap partagé",
             "interview_ids": [a, b, c], "ids_rejetes": []},
            {"axe_key": "contexte", "type": "consensus", "libelle": "Outils vieillissants",
             "interview_ids": [a, b], "ids_rejetes": [999]},
        ])
        db.commit()
        db.expire_all()
        mission = db.get(Mission, mid)
        libelles = {(x.libelle, x.type, x.edite) for x in mission.constats}
        assert ecrit is True
        assert libelles == {("Cap partagé", "ecart", True),
                            ("Outils vieillissants", "consensus", False)}
        ligne = next(l for l in constats_par_axe(mission)["contexte"]
                     if l["constat"].libelle == "Outils vieillissants")
        assert (ligne["n"], ligne["m"]) == (2, 3)
        assert "E999" in ligne["constat"].noms_non_rattaches
    finally:
        db.close()


def test_sortie_vide_n_ecrase_rien() -> None:
    mid, _ = _mission("Vide", ["Alix"])
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        mission.constats.append(MissionConstat(axe_key="contexte", type="consensus",
                                               libelle="Existant"))
        db.commit()
        assert apply_constats_ia(db, mission, []) is False
        db.commit()
        db.expire_all()
        assert [x.libelle for x in db.get(Mission, mid).constats] == ["Existant"]
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Tâche de fond — la synthèse enchaîne les constats
# --------------------------------------------------------------------------- #
def _mission_avec_synthese(nom: str) -> tuple[int, list[int]]:
    mid, ids = _mission(nom, ["Alix", "Bao"])
    db = SessionLocal()
    try:
        db.add(GlobalSynthesis(mission_id=mid, generation_status="running"))
        db.commit()
    finally:
        db.close()
    return mid, ids


def test_le_job_ecrit_les_constats_apres_la_synthese(monkeypatch) -> None:
    mid, (a, b) = _mission_avec_synthese("Job")
    monkeypatch.setattr(job, "generate_global_synthesis",
                        lambda *a_, **k: {"contexte": "- Synthèse"})
    vus = {}

    def faux_constats(mission, material_by_theme, material_libre, axes=None):
        vus["libre"] = [iv.id for iv, _ in material_libre]
        return {"constats": [{"axe_key": "contexte", "type": "consensus",
                              "libelle": "Cap partagé", "interview_ids": [a, b],
                              "ids_rejetes": []}], "ids_rejetes": []}

    monkeypatch.setattr(job, "generate_constats", faux_constats)
    job.run_global_synthesis_job(mid)
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        assert vus["libre"] == [a, b]
        assert mission.global_synthesis.generation_status == "idle"
        assert [(c.libelle, [iv.id for iv in c.interviews]) for c in mission.constats] == [
            ("Cap partagé", [a, b])]
    finally:
        db.close()


def test_echec_des_constats_garde_la_synthese_et_le_dit(monkeypatch) -> None:
    mid, _ = _mission_avec_synthese("JobEchec")
    monkeypatch.setattr(job, "generate_global_synthesis",
                        lambda *a_, **k: {"contexte": "- Synthèse écrite"})

    def boum(*a_, **k):
        raise SynthesisAIError("délai dépassé")

    monkeypatch.setattr(job, "generate_constats", boum)
    job.run_global_synthesis_job(mid)
    db = SessionLocal()
    try:
        gs = db.get(Mission, mid).global_synthesis
        assert gs.contenu("contexte") == "- Synthèse écrite"
        assert gs.generation_status == "error"
        assert "constats non générés" in gs.generation_error
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Étape 2 — la reco cite ses constats par identifiant
# --------------------------------------------------------------------------- #
_RECO = {"title": "R", "objectif": "o", "acteurs": "a", "valeur": 3, "complexite": 2,
         "proposition_valeur": "p", "plan_actions": "- x", "resultats_attendus": "- y"}


def test_reco_cite_ses_constats_et_un_id_inconnu_est_rejete(monkeypatch) -> None:
    prompts = []

    def faux(system, prompt, schema, hint, max_tokens=0):
        prompts.append((system, prompt))
        return {"axes": [{"title": "Axe", "recommendations": [
            {**_RECO, "constats": ["C7", "C404", "n'importe quoi"]}]}]}

    monkeypatch.setattr(synthese_ai, "_call_claude", faux)
    gs = SimpleNamespace(contenu=lambda k: "- matière" if k == "contexte" else "")
    constat = SimpleNamespace(id=7, axe_key="contexte", type="consensus", libelle="Cap partagé")
    axes = synthese_ai.generate_recommendations(gs, None, constats=[constat])
    assert "[C7] (Contexte, consensus) Cap partagé" in prompts[0][1]
    assert axes[0]["recommendations"][0]["constat_ids"] == [7]


def test_reco_sans_constats_prompt_et_sortie_inchanges(monkeypatch) -> None:
    prompts = []

    def faux(system, prompt, schema, hint, max_tokens=0):
        prompts.append(prompt)
        return {"axes": [{"title": "Axe", "recommendations": [dict(_RECO)]}]}

    monkeypatch.setattr(synthese_ai, "_call_claude", faux)
    gs = SimpleNamespace(contenu=lambda k: "- matière" if k == "contexte" else "")
    axes = synthese_ai.generate_recommendations(gs)
    assert "CONSTATS" not in prompts[0]
    assert "constat_ids" not in axes[0]["recommendations"][0]


def test_regeneration_des_recos_recree_les_liens(monkeypatch) -> None:
    mid, _ = _mission("Recos", ["Alix"])
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        db.add(GlobalSynthesis(mission_id=mid, contexte="- Synthèse", status="generated"))
        mission.constats.append(MissionConstat(axe_key="contexte", type="consensus",
                                               libelle="Cap partagé"))
        db.commit()
        cid = mission.constats[0].id
    finally:
        db.close()

    from app.routers import synthese as routes

    recus = {}

    def faux(global_synthesis, axes=None, constats=None):
        recus["constats"] = [c.id for c in constats or []]
        return [{"title": "Axe", "recommendations": [{**_RECO, "constat_ids": [cid]}]}]

    monkeypatch.setattr(routes, "generate_recommendations", faux)
    monkeypatch.setattr(routes, "ai_precondition_error", lambda *a, **k: None)
    r = TestClient(app).post(f"/missions/{mid}/recommandations/generate")
    assert r.status_code == 200
    assert recus["constats"] == [cid]
    db = SessionLocal()
    try:
        reco = db.get(Mission, mid).recommendation_axes[0].recommendations[0]
        assert [c.id for c in reco.constats] == [cid]
    finally:
        db.close()


def test_apply_recos_ignore_un_id_d_une_autre_mission() -> None:
    mid, _ = _mission("Etrangere", ["Alix"])
    autre, _ = _mission("Autre", ["Bao"])
    db = SessionLocal()
    try:
        m_autre = db.get(Mission, autre)
        m_autre.constats.append(MissionConstat(axe_key="contexte", type="consensus",
                                               libelle="Ailleurs"))
        db.commit()
        cid_autre = m_autre.constats[0].id
        mission = db.get(Mission, mid)
        apply_recommendations_result(db, mission, [
            {"title": "Axe", "recommendations": [{**_RECO, "constat_ids": [cid_autre]}]}])
        db.commit()
        db.expire_all()
        reco = db.get(Mission, mid).recommendation_axes[0].recommendations[0]
        assert reco.constats == []
    finally:
        db.close()


@pytest.mark.parametrize("brut,attendu", [("écart", "ecart"), ("Consensus", "consensus"),
                                          ("divergence", "ecart"), ("autre", None)])
def test_normalisation_du_type(brut, attendu) -> None:
    assert synthese_ai._norm_type_constat(brut) == attendu


def test_reco_ia_aux_champs_en_liste_est_aplatie_en_puces(monkeypatch) -> None:
    """Mesure réelle 2026-09-29 : qwen2.5:3b renvoie `plan_actions` en LISTE ;
    `.strip()` levait AttributeError et aucune reco n'était générée."""
    def faux(system, prompt, schema, hint, max_tokens=0):
        return {"axes": [{"title": "Axe", "recommendations": [
            {**_RECO, "plan_actions": ["Cadrer le mandat", "Tenir un comité"],
             "resultats_attendus": ["Moins d'escalades"]}]}]}

    monkeypatch.setattr(synthese_ai, "_call_claude", faux)
    gs = SimpleNamespace(contenu=lambda k: "- matière" if k == "contexte" else "")
    reco = synthese_ai.generate_recommendations(gs, None)[0]["recommendations"][0]
    assert "Cadrer le mandat" in reco["plan_actions"]
    assert "Tenir un comité" in reco["plan_actions"]
    assert "Moins d'escalades" in reco["resultats_attendus"]


def test_reco_ia_au_titre_en_liste_reste_sur_une_ligne(monkeypatch) -> None:
    def faux(system, prompt, schema, hint, max_tokens=0):
        return {"axes": [{"title": "Axe", "recommendations": [
            {**_RECO, "title": ["Gouverner", "les données"]}]}]}

    monkeypatch.setattr(synthese_ai, "_call_claude", faux)
    gs = SimpleNamespace(contenu=lambda k: "- matière" if k == "contexte" else "")
    titre = synthese_ai.generate_recommendations(gs, None)[0]["recommendations"][0]["title"]
    assert titre == "Gouverner ; les données"


def test_le_job_ne_remplace_pas_des_constats_importes(monkeypatch) -> None:
    """Arbitrage 2026-09-29 (option A) : des constats importés, non édités,
    ne sont jamais remplacés par une régénération de la synthèse ; l'IA n'est
    même pas appelée pour eux."""
    mid, _ = _mission_avec_synthese("JobImporte")
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        mission.constats.append(MissionConstat(
            axe_key="contexte", type="consensus", libelle="Importé"))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(job, "generate_global_synthesis",
                        lambda *a_, **k: {"contexte": "- Synthèse"})
    appels = []

    def faux_constats(*a_, **k):
        appels.append(1)
        return {"constats": [{"axe_key": "contexte", "type": "ecart", "libelle": "IA",
                              "interview_ids": [], "ids_rejetes": []}], "ids_rejetes": []}

    monkeypatch.setattr(job, "generate_constats", faux_constats)
    job.run_global_synthesis_job(mid)
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        assert [c.libelle for c in mission.constats] == ["Importé"]
        assert appels == []
        assert mission.global_synthesis.generation_status == "idle"
    finally:
        db.close()


def test_les_ids_prefixes_sont_lus_meme_entoures_de_texte() -> None:
    """Revue 2026-09-29 : « E12 (Dupont) » ou « E12, E14 » étaient rejetés en
    bloc. Un texte sans identifiant reste rejeté tel quel."""
    lu = synthese_ai._parse_ids(["E12 (Dupont)", "E12, e14", "[E3]", "7", "Dupont"],
                                synthese_ai._ID_ENTRETIEN_RE)
    assert lu == [12, 12, 14, 3, 7, "Dupont"]
    assert synthese_ai._parse_ids(["C4 : cap"], synthese_ai._ID_CONSTAT_RE) == [4]
    # Le préfixe d'un AUTRE type n'est pas pris : « C4 » n'est pas un entretien.
    assert synthese_ai._parse_ids(["C4"], synthese_ai._ID_ENTRETIEN_RE) == ["C4"]


def test_la_consigne_json_nomme_le_champ_constats_seulement_s_il_y_en_a(monkeypatch) -> None:
    """Mesure réelle 2026-09-30 : sans cette phrase, 0 reco sur 6 citait un
    constat ; avec, 6 sur 6. Sans constats, la consigne ne change pas."""
    hints = []

    def faux(system, prompt, schema, hint, max_tokens=0):
        hints.append(hint)
        return {"axes": []}

    monkeypatch.setattr(synthese_ai, "_call_claude", faux)
    gs = SimpleNamespace(contenu=lambda k: "- matière" if k == "contexte" else "")
    constat = SimpleNamespace(id=7, axe_key="contexte", type="consensus", libelle="Cap")
    synthese_ai.generate_recommendations(gs, None, constats=[constat])
    synthese_ai.generate_recommendations(gs, None)
    assert '"constats"' in hints[0] and "C<id>" in hints[0]
    assert hints[1] == synthese_ai.RECO_JSON_HINT


def test_un_nombre_nu_a_cote_d_un_id_prefixe_est_compte_rejete() -> None:
    """Revue 2026-09-30 : dans « Interview 3 et E5 », le 3 disparaissait sans
    trace. E5 est lu ; le texte entier est rendu pour être compté rejeté."""
    lu = synthese_ai._parse_ids(["Interview 3 et E5"], synthese_ai._ID_ENTRETIEN_RE)
    assert lu == [5, "Interview 3 et E5"]
    assert synthese_ai._parse_ids(["E12 (Dupont)"], synthese_ai._ID_ENTRETIEN_RE) == [12]
    # Un chiffre DANS une parenthèse est un commentaire (revue 2026-09-30).
    assert synthese_ai._parse_ids(["E12 (2 fois)"], synthese_ai._ID_ENTRETIEN_RE) == [12]


# --------------------------------------------------------------------------- #
# Régénération : une reco de même titre garde ses liens (choix A, 2026-10-01)
# --------------------------------------------------------------------------- #
def _mission_avec_constats(nom: str, libelles: list[str]) -> tuple[int, list[int]]:
    mid, _ = _mission(nom, ["Alix"])
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        for lib in libelles:
            mission.constats.append(MissionConstat(axe_key="contexte", type="consensus",
                                                   libelle=lib))
        db.commit()
        return mid, [c.id for c in mission.constats]
    finally:
        db.close()


def _appliquer(mid: int, axes: list[dict], **kw) -> list:
    db = SessionLocal()
    try:
        apply_recommendations_result(db, db.get(Mission, mid), axes, **kw)
        db.commit()
        db.expire_all()
        return [(r.title, sorted(c.id for c in r.constats), r.constats_non_rattaches)
                for a in db.get(Mission, mid).recommendation_axes for r in a.recommendations]
    finally:
        db.close()


def _reco(titre: str, **extra) -> dict:
    return {**_RECO, "title": titre, **extra}


def test_regeneration_garde_les_liens_d_une_reco_de_meme_titre_et_ajoute_ceux_de_l_ia() -> None:
    mid, (c1, c2) = _mission_avec_constats("Garde", ["Cap partagé", "Rythme"])
    _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("Clarifier le cap", constat_ids=[c1])]}])
    # Titre retrouvé malgré casse/espaces, axe renommé : la clé est le titre seul.
    apres = _appliquer(mid, [{"title": "Autre axe", "recommendations": [
        _reco("clarifier  le CAP", constat_ids=[c2])]}], conserver_liens=True)
    assert apres[0][1] == sorted([c1, c2])


def test_regeneration_titre_change_ne_herite_de_rien() -> None:
    mid, (c1,) = _mission_avec_constats("Renomme", ["Cap partagé"])
    _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("Clarifier le cap", constat_ids=[c1])]}])
    apres = _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("Autre chose")]}],
                       conserver_liens=True)
    assert apres[0][1] == []


def test_regeneration_titre_en_double_n_herite_de_rien() -> None:
    """Choix 2b : deux recos d'avant au même titre — on ne sait pas laquelle
    est laquelle, aucun lien n'est reporté plutôt qu'un faux."""
    mid, (c1, c2) = _mission_avec_constats("Double", ["Cap partagé", "Rythme"])
    _appliquer(mid, [{"title": "Axe", "recommendations": [
        _reco("Clarifier le cap", constat_ids=[c1]), _reco("Clarifier le cap", constat_ids=[c2])]}])
    apres = _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("Clarifier le cap")]}],
                       conserver_liens=True)
    assert apres[0][1] == []


def test_regeneration_garde_les_libelles_non_rattaches() -> None:
    mid, _ = _mission_avec_constats("NonRat", ["Cap partagé"])
    _appliquer(mid, [{"title": "Axe", "recommendations": [
        _reco("Clarifier le cap", constats=["Constat disparu"])]}])
    apres = _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("Clarifier le cap")]}],
                       conserver_liens=True)
    assert apres[0][2] == "Constat disparu"


def test_regeneration_ne_relie_pas_un_constat_sorti_de_la_mission() -> None:
    mid, (c1, c2) = _mission_avec_constats("Sorti", ["Cap partagé", "Rythme"])
    _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("Clarifier le cap", constat_ids=[c1, c2])]}])
    db = SessionLocal()
    try:
        db.delete(db.get(MissionConstat, c1))
        db.commit()
    finally:
        db.close()
    apres = _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("Clarifier le cap")]}],
                       conserver_liens=True)
    assert apres[0][1] == [c2]


def test_import_sans_conserver_liens_le_fichier_fait_foi() -> None:
    """Choix 1a : l'import Markdown n'hérite pas — seuls les liens du fichier."""
    mid, (c1, c2) = _mission_avec_constats("Import", ["Cap partagé", "Rythme"])
    _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("Clarifier le cap", constat_ids=[c1])]}])
    apres = _appliquer(mid, [{"title": "Axe", "recommendations": [
        _reco("Clarifier le cap", constats=["Rythme"])]}])
    assert apres[0][1] == [c2]


def test_route_regeneration_conserve_les_liens(monkeypatch) -> None:
    mid, (c1, c2) = _mission_avec_constats("Route", ["Cap partagé", "Rythme"])
    db = SessionLocal()
    try:
        db.add(GlobalSynthesis(mission_id=mid, contexte="- Synthèse", status="generated"))
        db.commit()
    finally:
        db.close()
    _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("Clarifier le cap", constat_ids=[c1])]}])

    from app.routers import synthese as routes

    monkeypatch.setattr(routes, "generate_recommendations", lambda gs, axes=None, constats=None: [
        {"title": "Axe", "recommendations": [_reco("Clarifier le cap", constat_ids=[c2])]}])
    monkeypatch.setattr(routes, "ai_precondition_error", lambda *a, **k: None)
    r = TestClient(app).post(f"/missions/{mid}/recommandations/generate")
    assert r.status_code == 200
    db = SessionLocal()
    try:
        reco = db.get(Mission, mid).recommendation_axes[0].recommendations[0]
        assert sorted(c.id for c in reco.constats) == sorted([c1, c2])
    finally:
        db.close()


def test_regeneration_deux_recos_neuves_de_meme_titre_n_heritent_de_rien() -> None:
    """Revue 2026-10-01 : la règle 2b vaut aussi pour la sortie neuve."""
    mid, (c1,) = _mission_avec_constats("DoubleNeuf", ["Cap partagé"])
    _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("Clarifier le cap", constat_ids=[c1])]}])
    apres = _appliquer(mid, [{"title": "Axe", "recommendations": [
        _reco("Clarifier le cap"), _reco("Clarifier le cap")]}], conserver_liens=True)
    assert [r[1] for r in apres] == [[], []]


def test_regeneration_ne_reprend_pas_un_non_rattache_devenu_constat() -> None:
    """Revue 2026-10-01 : un libellé hérité qui existe désormais comme constat
    ne reste pas affiché « introuvable »."""
    mid, _ = _mission_avec_constats("Devenu", ["Rythme"])
    _appliquer(mid, [{"title": "Axe", "recommendations": [
        _reco("Clarifier le cap", constats=["Cap partagé"])]}])
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        mission.constats.append(MissionConstat(axe_key="contexte", type="consensus",
                                               libelle="Cap partagé"))
        db.commit()
        cid = mission.constats[-1].id
    finally:
        db.close()
    apres = _appliquer(mid, [{"title": "Axe", "recommendations": [
        _reco("Clarifier le cap", constat_ids=[cid])]}], conserver_liens=True)
    assert apres[0][1] == [cid]
    assert apres[0][2] == ""


def test_regeneration_reco_sans_titre_n_herite_de_rien() -> None:
    """Revue 2026-10-01 : une cle vide (titre vide/None) n'apparie jamais."""
    mid, (c1,) = _mission_avec_constats("SansTitre", ["Cap partagé"])
    _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("", constat_ids=[c1])]}])
    for titre in ("", "   "):
        apres = _appliquer(mid, [{"title": "Axe", "recommendations": [_reco(titre)]}],
                           conserver_liens=True)
        assert apres[0][1] == []


def test_regeneration_relie_un_non_rattache_devenu_constat_meme_sans_id_ia() -> None:
    """Revue 2026-10-01 : l'IA ne le relie pas, le libelle herite correspond
    a un constat de la mission -> lien (pas de doublon, pas de texte)."""
    mid, _ = _mission_avec_constats("DevenuLie", ["Rythme"])
    _appliquer(mid, [{"title": "Axe", "recommendations": [
        _reco("Clarifier le cap", constats=["Cap partagé"])]}])
    db = SessionLocal()
    try:
        mission = db.get(Mission, mid)
        mission.constats.append(MissionConstat(axe_key="contexte", type="consensus",
                                               libelle="Cap partagé"))
        db.commit()
        cid = mission.constats[-1].id
    finally:
        db.close()
    apres = _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("Clarifier le cap")]}],
                       conserver_liens=True)
    assert apres[0][1] == [cid]
    assert apres[0][2] == ""

def test_regeneration_ne_relie_pas_un_constat_d_une_autre_mission() -> None:
    """Revue 2026-10-01 : la garde `c in mission.constats` doit tenir meme quand
    un lien herite pointe un constat hors mission (supprimer le constat ne
    suffit pas a l'exercer : le CASCADE efface deja le lien)."""
    mid, (c1, c2) = _mission_avec_constats("Garde1", ["Cap partagé", "Rythme"])
    _, (etranger,) = _mission_avec_constats("Garde2", ["Ailleurs"])
    _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("Clarifier le cap", constat_ids=[c1])]}])
    db = SessionLocal()
    try:
        reco = db.get(Mission, mid).recommendation_axes[0].recommendations[0]
        reco.constats.append(db.get(MissionConstat, etranger))
        db.commit()
    finally:
        db.close()
    apres = _appliquer(mid, [{"title": "Axe", "recommendations": [_reco("Clarifier le cap")]}],
                       conserver_liens=True)
    assert apres[0][1] == [c1]
