"""Hook UserPromptSubmit — grille de qualification de l'orchestrateur (étage O-A).

Injecte à chaque demande de travail un rappel court (~50 tokens) : qualifier
silencieusement, orchestrer si multi-étapes/multi-agents, sinon exécution directe.
Silencieux sur les commandes slash (l'utilisateur invoque déjà explicitement une skill).
Ne bloque jamais : toute erreur est avalée, exit 0.
Conception : `.claude/skills/agent-orchestrator/SKILL.md` (étape 1, qualification).

LE DÉNOMINATEUR QUI MANQUAIT (finding `VScode5:seuil-qualification-non-mesurable`,
option A, 2026-09-02). Le seuil « à partir de quand une demande mérite orchestration »
promettait depuis juillet une calibration sur données. Mesure du 2026-09-02 :
**106 runs, 106 `orchestre`, 0 `direct-signale`** — non par excès de zèle, mais par
construction, la méthode disant « les exécutions directes ne se journalisent pas ».
Le numérateur (`runs.jsonl`) existait, le dénominateur nulle part.

Ce hook voit CHAQUE prompt, orchestré ou non : il est le seul endroit du dispositif
où le dénominateur passe. Il en écrit une ligne par prompt dans un journal à part
(`.claude/orchestration/prompts.jsonl`), et **rien du texte du prompt** — un
horodatage, un booléen « commande slash », et la longueur en caractères. Deux raisons
de s'en tenir là : le contenu d'un prompt est du contenu client (règle de tokens du
hub, « ne jamais ouvrir en entier »), et la longueur suffit à situer une micro-tâche
face à un chantier. Le ratio se lit ensuite : lignes du journal (hors slash) contre
runs de `runs.jsonl` sur la même fenêtre.

Ce que ce journal NE fait pas : deviner à la place de l'humain. Il ne classe pas un
prompt en « aurait dû être orchestré » — c'est précisément ce qu'il faut instruire, pas
présumer. Il compte, et la règle « une exécution directe ne fait pas un run » reste
intacte.

Chemin dérivé de `__file__`, jamais du `cwd` (leçon `warn_verif_before_commit`, 2026-09-02 :
un hook qui résout ses fichiers depuis le répertoire courant surveille celui d'un autre).
Le journal est machine-local : append-only, une ligne courte, un `open` en ajout par
prompt. « Non versionné » n'est pas une intention mais une règle posée à deux endroits,
parce que la revue du 2026-09-02 a montré que le fichier n'était ignoré nulle part et
partait donc se faire committer dans les 5 dépôts : `.gitignore` du hub, et
`orchestration/prompts.jsonl` dans `_GITIGNORE_LIGNES` de l'installateur du kit. Le scan
l'ignore aussi dans `arbre_sale()`, sans quoi il serait signalé « reliquat de la séance
précédente » à chaque démarrage, pour toujours.
"""
import datetime as dt
import json
import os
import sys

GRID = (
    "[orchestrateur] Qualifier en silence : demande de travail multi-etapes ou "
    "multi-agents, ou verifications obligatoires en jeu -> suivre la skill "
    "agent-orchestrator (plan modes+modeles, journal log_run.py). Sinon executer "
    "directement sans mentionner cette grille. Catalogue : "
    ".claude/orchestration/catalogue.md"
)

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
JOURNAL = os.environ.get("AGENT_ORCHESTRATION_PROMPTS") or os.path.join(
    os.path.dirname(_HOOKS_DIR), "orchestration", "prompts.jsonl")


def journaliser(prompt: str, slash: bool) -> None:
    """Une ligne par prompt vu. Fail-open intégral : un journal qui ne peut pas
    s'écrire ne doit jamais coûter une demande à l'utilisateur."""
    try:
        os.makedirs(os.path.dirname(JOURNAL), exist_ok=True)
        ligne = {
            "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "slash": slash,
            "n_car": len(prompt),
        }
        with open(JOURNAL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    except OSError as exc:
        # Fail-open pour la demande (jamais bloquer un prompt), mais pas fail-
        # SILENCIEUX pour la mesure (chasse aux cas limites, 2026-09-02) :
        # `except: pass` rendait un journal perdu indiscernable d'un journal a
        # jour, dans 6 depots, pour toujours. Meme geste que log_usage.py
        # (corrige le 2026-09-01 pour le defaut symetrique).
        print(f"orchestrator_gate : journal illisible ({exc}) - ligne non "
              "ecrite, le ratio de qualification sous-comptera d'autant.",
              file=sys.stderr)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    brut = data.get("prompt") or ""
    slash = brut.lstrip().startswith("/")
    # `n_car` mesure le prompt BRUT, pas la version lstrip()ée qui servait à détecter
    # « /» : un espace de tête n'est pas une micro-tâche plus petite (revue 2026-09-02).
    journaliser(brut, slash)
    if slash:
        return 0
    print(GRID)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
