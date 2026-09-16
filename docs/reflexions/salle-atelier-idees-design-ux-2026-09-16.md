# Salle « Atelier d'idées » — design & UX du site, 2026-09-16

Convoquée sur demande explicite (« lance une salle pour revoir le design et
l'expérience utilisateur du site web »), mode subagent, 2 tours (positions
indépendantes puis confrontation). Voix : Cadreur, Portevoix, Wildcard
(`bmad-brainstorming`), Splinter (`bmad-advanced-elicitation` en round 2),
Sally (UX, invitée par la scène de la salle). Ne modifie aucun fichier — ce
document est le livrable de la salle elle-même (options + recommandation),
pas la revue.

## Question à trancher

**Quel périmètre et quelle méthode pour revoir le design/UX du site, et
faut-il en plus un garde-fou récurrent contre l'érosion silencieuse de
garde-fous déjà certifiés ?**

## Ce qui a été vérifié en séance (pas supposé)

- Une revue UX complète du site a eu lieu le **2026-07-23**
  (`docs/reflexions/revue-ux-2026-07-23.md`, 26 constats), **tous corrigés le
  jour même**. Le brief initial de l'orchestrateur affirmait à tort qu'aucune
  revue n'avait jamais eu lieu — erreur trouvée par Splinter, vérifiée.
- Depuis cette date, `record.html` (15 commits) et `record_libre.html`
  (17 commits) ont un churn très supérieur à tout le reste du site (1-3
  commits par écran ailleurs) — mesuré par `git log --since=2026-07-24
  --name-only`.
- Sur `record_libre.html`, 3 des 4 zones de statut visuellement identiques
  (`.muted`, `app.css:92`) — `rec-backup-status`, `rec-job-status`,
  `rec-draft-status` — sont nées le **2026-09-08** (`b6bba7d`), postérieures à
  la revue de juillet. Les 3 bandeaux d'alerte partageant le même rouge
  (`.ai-error`) datent du 2026-07-29/30. **Constat neuf, pas régression** de
  ce que juillet avait vu.
- Juillet avait explicitement certifié **« Garde-fous destructifs
  complets »** (toute suppression a un confirm nommant ce qui sera perdu)
  comme point fort à préserver. Deux jours plus tard (2026-07-25,
  `157a6a6`), le bouton « Recommencer » (`#rec-reset`) est arrivé sur
  `record.html`/`record_libre.html` **sans aucun `confirm()`** — il vide la
  transcription et révoque la sauvegarde audio en un clic, alors que
  « Démarrer » à trois lignes de là, sur une action moins destructrice, en a
  un. Vérifié indépendamment par Portevoix (lecture du handler) et Sally
  (numéros de ligne sur les deux fichiers), confirmé par Splinter (grep des
  `confirm()` du fichier). **C'est un défaut réel, pas un jugement
  esthétique.**

## Options en regard

