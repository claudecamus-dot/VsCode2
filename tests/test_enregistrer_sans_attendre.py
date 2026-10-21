"""« Enregistrer l'entretien sans attendre la transcription » (2026-10-19).

Besoin : enchaîner l'entretien suivant sans attendre que tout soit traité.
N1 (JS EXÉCUTÉ sous node) : la transcription en vol ne grise plus le bouton quand
l'audio complet est sur le serveur ; une tranche en envoi ou une copie locale seule
le grisent encore. N2 : texte vide accepté SI l'audio est sur le serveur.
N3 : lien « Nouvel entretien » et purge du brouillon envoyé."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, RECORDINGS_DIR, SessionLocal, engine, init_db
from app.main import app
from app.models import AudioFileJob, Interview, Mission

RACINE = Path(__file__).resolve().parent.parent
LIBRE = RACINE / "app" / "templates" / "interviews" / "record_libre.html"
REC_DRAFT = RACINE / "app" / "static" / "rec_draft.js"


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


def _fonction(src: str, nom: str) -> str:
    m = re.search(r"function\s+%s\s*\(" % nom, src)
    assert m
    i = src.index("{", m.start())
    prof = 0
    for j in range(i, len(src)):
        prof += (src[j] == "{") - (src[j] == "}")
        if prof == 0:
            return src[m.start(): j + 1]
    raise AssertionError


def _etat(texte="x", en_vol=0, job_submits=0, pending_backups=0, finalizing=False,
          serveur=1, locaux=0, confirmes=None, restauree=False) -> dict:
    if shutil.which("node") is None:
        pytest.fail("node est requis pour exécuter le JS de l'écran.")
    code = _fonction(LIBRE.read_text(encoding="utf-8"), "updateSubmitState")
    script = """
var transcriptSoFar=%s, pendingSegments=%d, pendingSegmentJobSubmits=%d, pendingBackups=%d,
    finalizing=%s, recordingActive=false, missionAbsente=false, backupLocalAcquitte=false,
    backupConfirmes=%d, sessionRestauree=%s;
