# Product brief — Interview-to-Deck

_Porte d'entrée produit 1 page (créée le 2026-07-29, finding
`famille:cadrage-produit-apps` du hub de supervision, arbitré). Sources de
vérité : [`README.md`](../README.md) (périmètre couvert),
[`docs/wiki/index.md`](wiki/index.md) (architecture, conventions, stack),
[`.roadmap/roadmap.json`](../.roadmap/roadmap.json) (avancement). Ce brief
synthétise, il ne remplace pas ces sources._

## Persona

**Consultant·e menant des entretiens qualitatifs** en mission d'audit ou
d'étude, qui doit restituer à un client une synthèse fidèle de dizaines
d'entretiens — sous forme de deck PowerPoint sur le template corporate
(persona du README : « outil interne pour consultant·es menant des
entretiens qualitatifs en audit / étude »).

## Pourquoi (problème à résoudre)

Entre la capture des entretiens et le deck de restitution, la chaîne manuelle
coûte des jours : retranscription, structuration des tours de parole, synthèse
transverse multi-entretiens, remise en forme PPT — avec deux risques
permanents : **perdre les verbatims** (la matière probante) en route, et faire
transiter des **interviews confidentielles** par des services externes.

## Besoins et points de douleur

- **Capturer** sans friction : sur trame (Mission → Trame → Thème → Question),
  en entretien libre, ou par enregistrement audio transcrit localement
  (faster-whisper) puis structuré par IA — revu sur un seul écran.
- **Synthétiser** en transverse : 5 catégories fixes (contexte, culture & ADN,
  forces & succès, points d'amélioration, aspirations), mélange d'entretiens
  structurés et libres, recommandations scorées valeur/complexité, édition
  autosave.
- **Restituer** vite et juste : export PPT sur le template client (thème et
  masters hérités), garde-fou géométrique + vérification de rendu réel.
- Douleur clé adressée : pas de SPA ni d'infrastructure — un outil local
  (FastAPI + SQLite), utilisable sans clé API (mode démo ou chaîne 100 %
  locale `AI_PROVIDER=ollama`).

## Proposition de valeur

**La chaîne entretien → synthèse → deck en un seul outil, exécutable en
totalité sur le poste du consultant.** Le différenciateur n'est pas l'IA de
synthèse (banalisée) : c'est le **couplage bout-en-bout vérifié** — verbatims
préservés de la capture jusqu'au deck, export contrôlé par rendu réel, et
confidentialité par défaut (aucune donnée d'interview ne quitte le poste avec
le fournisseur local).

## Limites assumées (état 2026-07-29)

- Outil interne : pas d'authentification ni de multi-utilisateurs — un poste,
  une base (`data/app.db`).
- La qualité de la synthèse dépend du fournisseur IA configuré ; le mode démo
  (`SYNTHESE_DEMO=1`) sert à évaluer la chaîne, pas la qualité d'analyse.
