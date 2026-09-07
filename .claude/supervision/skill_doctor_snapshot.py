"""Capture un instantane /skill-doctor a la demande (jamais automatique).

/skill-doctor est une commande CLI native (>= v2.1.252) qui rend, pour les skills
chargees dans le prompt systeme du contexte courant, leur source, leur cout de
listing par tour et leur cout reel en tokens sur les 7 derniers jours -- une donnee
que le dispositif ne mesure nulle part ailleurs (usage.jsonl/dormants() ne comptent
que la PRESENCE d'une invocation, jamais son cout).

Un sous-agent lance via l'outil Agent ne peut PAS invoquer /skill-doctor (commande
interactive/print uniquement). Ce script contourne la limite en lancant un
sous-processus CLI top-level independant (`claude -p "/skill-doctor"`), depuis la
racine du projet pour que le rapport porte sur les skills REELLEMENT charges ici, pas
une session vide. C'est un vrai appel facture : a lancer a la demande (agent-supervisor
peut l'invoquer lui-meme, il a Bash), jamais en tache de fond automatique.
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SORTIE = ROOT / ".claude" / "supervision" / "skill_doctor_last.txt"
TIMEOUT_S = 90


def capturer(timeout_s: int = TIMEOUT_S) -> tuple[bool, str]:
    """Lance `claude -p "/skill-doctor"` et rend (succes, texte_ou_erreur).

    Resout l'executable via shutil.which : sur Windows, `claude` est un shim
    `.cmd` (npm global) que subprocess.run(["claude", ...]) sans shell=True ne
    trouve pas de maniere fiable (pas de resolution PATHEXT automatique).
    """
    claude_bin = shutil.which("claude")
    if claude_bin is None:
        return False, "claude introuvable sur le PATH -- CLI non installee ou pas a jour"

    try:
        resultat = subprocess.run(
            [claude_bin, "-p", "/skill-doctor", "--output-format", "text"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_s,
        )
    except FileNotFoundError:
        return False, "claude introuvable sur le PATH -- CLI non installee ou pas a jour"
    except subprocess.TimeoutExpired:
        return False, f"claude -p /skill-doctor n'a pas repondu sous {timeout_s}s"

    sortie = (resultat.stdout or "").strip()
    erreur = (resultat.stderr or "").strip()
    if resultat.returncode != 0 or not sortie:
        detail = erreur or sortie or f"code de sortie {resultat.returncode}"
        return False, f"claude -p /skill-doctor a echoue : {detail}"
    return True, sortie


def ecrire_snapshot(texte: str) -> None:
    horodatage = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    entete = (
        f"# Instantane /skill-doctor -- {horodatage}\n"
        f"# Capture a la demande (script skill_doctor_snapshot.py), pas de cadence automatique.\n"
        f"# Perime au-dela de quelques jours : le cout 7j qu'il rapporte glisse avec le temps.\n\n"
    )
    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(entete + texte + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout", type=int, default=TIMEOUT_S,
        help=f"secondes avant abandon (defaut {TIMEOUT_S})",
    )
    args = parser.parse_args(argv)

    ok, texte = capturer(args.timeout)
    if not ok:
        print(f"skill_doctor_snapshot : ECHEC -- {texte}", file=sys.stderr)
        return 1

    ecrire_snapshot(texte)
    print(f"skill_doctor_snapshot : instantane ecrit -> {SORTIE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