var lostSegments=[], lostRetryBlocking=0;
var backupSegments=%s, backupPerdusLocaux=%s;
var submitBtn={disabled:null}, pdfBtn={disabled:null}, statusEl={textContent:''};
%s
updateSubmitState();
console.log(JSON.stringify({disabled: submitBtn.disabled, msg: statusEl.textContent}));
""" % (
        json.dumps(texte), en_vol, job_submits, pending_backups, json.dumps(finalizing),
        serveur if confirmes is None else confirmes, json.dumps(restauree),
        json.dumps([{"filename": "a.webm", "position": 0}] * serveur),
        json.dumps([{"position": 1}] * locaux), code,
    )
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr[:2000]
    return json.loads(res.stdout.strip())


# ---------------------------------------------------------------- N1 (JS exécuté)
def test_transcription_en_vol_avec_copie_serveur_ne_grise_pas():
    etat = _etat(en_vol=2, job_submits=1, serveur=1)
    assert etat["disabled"] is False
    assert "la transcription se termine en arrière-plan" in etat["msg"]


def test_transcription_en_vol_sans_copie_serveur_reste_grise():
    assert _etat(en_vol=1, serveur=0)["disabled"] is True


def test_envoi_de_tranche_en_cours_grise_et_dit_pourquoi():
    etat = _etat(pending_backups=1, serveur=2)
    assert etat["disabled"] is True
    assert "Envoi de l’audio en cours (2/3)" in etat["msg"]
    assert "ne ferme pas l’onglet" in etat["msg"]


def test_copie_locale_non_transmise_grise_meme_avec_transcription_en_vol():
    assert _etat(en_vol=1, serveur=1, locaux=1)["disabled"] is True


def test_derniere_tranche_non_livree_grise():
    assert _etat(finalizing=True, serveur=1)["disabled"] is True


def test_brouillon_restaure_sans_envoi_confirme_ne_compte_pas_comme_copie_serveur():
    # backupSegments rechargé d'un brouillon (onglet planté) : la dernière tranche peut manquer.
    assert _etat(en_vol=1, serveur=2, confirmes=0)["disabled"] is True
    assert _etat(texte="", serveur=2, confirmes=0)["disabled"] is True


def test_session_restauree_plus_une_tranche_envoyee_n_affirme_pas_la_copie_serveur():
    etat = _etat(en_vol=1, serveur=3, confirmes=1, restauree=True)
    assert etat["disabled"] is True
    assert _etat(texte="", serveur=3, confirmes=1, restauree=True)["disabled"] is True
    # texte present, rien en vol : enregistrable (le confirm() previent au clic)
    assert _etat(en_vol=0, serveur=3, confirmes=1, restauree=True)["disabled"] is False


def test_le_clic_d_une_session_restauree_demande_confirmation():
    src = LIBRE.read_text(encoding="utf-8")
    i = src.index("getElementById('record-form').addEventListener('submit'")
    assert "sessionRestauree" in src[i: i + 700] and "peut être incomplet" in src[i: i + 700]


def test_rien_a_sauver_grise():
    assert _etat(texte="", serveur=0)["disabled"] is True


def test_audio_sur_serveur_sans_texte_degrise():
    assert _etat(texte="", en_vol=1, serveur=1)["disabled"] is False


def test_le_clic_confirme_aussi_quand_la_transcription_est_en_vol():
    src = LIBRE.read_text(encoding="utf-8")
    i = src.index("getElementById('record-form').addEventListener('submit'")
    assert "enVol" in src[i: i + 900] and "confirm(" in src[i: i + 900]


# ---------------------------------------------------------------- N2 (serveur)
def _mission(client: TestClient) -> int:
    r = client.post("/entretiens/libre/nouveau", follow_redirects=False)
    mid = int(r.headers["location"].split("/")[2])
    with SessionLocal() as db:  # hors brouillon : le 303 mène à la fiche, pas à /finaliser
        db.get(Mission, mid).is_draft = False
        db.commit()
    return mid


def _audio(mission_id: int, nom: str) -> str:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    fichier = f"{mission_id}_{nom}"
    (RECORDINGS_DIR / fichier).write_bytes(b"x")
    return fichier


def _poster(client, mission_id, transcript, segments, token, **extra):
    return client.post(
        f"/missions/{mission_id}/interviews/record-libre/enregistrer",
        data={"transcript": transcript, "session_token": token,
              "audio_segments": json.dumps(segments), **extra},
        follow_redirects=False,
    )


def test_texte_vide_avec_audio_serveur_cree_l_entretien_sans_ia(monkeypatch):
    appels = []
    monkeypatch.setattr("app.routers.interviews_libre.planifier_structuration",
                        lambda i: appels.append(i))
    client = TestClient(app)
    mid = _mission(client)
    fichier = _audio(mid, "sans-attendre.webm")
    r = _poster(client, mid, "  ", [{"filename": fichier, "position": 0}], "tok-sa-1",
                audio_attendu="1")
    assert r.status_code == 303, r.text[:300]
    with SessionLocal() as db:
        it = db.query(Interview).filter(Interview.mission_id == mid).one()
        assert not (it.raw_transcript or "").strip()
        assert [s["filename"] for s in it.audio_segments] == [fichier]
        assert it.structuration_status == "a_transcrire"
    assert appels == []
    # la fiche explique et propose la retranscription depuis l'audio
    page = client.get(r.headers["location"]).text
    assert "Relancer la transcription (audio)" in page
    assert "Transcription incomplète" in page
    assert "Générer l'aperçu" not in page and "Structurer maintenant" not in page
    assert f"/missions/{mid}/interviews/record-libre" in page  # N3 : Nouvel entretien


def test_retranscription_fonctionne_sur_entretien_a_texte_vide(monkeypatch):
    monkeypatch.setattr("app.routers.interviews_retranscription.run_audio_file_job",
                        lambda job_id: None)
    client = TestClient(app)
    mid = _mission(client)
    fichier = _audio(mid, "retrans.webm")
    r = _poster(client, mid, "", [{"filename": fichier, "position": 0}], "tok-sa-2")
    iid = int(r.headers["location"].rsplit("/", 1)[1])
    r2 = client.post(f"/interviews/{iid}/retranscrire", follow_redirects=False)
    assert r2.status_code in (200, 303), r2.text[:300]
    with SessionLocal() as db:
        assert db.query(AudioFileJob).filter(AudioFileJob.interview_id == iid).count() == 1


def test_texte_vide_sans_audio_reste_refuse():
    client = TestClient(app)
    mid = _mission(client)
    r = _poster(client, mid, "", [], "tok-sa-3")
    assert r.status_code == 200 and "Aucun texte transcrit." in r.text
    # un nom inexistant ne vaut pas audio sauvegardé
    r = _poster(client, mid, "", [{"filename": "999_absent.webm", "position": 0}], "tok-sa-4")
    assert "Aucun texte transcrit." in r.text
    with SessionLocal() as db:
        assert db.query(Interview).filter(Interview.mission_id == mid).count() == 0


def test_audio_incomplet_ne_refuse_plus_le_texte_vide():
    """Décision du 2026-10-19 (« Go applique ») : un audio TRONQUÉ ne refuse plus
    l'enregistrement ; l'entretien est créé `a_transcrire` avec `tranches_manquantes`
    posé et un avertissement visible (jamais de perte du texte ni de l'audio)."""
    client = TestClient(app)
    mid = _mission(client)
    f1 = _audio(mid, "inc1.webm")
    # tranche 0 manquante (seule la 1 est sur disque) -> créé a_transcrire, 1 manquante
    r = _poster(client, mid, "", [{"filename": f1, "position": 1}], "tok-sa-5", audio_attendu="2")
    assert r.status_code == 303
    # le client annonce 3 tranches, une seule est arrivée -> 2 manquantes
    r = _poster(client, mid, "", [{"filename": f1, "position": 0}], "tok-sa-6", audio_attendu="3")
    assert r.status_code == 303
    with SessionLocal() as db:
        ivs = db.query(Interview).filter(Interview.mission_id == mid).order_by(Interview.id).all()
        assert [i.structuration_status for i in ivs] == ["a_transcrire", "a_transcrire"]
        assert [i.tranches_manquantes for i in ivs] == [1, 2]


def test_transcription_incomplete_avec_audio_tronque_est_creee_a_transcrire(monkeypatch):
    monkeypatch.setattr("app.routers.interviews_libre.planifier_structuration", lambda i: None)
    client = TestClient(app)
    mid = _mission(client)
    f1 = _audio(mid, "tronque1.webm")
    r = _poster(client, mid, "Texte partiel.", [{"filename": f1, "position": 1}], "tok-sa-11",
                audio_attendu="2", transcription_incomplete="1")
    assert r.status_code == 303
    # sans aucun audio, le texte partiel est structuré (a_traiter) : rien ne bloque
    r = _poster(client, mid, "Texte partiel.", [], "tok-sa-12", transcription_incomplete="1")
    assert r.status_code == 303
    with SessionLocal() as db:
        ivs = db.query(Interview).filter(Interview.mission_id == mid).order_by(Interview.id).all()
        assert [i.structuration_status for i in ivs] == ["a_transcrire", "a_traiter"]
        assert all(i.raw_transcript == "Texte partiel." for i in ivs)


def test_audio_contigu_complet_accepte():
    client = TestClient(app)
    mid = _mission(client)
    segs = [{"filename": _audio(mid, f"c{i}.webm"), "position": i} for i in range(3)]
    r = _poster(client, mid, "", segs, "tok-sa-7", audio_attendu="3")
    assert r.status_code == 303


def test_transcription_incomplete_ne_lance_pas_la_structuration(monkeypatch):
    appels = []
    monkeypatch.setattr("app.routers.interviews_libre.planifier_structuration",
                        lambda i: appels.append(i))
    client = TestClient(app)
    mid = _mission(client)
    f = _audio(mid, "partiel.webm")
    r = _poster(client, mid, "Début seulement.", [{"filename": f, "position": 0}], "tok-sa-8",
                audio_attendu="1", transcription_incomplete="1")
    assert r.status_code == 303
    with SessionLocal() as db:
        it = db.query(Interview).filter(Interview.mission_id == mid).one()
        assert it.structuration_status == "a_transcrire" and it.raw_transcript
    assert appels == []
    page = client.get(r.headers["location"]).text
    assert "Transcription incomplète" in page and "Structurer maintenant" not in page


def test_texte_complet_lance_la_structuration_au_fond(monkeypatch):
    appels = []
    monkeypatch.setattr("app.routers.interviews_libre.planifier_structuration",
                        lambda i: appels.append(i))
    client = TestClient(app)
    mid = _mission(client)
    r = _poster(client, mid, "Texte complet.", [], "tok-sa-9")
    assert r.status_code == 303 and len(appels) == 1


def test_entretien_a_transcrire_hors_synthese_et_revient_a_fait_apres_la_reprise():
    client = TestClient(app)
    mid = _mission(client)
    f = _audio(mid, "reprise.webm")
    r = _poster(client, mid, "", [{"filename": f, "position": 0}], "tok-sa-10", audio_attendu="1")
    iid = int(r.headers["location"].rsplit("/", 1)[1])
    assert "transcription à relancer" in client.get(f"/missions/{mid}").text
    r2 = client.post(f"/interviews/{iid}/retranscrire/confirmer", data={
        "transcript": "Texte retrouvé.", "turn_interlocuteur": ["A"], "turn_question": ["Q ?"],
        "turn_remarque": ["R"], "turn_section_title": [""]}, follow_redirects=False)
    assert r2.status_code == 303, r2.text[:300]
    with SessionLocal() as db:
        assert db.get(Interview, iid).structuration_status == "fait"


# ---------------------------------------------------------------- N3
def test_nouvel_entretien_present_sur_la_fiche_avec_texte():
    client = TestClient(app)
    mid = _mission(client)
    with SessionLocal() as db:
        it = Interview(mission_id=mid, mode="libre", status="done",
                       interviewee_name="T", raw_transcript="Bonjour.",
                       structuration_status="fait")
        db.add(it)
        db.commit()
        iid = it.id
    page = client.get(f"/interviews/{iid}").text
    assert f'href="/missions/{mid}/interviews/record-libre"' in page
    assert "effacerSiEnvoye(" in page


def _node_draft(corps: str) -> str:
    script = ("var s={};global.window={localStorage:{getItem:function(k){return k in s?s[k]:null},"
              "setItem:function(k,v){s[k]=String(v)},removeItem:function(k){delete s[k]}}};"
              "var d=require(%s);%s" % (json.dumps(str(REC_DRAFT)), corps))
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr[:1500]
    return res.stdout.strip()


def test_purge_du_brouillon_seulement_s_il_est_envoye_et_meme_jeton():
    sortie = _node_draft(
        "d.sauver(7,{transcript:'a'});"
        "var a=d.effacerSiEnvoye(7,'tok'), restant=!!d.charger(7);"   # non envoyé : intact
        "d.marquerEnvoye(7,undefined,'tok');"
        "var autre=d.effacerSiEnvoye(7,'ancien'), toujours=!!d.charger(7);"  # autre entretien : intact
        "var vide=d.effacerSiEnvoye(7,''), toujours2=!!d.charger(7);"
        "var b=d.effacerSiEnvoye(7,'tok'), apres=!!d.charger(7);"     # même jeton : purgé
        "console.log(JSON.stringify([a,restant,autre,toujours,vide,toujours2,b,apres]));"
    )
    assert json.loads(sortie) == [False, True, False, True, False, True, True, False]


def test_le_clic_garantit_un_jeton_et_ferme_le_bouton():
    src = LIBRE.read_text(encoding="utf-8")
    i = src.index("getElementById('record-form').addEventListener('submit'")
    bloc = src[i:]
    assert "newSessionToken()" in bloc[:2500]
    assert "submitBtn.disabled = true" in bloc[:3500]
    assert "audio_attendu" in bloc[:2600] and "transcription_incomplete" in bloc[:2600]


def test_purge_epargne_les_tranches_d_un_entretien_a_transcrire():
    """Revue de contrôle 2026-10-19 : le travail partiel des tranches d'un entretien
    `a_transcrire` ne doit pas être purgé au bout de 7 jours."""
    from datetime import datetime, timedelta

    from app.models import InterviewSegmentJob
    from app.services.interview_segment_jobs import purge_stale_segment_jobs

    vieux = datetime.now() - timedelta(days=30)
    with SessionLocal() as db:
        m = Mission(name="Purge a_transcrire")
        db.add(m)
        db.commit()
        for jeton, statut in (("tok-atr", "a_transcrire"), ("tok-fait2", "fait")):
            db.add(Interview(mission_id=m.id, mode="libre", status="done",
                             interviewee_name="Sans nom", raw_transcript="",
                             segment_token=jeton, structuration_status=statut))
            db.add(InterviewSegmentJob(session_token=jeton, position=0, status="done",
                                       text="t", created_at=vieux))
        db.commit()
        purge_stale_segment_jobs(db)
        restants = {j.session_token for j in db.query(InterviewSegmentJob).all()}
    assert "tok-atr" in restants
    assert "tok-fait2" not in restants
