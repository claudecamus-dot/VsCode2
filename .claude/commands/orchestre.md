---
description: Orchestre une demande de travail — qualifie, compose le plan (modes + modèles), exécute avec ses vérifications obligatoires, journalise
argument-hint: <demande de travail, ou « applique le finding X », ou « adopte <trouvaille> »>
---

Invoque la skill `agent-orchestrator` (outil `Skill`) et applique sa méthode en 5 étapes à
la demande ci-dessous. Cette commande est l'appel **explicite** de l'orchestrateur : elle
vaut mandat d'orchestrer, il n'y a donc pas à requalifier en « exécution directe » pour
gagner du temps.

Rappels que la commande ne dispense pas de lire dans la skill :

- chercher un **playbook** de `.claude/orchestration/playbooks/` avant de composer à vide ;
- insérer d'office les `verifications_oubliees` de `.claude/orchestration/routing-hints.json` ;
- **R4** — si la demande implique d'appliquer une reco du superviseur ou d'adopter une
  trouvaille de veille non encore arbitrée, présenter et attendre l'arbitrage ;
- **journaliser** le run en fin de course avec `py .claude/orchestration/log_run.py` —
  **uniquement si le livrable est une correction ou un bug traité** (règle du 2026-09-07 :
  un état des lieux, une propagation, une réception de diagnostic ou une reprise de travaux
  ne sont pas des runs). Avec un `resultat` discriminant (`en-attente-validation` tant que
  l'utilisateur n'a pas validé un livrable qui lui est destiné) et, si le run ferme un
  finding, l'arbitrage de clôture citant le commit — sans lui, le compteur ne bouge pas.

Demande à orchestrer :

$ARGUMENTS
