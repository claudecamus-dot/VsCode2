"""Tests de l'étage 1 du superviseur d'agents (.claude/supervision/scan_transcripts.py).

Le script est exercé en subprocess avec des chemins surchargés par env — aucun accès aux
vrais transcripts ni au vrai wiki.
"""
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / ".claude" / "supervision" / "scan_transcripts.py"
WRITE_DIAG = Path(__file__).resolve().parents[1] / ".claude" / "supervision" / "write_diagnostic.py"


def _line(skill=None, subagent=None, ts="2026-07-17T10:00:00Z"):
    if skill:
        blk = {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": skill}}
    else:
        blk = {"type": "tool_use", "id": "t1", "name": "Task", "input": {"subagent_type": subagent}}
    return json.dumps({"timestamp": ts, "message": {"content": [blk]}}) + "\n"


def _env(tmp_path):
    return dict(
        os.environ,
        AGENT_SUPERVISION_TRANSCRIPTS=str(tmp_path / "transcripts"),
        AGENT_SUPERVISION_STATE=str(tmp_path / "state.json"),
        AGENT_SUPERVISION_WIKI_PAGE=str(tmp_path / "page.md"),
        AGENT_SUPERVISION_WIKI_INDEX=str(tmp_path / "index.md"),
        AGENT_SUPERVISION_WIKI_HTML=str(tmp_path / "wiki.html"),
        AGENT_SUPERVISION_RUNS=str(tmp_path / "runs.jsonl"),
        AGENT_SUPERVISION_ROUTING_HINTS=str(tmp_path / "routing-hints.json"),
        AGENT_SUPERVISION_DIAGNOSTIC=str(tmp_path / "diagnostic.json"),
        # Jamais la vraie base app ni les vrais arbitrages par défaut dans les tests.
        AGENT_SUPERVISION_OPENHUB_DB=str(tmp_path / "absent.db"),
        AGENT_SUPERVISION_ARBITRAGES=str(tmp_path / "arbitrages.json"),
    )


def _run(tmp_path, args=()):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=_env(tmp_path), capture_output=True, text=True, timeout=60,
    )


