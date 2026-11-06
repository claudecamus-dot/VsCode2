"""UserPromptSubmit (TARGET variant, story 14.12): `ADOPTE GMAH` typed in a target project turns
the GMAH learning mode on for THAT project only. Exported by the kit; OFF until the user types it.

The hub hook `provenance_gmah.py` stays hub-only. This variant differs on purpose:
- key and signed state are LOCAL to the target: the key lives in
  <APPDATA or GMAH_KEY_DIR>/claude-gmah-<project slug>/jeton.key (decided by gravite.cle_hmac_chemin,
  absolute, outside .claude, no home fallback); the state is .claude/orchestration/gravite/etat.json;
- SEAL (drift protection): before anything is written, engine and policy files are compared with
  the sha256 list shipped by the installer (`.claude/orchestration/gmah_sceau.json`, same hashes as
  the hub's MANIFESTE.json, CRLF-insensitive). Any difference, missing file or missing seal ->
  refuse. If env GMAH_HUB_MANIFESTE points at the hub's published MANIFESTE.json, the seal is also
  compared with it (the hub stays master). HONEST LIMIT: this detects accidental or unreviewed
  drift; it does NOT stop an adversary who controls the kit (seal, hook and engine together) or an
  agent with unrestricted Bash under the same account. The real boundary stays the permission layer
  and human approval;
- the target learns only from its own journals (every path is relative to this project).
Reused from 14.10: real UserPromptSubmit payload with an existing transcript_path, local day ==
UTC day, arbitration written by the target's own `.claude/supervision/log_arbitrage.py` BEFORE the
token, `gravite.main(["on", ...])` in-process so every gravite check runs. Fail-open for the prompt
(exit 0, nothing printed), fail-closed for the mode. Reversible: `gravite.py off`, or delete the
exported files.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import importlib.util
import io
import json
import os
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ICI = Path(__file__).resolve().parent
CLAUDE = ICI.parent
RACINE = CLAUDE.parent
ORCH = CLAUDE / "orchestration"
JETONS = ORCH / "gravite" / "jetons.jsonl"
SCEAU = ORCH / "gmah_sceau.json"
DECLENCHEURS = {"ADOPTE GMAH": "adopte", "ADOPTE GMAH AUGMENTE": "adopte-augmente",
                "STOP GMAH": "stop"}
OBLIGATOIRES = (".claude/orchestration/gravite.py", ".claude/orchestration/neural_policy.yaml")

sys.path.insert(0, str(ORCH))  # story 14.17: _gmah_cle (sealed, shipped with the engine)
try:
    import _gmah_cle as _cle  # noqa: E402
except Exception:  # noqa: BLE001 - absent: the seal check refuses first, the prompt stays untouched
    _cle = None

try:
    sys.path.insert(0, str(ICI))
    from _stdin_borne import lier_stdin as _lier
    from _stdin_borne import lire_stdin_borne as _lsb
    _stdin_borne, _ecrire_journal_stdin, _journal_attente = _lier(globals(), __file__)
except Exception:  # noqa: BLE001 - helper absent: no stdin, hook stays silent
    _stdin_borne = None


def _stdin():
    try:
        return _stdin_borne(10.0) if _stdin_borne else _lsb(10.0)
    except Exception:  # noqa: BLE001
        return None


def empreinte(octets: bytes) -> str:
    """Same rule as export_agentic.empreinte_octets: CRLF -> LF for text, binary hashed as is."""
    if b"\0" not in octets:
        octets = octets.replace(b"\r\n", b"\n")
    return hashlib.sha256(octets).hexdigest()


def sceau_ok() -> bool:
    """True only if the seal exists, names the mandatory files and every sealed file matches."""
    try:
        sceau = json.loads(SCEAU.read_text(encoding="utf-8")).get("fichiers")
        if not isinstance(sceau, dict) or not all(o in sceau for o in OBLIGATOIRES):
            return False
        for dest, attendu in sceau.items():
            chemin = (RACINE / dest).resolve()
            if RACINE.resolve() not in chemin.parents or not isinstance(attendu, str):
                return False
            if empreinte(chemin.read_bytes()) != attendu:
                return False
        hub = os.environ.get("GMAH_HUB_MANIFESTE")
        if hub:  # the hub is master: its published hashes must equal the sealed ones
            pub = {f.get("destination"): f.get("sha256")
                   for f in json.loads(Path(hub).read_text(encoding="utf-8")).get("fichiers", [])}
            if any(pub.get(d) != h for d, h in sceau.items()):
                return False
        return True
    except Exception:  # noqa: BLE001 - fail closed
        return False


def _charger_gravite():
    spec = importlib.util.spec_from_file_location("gravite_cible", ORCH / "gravite.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cle_hmac(gr) -> bytes:
    """Read the target's HMAC key (path chosen by gravite), minting it O_EXCL on first use.
    On Windows a new key is DPAPI-protected, never written in clear (story 14.17, same module as
    the hub's: `_gmah_cle`). A legacy plaintext key is still READ here; `activer` migrates it
    first. Fail closed: an unusable blob raises OSError and nothing is minted."""
    p = gr.cle_hmac_chemin()
    if p is None:
        raise OSError("no safe key location")
    try:
        return _cle.lire(p)
    except FileNotFoundError:
        pass
    try:
        return _cle.creer(p)
    except FileExistsError:
        return _cle.lire(p)


def signature(cle: bytes, d: dict) -> str:
    msg = "|".join(str(d.get(k, "")) for k in ("ts", "type", "nonce", "session"))
    return hmac.new(cle, msg.encode("utf-8"), hashlib.sha256).hexdigest()


def type_jeton(prompt) -> str | None:
    return DECLENCHEURS.get(prompt.strip()) if isinstance(prompt, str) else None


def emettre(gr, kind: str, session: str = "") -> dict:
    ligne = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "type": kind,
             "nonce": secrets.token_hex(16), "session": session, "par": "provenance_gmah_cible"}
    ligne["sig"] = signature(cle_hmac(gr), ligne)
    JETONS.parent.mkdir(parents=True, exist_ok=True)
    with JETONS.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    return ligne


def _decision(jour: str, mode: str = "standard") -> str:
    phrase = "ADOPTE GMAH AUGMENTE" if mode == "augmente" else "ADOPTE GMAH"
    return f"ACCEPTE : activer-gravitationnel — {phrase} tapé par l'utilisateur le {jour}"


def _lire_liste(gr):
    try:
        with gr.ARBITRAGES.open("rb") as f:
            octets = f.read(gr.TAILLE_MAX_ARBITRAGES + 1)
    except FileNotFoundError:
        return []
    if len(octets) > gr.TAILLE_MAX_ARBITRAGES - 8192:
        return None
    brut = json.loads(octets.decode("utf-8"), object_pairs_hook=gr._sans_doublon)
    liste = brut.get("arbitrages") if isinstance(brut, dict) else None
    return liste if isinstance(liste, list) else None


def _cle_existante(liste, gr, jour: str, decision: str):
    derniere = None
    for e in liste:
        if isinstance(e, dict) and e.get("cible") == gr.CIBLE_ARBITRAGE:
            derniere = e
    if derniere and derniere.get("date") == jour and derniere.get("decision") == decision:
        return gr.cle_arbitrage(derniere)
    return None


def _jours():
    return (datetime.now().astimezone().strftime("%Y-%m-%d"), datetime.now(UTC).strftime("%Y-%m-%d"))


def _interactif(data) -> bool:
    if data.get("hook_event_name") != "UserPromptSubmit":
        return False
    tp = data.get("transcript_path")
    return isinstance(tp, str) and bool(tp) and os.path.isfile(tp)


def activer(gr=None, session: str = "", mode: str = "standard") -> bool:
    """Seal -> arbitration (idempotent) -> token -> `gravite on`. True only if the mode is on."""
    if not sceau_ok():
        return False  # drift or missing seal: nothing is written
    gr = gr or _charger_gravite()
    jour_local, jour_utc = _jours()
    if jour_local != jour_utc:
        return False
    decision = _decision(jour_local, mode)
    liste = _lire_liste(gr)
    if liste is None:
        return False
    try:  # story 14.17: the typed phrase is the human proof; wrap a legacy plaintext key in DPAPI
        kp = gr.cle_hmac_chemin()  # (this target's own key only, never the hub's)
        if kp is not None and _cle.est_clair(kp):
            _cle.migrer(kp)
    except OSError:
        return False  # fail closed for the mode: nothing minted, nothing written
    cle_hmac(gr)  # preflight: no key -> raises -> nothing written
    cle = _cle_existante(liste, gr, jour_local, decision)
    if cle is None:
        env = dict(os.environ, AGENT_SUPERVISION_ARBITRAGES=str(gr.ARBITRAGES),
                   AGENT_SUPERVISION_SKIP_SCAN="1")
        r = subprocess.run(
            [sys.executable, str(CLAUDE / "supervision" / "log_arbitrage.py"),
             "--cible", gr.CIBLE_ARBITRAGE, "--titre", "Mode gravitationnel",
             "--categories", "autre", "--decision", decision],
            env=env, capture_output=True, timeout=10, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            return False
        liste = _lire_liste(gr)
        if not liste:
            return False
        e = liste[-1]
        if not (isinstance(e, dict) and e.get("cible") == gr.CIBLE_ARBITRAGE
                and e.get("decision") == decision):
            return False
        cle = gr.cle_arbitrage(e)
    if not cle:
        return False
    jeton = "adopte-augmente" if mode == "augmente" else "adopte"
    emis = emettre(gr, jeton, session)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        ok = False
        try:
            ok = gr.main(["on", "--arbitrage", cle] + (["--mode", "augmente"] if mode == "augmente" else [])) == 0
        except BaseException:  # noqa: BLE001 - an engine that sys.exit()s or is interrupted is a failed `on`, not a hook crash
            ok = False
        finally:  # any exit of `on` must not leave a usable token
            if not ok:
                _revoquer(gr, "revoque:story-14.12", jeton, emis)
    return ok


def _revoquer(gr, cle_rev: str, jeton: str, emis) -> None:
    """Consume the minted token with gravite's own helper; neutralise it unless that PROVABLY worked.
    gravite.consommer_jeton catches OSError itself (lock busy, ACL, disk full) and RETURNS
    (False, reason): a refusal is as much a failure as a raise. A False for "already consumed" is
    harmless (the extra line only rebinds the nonce to a revocation key). A BaseException
    (KeyboardInterrupt) still neutralises, then propagates: the interruption is not swallowed."""
    neutraliser = True
    try:
        ok_r, _ = gr.consommer_jeton(cle_rev, type_jeton=jeton)
        neutraliser = ok_r is not True
    except Exception:  # noqa: BLE001 - story 14.11 (user decision 2026-11-04): fall back
        pass
    finally:
        if neutraliser:
            _neutraliser_jeton(gr, emis)


def _neutraliser_jeton(gr, emis) -> None:
    """Fallback when gravite's consommer_jeton raised or refused: bind the minted nonce to a
    revocation key by appending the same {"consomme": nonce, ...} line gravite writes, without its lock (the lock may
    be what failed). gravite then refuses that token for every real key ("bound to another key").
    Never raises: the prompt is never blocked. RESIDUAL RISK (honest): if the token log cannot be
    appended at all (disk full, ACL, file locked), the token stays consumable until its TTL
    (gravite.JETON_TTL_S, 15 min); `gravite.py on` still needs a valid arbitration for its key."""
    try:
        nonce = emis.get("nonce") if isinstance(emis, dict) else None
        if not isinstance(nonce, str):
            return
        try:
            chemin = Path(gr._jetons_chemin())  # the file gravite reads when it consumes
        except Exception:  # noqa: BLE001
            chemin = JETONS
        ligne = {"consomme": nonce, "cle": "revoque:story-14.11",
                 "ts": datetime.now(UTC).isoformat(timespec="seconds")}
        tete = ""
        try:  # a previous writer may have left a truncated last line: never glue onto it
            with chemin.open("rb") as f:
                if f.seek(0, 2) > 0:
                    f.seek(-1, 2)
                    tete = "" if f.read(1) == b"\n" else "\n"
        except OSError:
            pass
        with chemin.open("a", encoding="utf-8", newline="\n") as f:
            f.write(tete + json.dumps(ligne, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - fail-open for the prompt; residual risk documented above
        pass


def main():
    try:
        brut = _stdin()
        if isinstance(brut, bytes):
            brut = brut.decode("utf-8", "replace")
        if not brut:
            return 0
        data = json.loads(brut)
        if not isinstance(data, dict):
            return 0
        kind = type_jeton(data.get("prompt"))
        session = str(data.get("session_id") or "")
        if kind == "stop":
            emettre(_charger_gravite(), kind, session)
        elif kind in ("adopte", "adopte-augmente") and _interactif(data):
            activer(session=session, mode="augmente" if kind == "adopte-augmente" else "standard")
    except Exception:  # noqa: BLE001 - fail-open
        pass
    return 0


if __name__ == "__main__":
    try:
        main()
    finally:
        os._exit(0)