| Option | Ce qu'on fait | Ce que ça coûte | Ce qu'on saurait | Quand |
| --- | --- | --- | --- | --- |
| **1 — Écran d'enregistrement, ciblé** | `design-reviewer` + `a11y-auditor` + `revue-ui-web` en parallèle sur un **rendu réel** (via `run-dev-server` : un entretien réellement en cours, les 4 statuts peuplés, un bandeau levé) des 3 fichiers `record.html` / `record_libre.html` / `record_segment_wait.html` (le 3e ajouté par Wildcard : même tunnel, 2 commits depuis juillet). Inclut le bug « Recommencer sans confirm ». | Modéré — 3 fichiers ciblés (~4800 lignes cumulées + l'écran d'attente), 3 agents/skills en parallèle, un bootstrap de mission réelle. | La table état→gravité→poids visuel du tunnel (quel statut doit « crier »), le sort du bug destructeur, un rapport scoré au format que Cadreur réclame (constat + écran + usager gêné + sévérité). | Immédiat — serveur déjà lancé sur :8020, aucune dépendance bloquante. |
| **2 — Option 1 + garde-fou récurrent** | En plus de l'option 1, un contrôle qui re-vérifie PÉRIODIQUEMENT les points forts certifiés de juillet (discipline chromatique, confirm sur toute action destructive) — un invariant testé, pas une nouvelle liste ponctuelle. | Plus élevé — implique un test de régression automatisé (ex. toute action listée « destructrice » doit avoir un confirm), pas seulement une revue. Change la nature de la demande initiale (ponctuelle → dispositif permanent). | Si l'érosion silencieuse d'un garde-fou certifié est un incident isolé ou un phénomène récurrent sur ce projet. | Après l'option 1 (dépend de savoir d'abord ce que « destructif » doit couvrir). |
| **F — Glossaire synthèse (quasi gratuit)** | Trancher UN mot (« transverse » ou « globale ») pour la même page, propager par `sed` sur les 6 fichiers mixtes recensés par Wildcard (`grep -ril`). | ~10 minutes, décision humaine + une commande — pas de producteur agent. | Rien à apprendre, une décision à prendre et exécuter. | Indépendant, immédiat. |

**Écartées explicitement** : planche contact des 44 templates (bruit sur 40
écrans qui n'ont pas bougé), révision du socle CSS comme option séparée
(c'est un livrable dérivé de l'option 1, pas un choix à part), le tunnel
`libre_*` entier en périmètre propre (sa part vocabulaire est fondue dans F,
le reste jugé hors-signal par les 5 voix).

## Recommandation nommée

**Option 1 + Option F en parallèle**, maintenant. L'option 2 est **nommée
mais pas recommandée en l'état** — je la remets à votre arbitrage séparément,
parce qu'elle change la nature de la demande (d'une revue ponctuelle à un
dispositif permanent) et que son coût est significativement plus élevé ;
Splinter la défend, les 4 autres voix ne l'ont pas contestée mais ne l'ont
pas non plus intégrée à leur convergence.

Producteur si vous validez l'option 1 : `run-dev-server` (bootstrap d'un
entretien réel en cours) puis `design-reviewer` + `a11y-auditor` +
`revue-ui-web` en parallèle sur ce rendu — **pas** le playbook
`revue-design-parallele` (4 de ses 5 runs ont servi de véhicule générique de
revue de code, pas de revue design fraîche).

## Désaccord(s) documenté(s)

- **Périmètre-lieu (record.html/record_libre.html/record_segment_wait.html)
  : aucun désaccord.** Convergence réelle et indépendante — Wildcard (option
  C, avant de voir les autres), Sally (lecture du CSS), Splinter (churn git)
  sont arrivés au même endroit par trois chemins différents, vérifiés
  ensuite les uns par les autres.
- **Le LIVRABLE doit-il s'arrêter à une revue ponctuelle (option 1) ou
  inclure un garde-fou récurrent (option 2) : désaccord non résolu.**
  Splinter le pose explicitement et le remet à votre tranchage (« à l'humain
  de trancher : option C seule ou C+E ») plutôt que de forcer un consensus.
- **Le sort des constats de Portevoix hors du périmètre resserré** (2
  points : vocabulaire « Synthèse transverse » vs « Synthèse globale »
  incohérent sur 6 fichiers ; les deux boutons « Exporter en PPT » identiques
  — ce dernier est la mise en œuvre littérale du constat #17 de juillet
  « dupliquer/sticky », dont la duplication non différenciée est ce qui
  gêne maintenant). Portevoix a cédé le périmètre à cette condition
  explicite : que ces deux points soient **écrits, datés**, pas dissous.
  Cadreur a accepté la condition. Traités ci-dessous, pas dans la salle.

## Reste à tracer (condition de Portevoix, tenue)

| id | sév. | titre | fichier:ligne | preuve | statut |
| --- | --- | --- | --- | --- | --- |
| SALLE1-F1 | mineur | « Synthèse transverse » / « Synthèse globale » désignent la même chose sur 6 fichiers mixtes (`libre_analyse`, `libre_detail`, `libre_regen_review`, `apercu`, `globale`, `recommandations`) | voir `grep -ril` de Wildcard | constat round 1/2 (Portevoix, Wildcard) | ouvert |
| SALLE1-F2 | mineur | Deux boutons « Exporter en PPT » identiques sans différenciation — mise en œuvre du constat #17 de juillet (« dupliquer/sticky »), mais la duplication non nommée est ce qui gêne maintenant | `synthese/apercu.html:97,560` | constat round 1 (Portevoix), confirmé round 2 (Splinter, réf. constat #17) | differe |

Ces deux lignes sont hors du périmètre recommandé (option 1) — à reprendre
dans un tour séparé (candidat naturel : exécuté en même temps que l'option F
ci-dessus pour F1 ; F2 est une décision de wording, pas une revue).