def test_scan_counts_and_generates_page_and_index(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s1.jsonl").write_text(
        _line(skill="run-dev-server") + _line(subagent="Explore"), encoding="utf-8"
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "+2 evenement" in result.stdout

    page = (tmp_path / "page.md").read_text(encoding="utf-8")
    assert "`run-dev-server` | projet | 1" in page
    assert "`Explore` | 1" in page
    assert "Ne pas éditer à la main" in page

    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "## TODO agents" in index
    assert "technical/agents-supervision.md" in index


def test_incremental_scan_only_reads_new_lines_and_is_idempotent(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    transcript = tdir / "s1.jsonl"
    transcript.write_text(_line(skill="run-dev-server"), encoding="utf-8")
    _run(tmp_path)

    # Rescan sans nouveauté : 0 événement, pas de double comptage.
    result = _run(tmp_path)
    assert "+0 evenement" in result.stdout

    # Ajout d'une invocation : le compteur cumule sans relire l'ancien.
    with transcript.open("a", encoding="utf-8") as fh:
        fh.write(_line(skill="run-dev-server", ts="2026-07-18T09:00:00Z"))
    result = _run(tmp_path)
    assert "+1 evenement" in result.stdout
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["skills"]["run-dev-server"]["n"] == 2
    assert state["skills"]["run-dev-server"]["last"].startswith("2026-07-18")

    # La section TODO de l'index est remplacée entre marqueurs, jamais dupliquée.
    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert index.count("## TODO agents") == 1


def test_wiki_html_block_replaced_between_markers_only(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s1.jsonl").write_text(_line(skill="run-dev-server"), encoding="utf-8")

    # Sans marqueurs : la page n'est pas touchée.
    html = tmp_path / "wiki.html"
    html.write_text("<html><body><p>page</p></body></html>", encoding="utf-8")
    result = _run(tmp_path)
    assert "sans marqueurs" in result.stdout
    assert html.read_text(encoding="utf-8") == "<html><body><p>page</p></body></html>"

    # Avec marqueurs : le bloc est injecté, le reste de la page intact, et c'est idempotent.
    html.write_text(
        "<html><body><p>avant</p>\n"
        "<!-- TODO-AGENTS-HTML:START -->placeholder<!-- TODO-AGENTS-HTML:END -->\n"
        "<p>après</p></body></html>",
        encoding="utf-8",
    )
    _run(tmp_path)
    txt = html.read_text(encoding="utf-8")
    assert "run-dev-server" in txt and "<p>avant</p>" in txt and "<p>après</p>" in txt
    assert "placeholder" not in txt
    _run(tmp_path)
    assert html.read_text(encoding="utf-8").count('id="agents-supervision"') == 1


def test_partial_trailing_line_is_not_consumed(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    complete = _line(skill="run-dev-server")
    partial = _line(skill="pptx-deck").rstrip("\n")  # sans \n final : ligne en cours d'écriture
    (tdir / "s1.jsonl").write_text(complete + partial, encoding="utf-8")
    _run(tmp_path)
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["skills"]["run-dev-server"]["n"] == 1
    assert "pptx-deck" not in state["skills"]

    # La ligne se termine : elle est comptée au scan suivant.
    with (tdir / "s1.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("\n")
    _run(tmp_path)
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["skills"]["pptx-deck"]["n"] == 1


# --- Incrément O-C : routing-hints.json (sens superviseur → orchestrateur) ---


def _run_line(**over):
    base = {
        "ts": "2026-07-17T18:00:00+02:00",
        "demande": "test",
        "qualification": "orchestre",
        "playbook": "dev-verifie",
        "plan": [{"etape": "impl", "agent": "general-purpose", "mode": "synchrone", "modele": "sonnet"}],
        "resultat": "succes",
        "reprises": 0,
        "notes": "",
    }
    base.update(over)
    return json.dumps(base) + "\n"


def test_routing_hints_croisent_usage_et_runs(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    # 3 invocations → « éprouvé » (PROVEN_MIN) ; revue-increment jamais invoquée.
    (tdir / "s1.jsonl").write_text(
        _line(skill="run-dev-server", ts="2026-07-15T10:00:00Z")
        + _line(skill="run-dev-server", ts="2026-07-16T10:00:00Z")
        + _line(skill="run-dev-server", ts="2026-07-17T10:00:00Z"),
        encoding="utf-8",
    )
    (tmp_path / "runs.jsonl").write_text(
        _run_line()
        + _run_line(resultat="echec", reprises=2, plan=[
            {"etape": "revue", "agent": "Explore", "mode": "parallele", "modele": "haiku"}
        ]),
        encoding="utf-8",
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "2 run(s) orchestrateur" in result.stdout
    assert "routing-hints.json a jour" in result.stdout

    hints = json.loads((tmp_path / "routing-hints.json").read_text(encoding="utf-8"))
    assert "run-dev-server" in hints["eprouves"]
    assert "revue-increment" in hints["jamais_utilises"]
    assert any("revue-increment" in v for v in hints["verifications_oubliees"])
    # Plan vs réel : stats par playbook et par agent héritées du résultat du run.
    assert hints["playbooks"]["dev-verifie"] == {"n": 2, "succes": 1, "echecs": 1, "reprises": 2}
    assert hints["agents"]["Explore"] == {"n": 1, "succes": 0, "echecs": 1, "reprises": 2}
    # Pas de diagnostic étage 2 : signalé comme à lancer.
    assert hints["diagnostic_a_jour"] is False
    assert "diagnostic agent-supervisor a lancer ou perime" in result.stdout


def test_diagnostic_etage2_fusionne_dans_page_et_hints(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s1.jsonl").write_text(_line(skill="run-dev-server"), encoding="utf-8")
    (tmp_path / "diagnostic.json").write_text(
        json.dumps({
            "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "findings": [
                {"categorie": "ko-repete", "cible": "pptx-verify", "priorite": 3,
                 "titre": "pptx-verify échoue sans LibreOffice",
                 "recommandation": "vérifier soffice avant de router"},
            ],
        }),
        encoding="utf-8",
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "a lancer ou perime" not in result.stdout

    page = (tmp_path / "page.md").read_text(encoding="utf-8")
    assert "pptx-verify échoue sans LibreOffice" in page
    assert "à jour" in page

    hints = json.loads((tmp_path / "routing-hints.json").read_text(encoding="utf-8"))
    assert hints["diagnostic_a_jour"] is True
    assert hints["prudence"] == [
        {"cible": "pptx-verify", "raison": "pptx-verify échoue sans LibreOffice"}
    ]


def test_diagnostic_perime_signale_a_relancer(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s1.jsonl").write_text(_line(skill="run-dev-server"), encoding="utf-8")
    (tmp_path / "diagnostic.json").write_text(
        json.dumps({
            "generated": "2026-01-01T10:00:00+01:00",
            "findings": [{"categorie": "inefficacite", "cible": "Explore", "priorite": 1,
                          "titre": "Fan-out trop large"}],
        }),
        encoding="utf-8",
    )
    result = _run(tmp_path)
    assert "a lancer ou perime" in result.stdout
    page = (tmp_path / "page.md").read_text(encoding="utf-8")
    assert "à relancer" in page
    hints = json.loads((tmp_path / "routing-hints.json").read_text(encoding="utf-8"))
    assert hints["diagnostic_a_jour"] is False


def test_diagnostic_arbitre_disparait_du_todo_et_de_la_prudence_mais_reste_mesure(tmp_path):
    """Un constat qualitatif (étage 2) dont la cible est arbitrée doit sortir du TODO et
    de `prudence` (routing-hints) — même contrat que les constats déterministes
    (`test_arbitrages_closent_les_todos_et_restent_affiches`), jusqu'ici non couvert :
    `diagnostic_todos()`/`build_routing_hints()` ne consultaient jamais arbitrages.json."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s1.jsonl").write_text(_line(skill="run-dev-server"), encoding="utf-8")
    (tmp_path / "diagnostic.json").write_text(
        json.dumps({
            "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "findings": [
                {"categorie": "ko-repete", "cible": "pptx-verify", "priorite": 3,
                 "titre": "pptx-verify échoue sans LibreOffice",
                 "recommandation": "vérifier soffice avant de router"},
            ],
        }),
        encoding="utf-8",
    )
    html = tmp_path / "wiki.html"
    html.write_text(
        "<!-- TODO-AGENTS-HTML:START -->x<!-- TODO-AGENTS-HTML:END -->", encoding="utf-8"
    )

    # Sans arbitrage : le constat s'affiche et pèse sur le routage (comportement existant).
    _run(tmp_path)
    page = (tmp_path / "page.md").read_text(encoding="utf-8")
    assert "pptx-verify échoue sans LibreOffice" in page
    hints = json.loads((tmp_path / "routing-hints.json").read_text(encoding="utf-8"))
    assert hints["prudence"] == [
        {"cible": "pptx-verify", "raison": "pptx-verify échoue sans LibreOffice"}
    ]

    # Avec arbitrage sur cette cible : le constat disparaît du TODO et de prudence,
    # mais le diagnostic reste "lancé" (pas "jamais lancé") et la décision reste visible.
    (tmp_path / "arbitrages.json").write_text(json.dumps({"arbitrages": [
        {"cible": "pptx-verify", "decision": "soffice installé sur le poste dev depuis le 2026-07-19",
         "date": "2026-07-19"},
    ]}, ensure_ascii=False), encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr

    page = (tmp_path / "page.md").read_text(encoding="utf-8")
    assert "pptx-verify échoue sans LibreOffice" not in page
    assert "Jamais lancé" not in page
    assert "rien à signaler, tous les constats précédents ont été arbitrés" in page
    assert "soffice installé sur le poste dev depuis le 2026-07-19" in page  # section Arbitrages

    html_txt = html.read_text(encoding="utf-8")
    assert "pptx-verify échoue sans LibreOffice" not in html_txt
    assert "Jamais lancé" not in html_txt
    assert "rien à signaler" in html_txt

    hints = json.loads((tmp_path / "routing-hints.json").read_text(encoding="utf-8"))
    assert hints["prudence"] == []
    assert hints["diagnostic_a_jour"] is True  # toujours à jour : l'arbitrage ne périme rien


# --- Étage 2 : écriture validée du diagnostic (write_diagnostic.py) ---


def _write_diag(tmp_path, payload):
    return subprocess.run(
        [sys.executable, str(WRITE_DIAG), json.dumps(payload, ensure_ascii=False)],
        env=dict(os.environ, AGENT_SUPERVISION_DIAGNOSTIC=str(tmp_path / "diagnostic.json")),
        capture_output=True, text=True, timeout=30, encoding="utf-8",
    )


def test_write_diagnostic_valide_et_horodate(tmp_path):
    out = _write_diag(tmp_path, {"findings": [
        {"categorie": "agent-mort", "titre": "46 skills BMAD sans usage",
         "preuve": "0 invocation depuis l'install du 2026-07-16", "priorite": 4},
    ]})
    assert out.returncode == 0, out.stderr
    diag = json.loads((tmp_path / "diagnostic.json").read_text(encoding="utf-8"))
    assert diag["generated"][:4] == "2026" and len(diag["findings"]) == 1

    # Le scan le consomme tel quel (boucle complète étage 1 ↔ étage 2).
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s1.jsonl").write_text(_line(skill="run-dev-server"), encoding="utf-8")
    _run(tmp_path)
    assert "46 skills BMAD sans usage" in (tmp_path / "page.md").read_text(encoding="utf-8")


def test_write_diagnostic_rejette_sans_preuve_ou_categorie_inconnue(tmp_path):
    sans_preuve = _write_diag(tmp_path, {"findings": [
        {"categorie": "inefficacite", "titre": "trop de fan-out"},
    ]})
    assert sans_preuve.returncode == 1 and "preuve" in sans_preuve.stdout
    mauvaise_cat = _write_diag(tmp_path, {"findings": [
        {"categorie": "ressenti", "titre": "t", "preuve": "p"},
    ]})
    assert mauvaise_cat.returncode == 1 and "categorie invalide" in mauvaise_cat.stdout
    assert not (tmp_path / "diagnostic.json").exists()


# --- Incrément C : challenge (propositions, prudence déterministe, trous, OpenHub) ---


def test_proposition_de_challenge_rendue_dans_la_page(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s1.jsonl").write_text(_line(skill="run-dev-server"), encoding="utf-8")
    assert _write_diag(tmp_path, {"findings": [
        {"categorie": "agent-mort", "titre": "slide-text-polish sans usage",
         "preuve": "0 invocation depuis 2026-07-15",
         "proposition": "élargir son déclencheur à toute revue de deck, sinon désinstaller"},
    ]}).returncode == 0
    _run(tmp_path)
    page = (tmp_path / "page.md").read_text(encoding="utf-8")
    assert "**Proposition** : élargir son déclencheur" in page


def test_prudence_deterministe_sur_echecs_repetes_et_trous_catalogue(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s1.jsonl").write_text(_line(skill="run-dev-server"), encoding="utf-8")
    echec = _run_line(resultat="echec", plan=[
        {"etape": "x", "agent": "bmad-quick-dev", "mode": "synchrone", "modele": "sonnet"}
    ])
    (tmp_path / "runs.jsonl").write_text(
        echec + echec
        + _run_line(notes="resolution: creation relecteur-pptx")
        + _run_line(notes="blocage puis resolution: creation relecteur-pptx"),
        encoding="utf-8",
    )
    _run(tmp_path)
    hints = json.loads((tmp_path / "routing-hints.json").read_text(encoding="utf-8"))
    assert {"cible": "bmad-quick-dev",
            "raison": "échecs répétés en orchestration (2/2 runs)"} in hints["prudence"]
    assert hints["trous_catalogue"] == [{"resolution": "creation", "nom": "relecteur-pptx", "n": 2}]
    # Trou récurrent (≥2) : remonté en TODO déterministe.
    assert "Trou récurrent du catalogue" in (tmp_path / "page.md").read_text(encoding="utf-8")


def test_diagnostic_perime_par_activite_meme_recent(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s1.jsonl").write_text(_line(skill="run-dev-server"), encoding="utf-8")
    assert _write_diag(tmp_path, {"findings": [
        {"categorie": "autre", "titre": "t", "preuve": "p"},
    ]}).returncode == 0
    # 3 runs postérieurs au diagnostic : périmé malgré une date récente.
    futur = "2099-01-01T00:00:00+00:00"
    (tmp_path / "runs.jsonl").write_text(3 * _run_line(ts=futur), encoding="utf-8")
    result = _run(tmp_path)
    assert "a lancer ou perime" in result.stdout
    hints = json.loads((tmp_path / "routing-hints.json").read_text(encoding="utf-8"))
    assert hints["diagnostic_a_jour"] is False


def test_couverture_openhub_optionnelle(tmp_path):
    import sqlite3

    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s1.jsonl").write_text(_line(skill="run-dev-server"), encoding="utf-8")
    # Sans base : aucune section, le scan ne bronche pas (déjà couvert par les tests amont).
    _run(tmp_path)
    assert "OpenHub" not in (tmp_path / "page.md").read_text(encoding="utf-8")
    # Avec une base agent_results : section rendue, réels vs simulés distingués.
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE agent_results (agent_label TEXT, runtime_available INT, created_at TEXT)")
    con.executemany("INSERT INTO agent_results VALUES (?, ?, ?)", [
        ("Auditeur", 0, "2026-07-10T10:00:00"),
        ("Auditeur", 1, "2026-07-12T10:00:00"),
    ])
    con.commit()
    con.close()
    env_db = {"AGENT_SUPERVISION_OPENHUB_DB": str(db)}
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        env={**_env(tmp_path), **env_db}, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    page = (tmp_path / "page.md").read_text(encoding="utf-8")
    assert "## Agents OpenHub (app)" in page
    assert "1 réel(s), 1 simulé(s)" in page and "`Auditeur` ×2" in page


# --- Arbitrages : boucle propose→arbitre bouclée (décisions humaines qui closent un TODO) ---


def test_arbitrages_closent_les_todos_et_restent_affiches(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "s1.jsonl").write_text(_line(skill="run-dev-server"), encoding="utf-8")

    # Sans arbitrage : les constats d'usage nagguent (skills BMAD réels du repo, 0 invocation).
    _run(tmp_path)
    page = (tmp_path / "page.md").read_text(encoding="utf-8")
    assert "Trier les skills BMAD" in page
    assert "Arbitrages enregistrés" not in page

    # Avec arbitrages : le TODO correspondant disparaît, la décision reste visible
    # (page + bloc HTML) et part dans routing-hints.json pour l'orchestrateur.
    (tmp_path / "arbitrages.json").write_text(json.dumps({"arbitrages": [
        {"cible": "famille:BMAD", "decision": "tri exécuté le 2026-07-18", "date": "2026-07-18"},
        {"cible": "pptx-framed-image", "decision": "conservée — playbook export-ppt-verifie", "date": "2026-07-18"},
        {"cible": "sans-decision"},  # entrée invalide : ignorée, jamais bloquante
    ]}, ensure_ascii=False), encoding="utf-8")
    html = tmp_path / "wiki.html"
    html.write_text(
        "<!-- TODO-AGENTS-HTML:START -->x<!-- TODO-AGENTS-HTML:END -->", encoding="utf-8"
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    page = (tmp_path / "page.md").read_text(encoding="utf-8")
    assert "Trier les skills BMAD" not in page
    assert "## Arbitrages enregistrés" in page
    assert "tri exécuté le 2026-07-18" in page
    # La skill arbitrée sort de la ligne TODO « Skills projet sans usage »…
    todo_projet = [l for l in page.splitlines() if "Skills projet sans usage" in l]
    assert all("pptx-framed-image" not in l for l in todo_projet)
    # …mais l'usage réel reste mesuré : toujours listée en « Jamais utilisés ».
    assert page.count("pptx-framed-image") >= 2  # section jamais-utilisés + section arbitrages
    assert "Arbitrages enregistrés" in html.read_text(encoding="utf-8")
    hints = json.loads((tmp_path / "routing-hints.json").read_text(encoding="utf-8"))
    assert [a["cible"] for a in hints["arbitrages"]] == ["famille:BMAD", "pptx-framed-image"]
    assert "pptx-framed-image" in hints["jamais_utilises"]
